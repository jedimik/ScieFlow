"""Antigravity CLI (`agy`): `~/.gemini/antigravity-cli`.

Each conversation is its own SQLite file whose `steps.step_payload` is
protobuf. The bodies copy and restore intact, but nothing inside them can be
rewritten — only the summary row, `settings.json` and `history.jsonl` carry
remappable paths. Skill detection here is a byte scan, marked low confidence.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..model import ChatRef, CopySpec
from ..remap import FileChange, remap_obj, remap_string
from .base import StoreAdapter, is_excluded, parse_iso, register, snapshot_sqlite

SUMMARY_DB = "conversation_summaries.db"
SUMMARY_TABLE = "conversation_summaries"
#: Bytes of protobuf payload read per conversation when scanning for skills.
SCAN_BUDGET = 2_000_000


@register
class AntigravityStore(StoreAdapter):
    tool = "agy"

    @property
    def conversations_dir(self) -> Path:
        return self.root / "conversations"

    def skip(self, path: Path) -> bool:
        if self.options.get("with_brain"):
            try:
                if path.relative_to(self.root).parts[:1] == ("brain",):
                    return False
            except ValueError:
                pass
        return is_excluded(self.tool, path, self.root, self.exclude_extra)

    # -- read ----------------------------------------------------------
    def _summaries(self) -> dict[str, dict]:
        path = self.root / SUMMARY_DB
        if not path.exists():
            return {}
        snap = snapshot_sqlite(path)
        conn = sqlite3.connect(snap)
        try:
            cur = conn.execute(f"SELECT * FROM {SUMMARY_TABLE}")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
            snap.unlink(missing_ok=True)
        return {r["conversation_id"]: r for r in rows if r.get("conversation_id")}

    @staticmethod
    def _workspace(row: dict) -> str | None:
        raw = row.get("workspace_uris")
        if not raw:
            return None
        try:
            uris = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return None
        if not isinstance(uris, list) or not uris:
            return None
        return str(uris[0]).removeprefix("file://")

    def discover(self) -> list[ChatRef]:
        if not self.conversations_dir.is_dir():
            return []
        summaries = self._summaries()
        refs: list[ChatRef] = []
        for db in sorted(self.conversations_dir.glob("*.db")):
            if self.skip(db):
                continue
            chat_id = db.stem
            row = summaries.get(chat_id, {})
            files = [db]
            brain = self.root / "brain" / chat_id
            if brain.is_dir():
                files.extend(p for p in brain.rglob("*") if p.is_file() and not self.skip(p))
            title = (row.get("title") or row.get("preview") or "").strip().splitlines()
            refs.append(
                ChatRef(
                    tool=self.tool,
                    chat_id=chat_id,
                    project_path=self._workspace(row),
                    title=(title[0][:120] if title else "(no summary)"),
                    started=parse_iso(str(row.get("last_user_input_time") or "") or None),
                    updated=parse_iso(str(row.get("last_modified_time") or "") or None),
                    size_bytes=sum(p.stat().st_size for p in files),
                    message_count=int(row.get("step_count") or 0),
                    files=tuple(files),
                    extras={"summary": row, "has_summary": bool(row)},
                )
            )
        return refs

    def collect(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs: list[CopySpec] = []
        for ref in refs:
            for path in ref.files:
                if self.skip(path):
                    continue
                specs.append(
                    CopySpec(
                        member=f"chats/{self.tool}/{path.relative_to(self.root).as_posix()}",
                        source=path,
                    )
                )
        return specs

    def sidecars(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs: list[CopySpec] = []
        rows = [r.extras.get("summary") for r in refs if r.extras.get("summary")]
        if rows:
            specs.append(
                CopySpec(
                    member=f"sidecars/{self.tool}/conversation_summaries.rows.json",
                    data=json.dumps(rows, indent=1, default=str).encode(),
                )
            )
        history = self.root / "history.jsonl"
        if history.exists():
            wanted = {r.project_path for r in refs if r.project_path}
            kept = []
            with history.open("rb") as fh:
                for raw in fh:
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if row.get("workspace") in wanted:
                        kept.append(raw.strip())
            if kept:
                specs.append(
                    CopySpec(
                        member=f"sidecars/{self.tool}/history.jsonl",
                        data=b"\n".join(kept) + b"\n",
                    )
                )
        settings = self.root / "settings.json"
        if settings.exists():
            specs.append(CopySpec(member=f"sidecars/{self.tool}/settings.json", source=settings))
        return specs

    def transcript_texts(self, ref: ChatRef):
        """Protobuf payloads, decoded loosely. Only good for substring hunting."""
        db = self.conversations_dir / f"{ref.chat_id}.db"
        if not db.exists():
            return
        snap = snapshot_sqlite(db)
        conn = sqlite3.connect(snap)
        try:
            budget = SCAN_BUDGET
            for (payload,) in conn.execute("SELECT step_payload FROM steps ORDER BY idx"):
                if not payload or budget <= 0:
                    continue
                chunk = bytes(payload)[:budget]
                budget -= len(chunk)
                yield chunk.decode("utf-8", errors="ignore")
        except sqlite3.Error:
            return
        finally:
            conn.close()
            snap.unlink(missing_ok=True)

    # -- write ---------------------------------------------------------
    def plan_restore(
        self, bundle_dir: Path, dest_root: Path, mapping: dict[str, str]
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        chats_dir = bundle_dir / "chats" / self.tool
        if chats_dir.is_dir():
            for path in sorted(p for p in chats_dir.rglob("*") if p.is_file()):
                target = dest_root / path.relative_to(chats_dir)
                changes.append(
                    FileChange(
                        target,
                        "skip-exists" if target.exists() else "create",
                        "protobuf body copied verbatim" if target.suffix == ".db" else "",
                        source=path,
                    )
                )
        side = bundle_dir / "sidecars" / self.tool
        rows_file = side / "conversation_summaries.rows.json"
        if rows_file.exists():
            try:
                rows = json.loads(rows_file.read_text())
            except (json.JSONDecodeError, OSError):
                rows = []
            rows = [_remap_summary(row, mapping) for row in rows]
            changes.append(
                FileChange(
                    dest_root / SUMMARY_DB,
                    "sqlite-upsert",
                    f"{len(rows)} conversation summaries",
                    rows=[(SUMMARY_TABLE, rows)],
                )
            )
        history = side / "history.jsonl"
        if history.exists():
            target = dest_root / "history.jsonl"
            lines = []
            for raw in history.read_bytes().splitlines():
                try:
                    row = remap_obj(json.loads(raw), mapping)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                lines.append(json.dumps(row, ensure_ascii=False).encode())
            current = target.read_bytes() if target.exists() else b""
            changes.append(
                FileChange(
                    target,
                    "merge",
                    f"{len(lines)} history line(s) appended",
                    data=current + b"\n".join(lines) + b"\n",
                )
            )
        return changes


def _remap_summary(row: dict, mapping: dict[str, str]) -> dict:
    out = dict(row)
    raw = out.get("workspace_uris")
    if isinstance(raw, str):
        try:
            uris = json.loads(raw)
        except json.JSONDecodeError:
            uris = None
        if isinstance(uris, list):
            out["workspace_uris"] = json.dumps(
                [remap_string(str(u), mapping) for u in uris]
            )
    if isinstance(out.get("app_data_dir"), str):
        out["app_data_dir"] = remap_string(out["app_data_dir"], mapping)
    return out
