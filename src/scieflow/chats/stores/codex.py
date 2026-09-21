"""Codex CLI: `~/.codex`.

A chat is a rollout file plus rows in two SQLite databases. Both have live
`-wal` companions, so every read goes through a `.backup()` snapshot and the
whole 360M/33M databases are never bundled — only the selected threads' rows.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..model import ChatRef, CopySpec
from ..remap import FileChange, remap_obj, remap_string
from .base import StoreAdapter, StoreError, register, snapshot_sqlite, ts

STATE_DB = "state_5.sqlite"
HISTORY_DB = "thread_history_1.sqlite"

#: thread-scoped tables, per database, keyed by the column holding the thread id.
STATE_TABLES: dict[str, str] = {
    "threads": "id",
    "thread_dynamic_tools": "thread_id",
    "thread_attachments": "thread_id",
}
HISTORY_TABLES: dict[str, str] = {
    "thread_turns": "thread_id",
    "thread_items": "thread_id",
    "thread_realtime_items": "thread_id",
    "thread_history_projection_state": "thread_id",
}

_PROJECT_HEADER = re.compile(r'^\s*\[projects\.\"(?P<path>[^\"]+)\"\]')


def _rows(conn: sqlite3.Connection, table: str, column: str, ids: list[str]) -> list[dict]:
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(f"SELECT * FROM {table} WHERE {column} IN ({marks})", ids)
    except sqlite3.Error:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@register
class CodexStore(StoreAdapter):
    tool = "codex"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def _open(self, name: str) -> tuple[sqlite3.Connection, Path] | None:
        path = self.root / name
        if not path.exists():
            return None
        snap = snapshot_sqlite(path)
        return sqlite3.connect(snap), snap

    # -- read ----------------------------------------------------------
    def discover(self) -> list[ChatRef]:
        opened = self._open(STATE_DB)
        if opened is None:
            return self._discover_from_files()
        conn, snap = opened
        try:
            counts = self._item_counts()
            refs = []
            cur = conn.execute(
                "SELECT id, rollout_path, cwd, title, preview, first_user_message,"
                " created_at_ms, updated_at_ms, created_at, updated_at, archived"
                " FROM threads"
            )
            for row in cur.fetchall():
                (tid, rollout, cwd, title, preview, first_msg,
                 cms, ums, csec, usec, archived) = row
                path = Path(rollout) if rollout else None
                if path is not None and not path.is_absolute():
                    path = self.root / path
                files = [path] if path and path.exists() and not self.skip(path) else []
                label = (title or preview or first_msg or "").strip().splitlines()
                refs.append(
                    ChatRef(
                        tool=self.tool,
                        chat_id=tid,
                        project_path=cwd,
                        title=(label[0][:120] if label else "(untitled)"),
                        started=ts(cms or csec),
                        updated=ts(ums or usec),
                        size_bytes=sum(p.stat().st_size for p in files),
                        message_count=counts.get(tid, 0),
                        files=tuple(files),
                        extras={"rollout_path": rollout, "archived": bool(archived)},
                    )
                )
            return refs
        finally:
            conn.close()
            snap.unlink(missing_ok=True)

    def _item_counts(self) -> dict[str, int]:
        opened = self._open(HISTORY_DB)
        if opened is None:
            return {}
        conn, snap = opened
        try:
            return {
                tid: n
                for tid, n in conn.execute(
                    "SELECT thread_id, COUNT(*) FROM thread_items GROUP BY thread_id"
                )
            }
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
            snap.unlink(missing_ok=True)

    def _discover_from_files(self) -> list[ChatRef]:
        """Fallback when the state database is missing: read rollout headers."""
        if not self.sessions_dir.is_dir():
            return []
        refs = []
        for path in sorted(self.sessions_dir.rglob("rollout-*.jsonl")):
            if self.skip(path):
                continue
            head: dict = {}
            try:
                with path.open("rb") as fh:
                    for raw in fh:
                        if raw.strip():
                            head = json.loads(raw).get("payload", {})
                            break
            except (OSError, json.JSONDecodeError):
                continue
            tid = head.get("id") or head.get("session_id") or path.stem
            refs.append(
                ChatRef(
                    tool=self.tool,
                    chat_id=str(tid),
                    project_path=head.get("cwd"),
                    title="(untitled)",
                    started=ts(head.get("timestamp")),
                    updated=None,
                    size_bytes=path.stat().st_size,
                    message_count=0,
                    files=(path,),
                    extras={"rollout_path": str(path)},
                )
            )
        return refs

    def collect(self, refs: list[ChatRef]) -> list[CopySpec]:
        if not refs:
            return []
        ids = [r.chat_id for r in refs]
        specs: list[CopySpec] = []
        for ref in refs:
            for path in ref.files:
                if self.skip(path):
                    continue
                specs.append(
                    CopySpec(
                        member=f"chats/{self.tool}/{ref.chat_id}/rollout.jsonl",
                        source=path,
                    )
                )
        for db_name, tables in ((STATE_DB, STATE_TABLES), (HISTORY_DB, HISTORY_TABLES)):
            for chat_id, payload in self._export_rows(db_name, tables, ids).items():
                specs.append(
                    CopySpec(
                        member=f"chats/{self.tool}/{chat_id}/{Path(db_name).stem}.rows.json",
                        data=json.dumps(payload, indent=1, default=str).encode(),
                    )
                )
        return specs

    def _export_rows(
        self, db_name: str, tables: dict[str, str], ids: list[str]
    ) -> dict[str, dict]:
        opened = self._open(db_name)
        if opened is None:
            return {}
        conn, snap = opened
        try:
            out: dict[str, dict] = {cid: {} for cid in ids}
            for table, column in tables.items():
                for row in _rows(conn, table, column, ids):
                    key = row.get(column)
                    if key in out:
                        out[key].setdefault(table, []).append(row)
            if db_name == STATE_DB:
                self._attach_projects(conn, out)
            return {k: v for k, v in out.items() if v}
        finally:
            conn.close()
            snap.unlink(missing_ok=True)

    @staticmethod
    def _attach_projects(conn: sqlite3.Connection, out: dict[str, dict]) -> None:
        for chat_id, payload in out.items():
            threads = payload.get("threads") or []
            pid = threads[0].get("project_id") if threads else None
            if not pid:
                continue
            payload["projects"] = _rows(conn, "projects", "id", [pid])
            payload["project_roots"] = _rows(conn, "project_roots", "project_id", [pid])

    def sidecars(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs: list[CopySpec] = []
        config = self.root / "config.toml"
        if config.exists():
            wanted = {r.project_path for r in refs if r.project_path}
            blocks = _extract_project_blocks(config.read_text(errors="replace"), wanted)
            if blocks:
                specs.append(
                    CopySpec(
                        member=f"sidecars/{self.tool}/config.projects.toml",
                        data=blocks.encode(),
                    )
                )
        history = self.root / "history.jsonl"
        if history.exists():
            ids = {r.chat_id for r in refs}
            kept = []
            with history.open("rb") as fh:
                for raw in fh:
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if row.get("session_id") in ids:
                        kept.append(raw.strip())
            if kept:
                specs.append(
                    CopySpec(
                        member=f"sidecars/{self.tool}/history.jsonl",
                        data=b"\n".join(kept) + b"\n",
                    )
                )
        return specs

    def transcript_texts(self, ref: ChatRef):
        for path in ref.files:
            try:
                yield path.read_text(errors="replace")
            except OSError:
                continue

    # -- write ---------------------------------------------------------
    def plan_restore(
        self, bundle_dir: Path, dest_root: Path, mapping: dict[str, str]
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        chats_dir = bundle_dir / "chats" / self.tool
        if chats_dir.is_dir():
            for chat_dir in sorted(p for p in chats_dir.iterdir() if p.is_dir()):
                changes.extend(self._plan_chat(chat_dir, dest_root, mapping))
        changes.extend(self._plan_sidecars(bundle_dir, dest_root, mapping))
        return changes

    def _plan_chat(
        self, chat_dir: Path, dest_root: Path, mapping: dict[str, str]
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        chat_id = chat_dir.name
        rollout = chat_dir / "rollout.jsonl"
        target_rollout: Path | None = None
        state = _load_rows(chat_dir / f"{Path(STATE_DB).stem}.rows.json")
        threads = state.get("threads") or []
        if rollout.exists():
            old_path = threads[0].get("rollout_path") if threads else None
            target_rollout = self._rollout_target(old_path, rollout, dest_root, mapping)
            changes.append(
                FileChange(
                    target_rollout,
                    "skip-exists" if target_rollout.exists() else "create",
                    f"thread {chat_id[:8]}",
                    data=_rewrite_jsonl(rollout, mapping),
                )
            )
        for row in threads:
            if target_rollout is not None:
                row["rollout_path"] = str(target_rollout)
            row["cwd"] = remap_string(row.get("cwd", ""), mapping)
        for db_name, tables in ((STATE_DB, STATE_TABLES), (HISTORY_DB, HISTORY_TABLES)):
            payload = state if db_name == STATE_DB else _load_rows(
                chat_dir / f"{Path(HISTORY_DB).stem}.rows.json"
            )
            payload = {k: remap_obj(v, mapping) for k, v in payload.items()}
            if not payload:
                continue
            total = sum(len(v) for v in payload.values())
            changes.append(
                FileChange(
                    dest_root / db_name,
                    "sqlite-upsert",
                    f"{total} rows across {len(payload)} tables (thread {chat_id[:8]})",
                    rows=[(table, list(rows)) for table, rows in payload.items()],
                )
            )
        return changes

    def _rollout_target(
        self, old_path: str | None, bundled: Path, dest_root: Path, mapping: dict[str, str]
    ) -> Path:
        if old_path:
            mapped = remap_string(old_path, mapping)
            candidate = Path(mapped)
            try:
                rel = Path(old_path).relative_to(self.root)
                return dest_root / rel
            except ValueError:
                if candidate.is_absolute():
                    return candidate
        return dest_root / "sessions" / "restored" / f"{bundled.parent.name}.jsonl"

    def _plan_sidecars(
        self, bundle_dir: Path, dest_root: Path, mapping: dict[str, str]
    ) -> list[FileChange]:
        side = bundle_dir / "sidecars" / self.tool
        changes: list[FileChange] = []
        blocks_file = side / "config.projects.toml"
        if blocks_file.exists():
            target = dest_root / "config.toml"
            current = target.read_text(errors="replace") if target.exists() else ""
            incoming = _remap_project_blocks(blocks_file.read_text(), mapping)
            missing = _missing_blocks(current, incoming)
            if missing:
                changes.append(
                    FileChange(
                        target,
                        "merge",
                        f"{missing.count('[projects.')} project block(s) appended",
                        data=(current.rstrip("\n") + "\n\n" + missing).encode()
                        if current
                        else missing.encode(),
                    )
                )
        history = side / "history.jsonl"
        if history.exists():
            target = dest_root / "history.jsonl"
            current = target.read_bytes() if target.exists() else b""
            changes.append(
                FileChange(
                    target,
                    "merge",
                    "prompt history lines appended",
                    data=current + history.read_bytes(),
                )
            )
        return changes


def _load_rows(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise StoreError(f"unreadable bundle member {path}: {e}") from e


def _rewrite_jsonl(path: Path, mapping: dict[str, str]) -> bytes:
    out: list[bytes] = []
    with path.open("rb") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except (json.JSONDecodeError, UnicodeDecodeError):
                out.append(stripped)
                continue
            out.append(json.dumps(remap_obj(row, mapping), ensure_ascii=False).encode())
    return b"\n".join(out) + (b"\n" if out else b"")


def _extract_project_blocks(text: str, wanted: set[str | None]) -> str:
    """Pull `[projects."<path>"]` blocks for the selected cwds, verbatim."""
    lines = text.splitlines()
    out: list[str] = []
    keeping = False
    for line in lines:
        match = _PROJECT_HEADER.match(line)
        if match:
            keeping = match.group("path") in wanted
        elif line.lstrip().startswith("[") and not line.lstrip().startswith("[projects."):
            keeping = False
        if keeping:
            out.append(line)
    return "\n".join(out).strip() + "\n" if out else ""


def _remap_project_blocks(text: str, mapping: dict[str, str]) -> str:
    out = []
    for line in text.splitlines():
        match = _PROJECT_HEADER.match(line)
        if match:
            old = match.group("path")
            line = line.replace(f'"{old}"', f'"{remap_string(old, mapping)}"')
        out.append(line)
    return "\n".join(out).strip() + "\n" if out else ""


def _missing_blocks(current: str, incoming: str) -> str:
    have = {m.group("path") for m in map(_PROJECT_HEADER.match, current.splitlines()) if m}
    out: list[str] = []
    keeping = False
    for line in incoming.splitlines():
        match = _PROJECT_HEADER.match(line)
        if match:
            keeping = match.group("path") not in have
        if keeping:
            out.append(line)
    return "\n".join(out).strip() + "\n" if out else ""
