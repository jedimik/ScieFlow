"""Gemini CLI: `~/.gemini`.

The most portable of the four stores. Chats live at
`tmp/<slug>/chats/session-<iso>-<hex>.jsonl`, the slug comes from
`projects.json` (absolute path -> lowercased basename), and the only embedded
path derivative is `projectHash`, which is `sha256(<absolute project path>)`
and must be recomputed when the path changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..model import ChatRef, CopySpec
from ..remap import FileChange, remap_string
from .base import StoreAdapter, parse_iso, register

PROJECTS_FILE = "projects.json"


def project_hash(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()


@register
class GeminiStore(StoreAdapter):
    tool = "gemini"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    def _projects(self) -> dict[str, str]:
        path = self.root / PROJECTS_FILE
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        projects = raw.get("projects") if isinstance(raw, dict) else None
        return projects if isinstance(projects, dict) else {}

    # -- read ----------------------------------------------------------
    def discover(self) -> list[ChatRef]:
        if not self.tmp_dir.is_dir():
            return []
        by_slug = {slug: path for path, slug in self._projects().items()}
        refs: list[ChatRef] = []
        for chat in sorted(self.tmp_dir.glob("*/chats/*.jsonl")):
            if self.skip(chat):
                continue
            slug = chat.parent.parent.name
            meta, messages, title = _read_chat(chat)
            refs.append(
                ChatRef(
                    tool=self.tool,
                    chat_id=meta.get("sessionId") or chat.stem,
                    project_path=by_slug.get(slug),
                    title=title or "(untitled)",
                    started=parse_iso(meta.get("startTime")),
                    updated=parse_iso(meta.get("lastUpdated")),
                    size_bytes=chat.stat().st_size,
                    message_count=messages,
                    files=(chat,),
                    extras={"slug": slug, "projectHash": meta.get("projectHash")},
                )
            )
        return refs

    def collect(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs = []
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
        specs = []
        wanted = {r.project_path for r in refs if r.project_path}
        projects = {p: s for p, s in self._projects().items() if p in wanted}
        if projects:
            specs.append(
                CopySpec(
                    member=f"sidecars/{self.tool}/{PROJECTS_FILE}",
                    data=json.dumps({"projects": projects}, indent=2).encode(),
                )
            )
        settings = self.root / "settings.json"
        if settings.exists():
            specs.append(CopySpec(member=f"sidecars/{self.tool}/settings.json", source=settings))
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
        bundled_projects = _bundled_projects(bundle_dir / "sidecars" / self.tool / PROJECTS_FILE)
        by_slug = {slug: path for path, slug in bundled_projects.items()}
        chats_dir = bundle_dir / "chats" / self.tool
        if chats_dir.is_dir():
            for path in sorted(chats_dir.rglob("*.jsonl")):
                rel = path.relative_to(chats_dir).parts
                old_slug = rel[1] if len(rel) >= 2 and rel[0] == "tmp" else ""
                old_project = by_slug.get(old_slug)
                new_project = remap_string(old_project, mapping) if old_project else None
                new_slug = _slug_for(new_project) if new_project else old_slug
                target = dest_root.joinpath("tmp", new_slug, *rel[2:])
                detail = "" if new_slug == old_slug else f"slug {old_slug} -> {new_slug}"
                changes.append(
                    FileChange(
                        target,
                        "skip-exists" if target.exists() else "create",
                        detail,
                        data=_rewrite_chat(path, new_project),
                    )
                )
        if bundled_projects:
            target = dest_root / PROJECTS_FILE
            current: dict = {}
            if target.exists():
                try:
                    current = json.loads(target.read_text()).get("projects", {})
                except (json.JSONDecodeError, OSError):
                    current = {}
            incoming = {
                remap_string(path, mapping): _slug_for(remap_string(path, mapping))
                for path in bundled_projects
            }
            merged = {**incoming, **current}
            changes.append(
                FileChange(
                    target,
                    "merge",
                    f"{len(incoming)} project mapping(s)",
                    data=json.dumps({"projects": merged}, indent=2).encode(),
                )
            )
        return changes


def _slug_for(path: str) -> str:
    """Gemini's `tmp/` directory name: lowercased basename, non-alphanumerics to `-`.

    Verified against every entry of a real `projects.json`, e.g.
    `/home/u/Github/research/SoftwareX_SegSnake` -> `softwarex-segsnake`.
    """
    name = re.sub(r"[^a-z0-9]+", "-", Path(path).name.lower()).strip("-")
    return name or re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def _bundled_projects(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("projects", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _read_chat(path: Path) -> tuple[dict, int, str | None]:
    """Header line, message count, and a title from the first real user turn."""
    meta: dict = {}
    messages = 0
    title: str | None = None
    try:
        with path.open("rb") as fh:
            for index, raw in enumerate(fh):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if index == 0:
                    meta = row
                    continue
                if "$set" in row:
                    found = row["$set"].get("messages")
                    if isinstance(found, list):
                        messages = max(messages, len(found))
                        title = title or _first_user_text(found)
                    continue
                if row.get("type"):
                    messages += 1
                    if title is None:
                        title = _first_user_text([row])
    except OSError:
        pass
    return meta, messages, title


def _first_user_text(messages: list) -> str | None:
    """First user text that is not the CLI's own `<session_context>` preamble."""
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "user":
            continue
        content = message.get("content")
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else part
            if not isinstance(text, str):
                continue
            cleaned = " ".join(text.split())
            if cleaned.startswith("<session_context>"):
                cleaned = cleaned.split("</session_context>", 1)[-1].strip()
            if cleaned:
                return cleaned[:120]
    return None


def _rewrite_chat(path: Path, new_project: str | None) -> bytes:
    """Only the header's `projectHash` changes; message bodies stay byte-identical."""
    lines = [line for line in path.read_bytes().splitlines() if line.strip()]
    if lines and new_project:
        try:
            header = json.loads(lines[0])
            header["projectHash"] = project_hash(new_project)
            lines[0] = json.dumps(header, ensure_ascii=False).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return b"\n".join(lines) + (b"\n" if lines else b"")
