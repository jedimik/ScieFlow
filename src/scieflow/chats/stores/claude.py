"""Claude Code: `~/.claude`.

Transcripts live at `projects/<abs-path-slug>/<uuid>.jsonl` and carry `cwd`
on nearly every line, so both the directory name and the file contents need
rewriting when the path differs on the target machine.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import ChatRef, CopySpec
from ..remap import FileChange, remap_keys, remap_obj, remap_string, slug, unslug
from .base import StoreAdapter, parse_iso, register

#: Per-session leftovers that live outside `projects/`, matched by session id.
AUX_DIRS: tuple[str, ...] = (
    "session-env",
    "file-history",
    "shell-snapshots",
    "todos",
    "plans",
)

#: Dropped from `settings.json` — they can carry tokens or helper commands.
SETTINGS_REDACT: tuple[str, ...] = ("env", "apiKeyHelper", "awsAuthRefresh")

#: Kept from `~/.claude.json`; everything else is machine identity or cache.
GLOBAL_KEEP: tuple[str, ...] = ("projects", "githubRepoPaths")

_TITLE_MARKERS = (b'"custom-title"', b'"ai-title"')


@register
class ClaudeStore(StoreAdapter):
    tool = "claude"

    # -- paths ---------------------------------------------------------
    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def global_json(self) -> Path:
        """`~/.claude.json` — beside the store root, not inside it."""
        return self.root.parent / f"{self.root.name}.json"

    # -- read ----------------------------------------------------------
    def _history_titles(self) -> dict[str, str]:
        path = self.root / "history.jsonl"
        titles: dict[str, str] = {}
        if not path.exists():
            return titles
        with path.open("rb") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                sid = row.get("sessionId")
                if sid and sid not in titles and row.get("display"):
                    titles[sid] = str(row["display"]).strip().splitlines()[0][:120]
        return titles

    @staticmethod
    def _scan(path: Path) -> tuple[int, dict, dict, str | None]:
        """One pass: record count, first record, last record, best title."""
        count = 0
        first: dict = {}
        last: dict = {}
        title: str | None = None
        with path.open("rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                count += 1
                if any(marker in raw for marker in _TITLE_MARKERS):
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        row = {}
                    if row.get("type") == "custom-title":
                        title = row.get("customTitle") or row.get("title") or title
                    elif row.get("type") == "ai-title" and title is None:
                        title = row.get("aiTitle") or row.get("title")
                    continue
                if count == 1 or not first:
                    try:
                        first = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        first = {}
                try:
                    last = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        return count, first, last, title

    def _aux_files(self, chat_id: str) -> list[Path]:
        found: list[Path] = []
        for name in AUX_DIRS:
            base = self.root / name
            if not base.is_dir():
                continue
            found.extend(
                p for p in base.rglob(f"*{chat_id}*") if p.is_file() and not self.skip(p)
            )
        return found

    def discover(self) -> list[ChatRef]:
        if not self.projects_dir.is_dir():
            return []
        titles = self._history_titles()
        refs: list[ChatRef] = []
        for slug_dir in sorted(self.projects_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            for transcript in sorted(slug_dir.glob("*.jsonl")):
                if self.skip(transcript):
                    continue
                chat_id = transcript.stem
                count, first, last, title = self._scan(transcript)
                if count == 0:
                    continue
                project = first.get("cwd") or last.get("cwd") or unslug(slug_dir.name)
                files = [transcript]
                sidecar_dir = slug_dir / chat_id
                if sidecar_dir.is_dir():
                    files.extend(
                        p for p in sidecar_dir.rglob("*") if p.is_file() and not self.skip(p)
                    )
                files.extend(self._aux_files(chat_id))
                refs.append(
                    ChatRef(
                        tool=self.tool,
                        chat_id=chat_id,
                        project_path=project,
                        title=title or titles.get(chat_id) or "(untitled)",
                        started=parse_iso(first.get("timestamp")),
                        updated=parse_iso(last.get("timestamp")),
                        size_bytes=sum(p.stat().st_size for p in files),
                        message_count=count,
                        files=tuple(files),
                        extras={"slug": slug_dir.name},
                    )
                )
        return refs

    def _member(self, path: Path) -> str:
        return f"chats/{self.tool}/{path.relative_to(self.root).as_posix()}"

    def collect(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs: list[CopySpec] = []
        seen: set[str] = set()
        for ref in refs:
            for path in ref.files:
                member = self._member(path)
                if member in seen or self.skip(path):
                    continue
                seen.add(member)
                specs.append(CopySpec(member=member, source=path))
        return specs

    def _filtered_global(self, refs: list[ChatRef]) -> bytes | None:
        if not self.global_json.exists():
            return None
        try:
            raw = json.loads(self.global_json.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        wanted = {r.project_path for r in refs if r.project_path}
        out: dict = {}
        projects = raw.get("projects")
        if isinstance(projects, dict):
            out["projects"] = {
                key: _redact_mcp(value)
                for key, value in projects.items()
                if key in wanted
            }
        repos = raw.get("githubRepoPaths")
        if isinstance(repos, dict):
            out["githubRepoPaths"] = {
                key: value
                for key, value in repos.items()
                if any(str(value).startswith(w) or key in wanted for w in wanted)
            }
        return json.dumps(out, indent=2, sort_keys=True).encode()

    def sidecars(self, refs: list[ChatRef]) -> list[CopySpec]:
        specs: list[CopySpec] = []
        settings = self.root / "settings.json"
        if settings.exists():
            try:
                data = json.loads(settings.read_text())
                for key in SETTINGS_REDACT:
                    data.pop(key, None)
                specs.append(
                    CopySpec(
                        member=f"sidecars/{self.tool}/settings.json",
                        data=json.dumps(data, indent=2, sort_keys=True).encode(),
                    )
                )
            except (json.JSONDecodeError, OSError):
                pass
        for name in ("installed_plugins.json", "known_marketplaces.json"):
            path = self.root / "plugins" / name
            if path.exists():
                specs.append(
                    CopySpec(member=f"sidecars/{self.tool}/{name}", source=path)
                )
        filtered = self._filtered_global(refs)
        if filtered is not None:
            specs.append(
                CopySpec(member=f"sidecars/{self.tool}/claude.json.filtered", data=filtered)
            )
        return specs

    def transcript_texts(self, ref: ChatRef):
        for path in ref.files:
            if path.suffix == ".jsonl":
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
            for path in sorted(p for p in chats_dir.rglob("*") if p.is_file()):
                rel = path.relative_to(chats_dir)
                changes.append(self._plan_member(path, rel, dest_root, mapping))
        changes.extend(self._plan_sidecars(bundle_dir, dest_root, mapping))
        return changes

    def _plan_member(
        self, path: Path, rel: Path, dest_root: Path, mapping: dict[str, str]
    ) -> FileChange:
        parts = rel.parts
        if parts[0] == "projects" and len(parts) >= 3:
            old_slug = parts[1]
            new_slug = self._new_slug(path, old_slug, mapping)
            target = dest_root.joinpath("projects", new_slug, *parts[2:])
            detail = "" if new_slug == old_slug else f"slug {old_slug} -> {new_slug}"
            if path.suffix == ".jsonl":
                data = _rewrite_jsonl(path, mapping)
                action = "skip-exists" if target.exists() else "create"
                return FileChange(target, action, detail, data=data)
            action = "skip-exists" if target.exists() else "create"
            return FileChange(target, action, detail, source=path)
        target = dest_root / rel
        action = "skip-exists" if target.exists() else "create"
        return FileChange(target, action, "", source=path)

    def _new_slug(self, path: Path, old_slug: str, mapping: dict[str, str]) -> str:
        cwd = None
        if path.suffix == ".jsonl":
            try:
                with path.open("rb") as fh:
                    for raw in fh:
                        if not raw.strip():
                            continue
                        try:
                            cwd = json.loads(raw).get("cwd")
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            cwd = None
                        if cwd:
                            break
            except OSError:
                cwd = None
        source = cwd or unslug(old_slug)
        return slug(remap_string(source, mapping))

    def _plan_sidecars(
        self, bundle_dir: Path, dest_root: Path, mapping: dict[str, str]
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        side = bundle_dir / "sidecars" / self.tool
        filtered = side / "claude.json.filtered"
        if filtered.exists():
            target = dest_root.parent / f"{dest_root.name}.json"
            try:
                incoming = json.loads(filtered.read_text())
            except (json.JSONDecodeError, OSError):
                incoming = {}
            current = {}
            if target.exists():
                try:
                    current = json.loads(target.read_text())
                except (json.JSONDecodeError, OSError):
                    current = {}
            merged = dict(current)
            for key in GLOBAL_KEEP:
                block = incoming.get(key)
                if not isinstance(block, dict):
                    continue
                if key == "githubRepoPaths":
                    # repo -> path: the *value* is the checkout location.
                    remapped = {
                        repo: remap_string(path, mapping) if isinstance(path, str) else path
                        for repo, path in block.items()
                    }
                else:
                    # project path -> settings: the *key* is the path.
                    remapped = remap_keys(remap_obj(block, mapping), mapping)
                existing = merged.get(key) if isinstance(merged.get(key), dict) else {}
                merged[key] = {**remapped, **existing}  # never clobber the target's own
            changes.append(
                FileChange(
                    target,
                    "merge",
                    f"{len(incoming.get('projects', {}))} project entries",
                    data=json.dumps(merged, indent=2, sort_keys=True).encode(),
                )
            )
        for name in ("installed_plugins.json", "known_marketplaces.json"):
            path = side / name
            if path.exists():
                changes.append(
                    FileChange(
                        dest_root / "plugins" / name,
                        "skip-exists" if (dest_root / "plugins" / name).exists() else "create",
                        "plugin registry (payloads are re-installed, not restored)",
                        source=path,
                    )
                )
        settings = side / "settings.json"
        if settings.exists():
            target = dest_root / "settings.json"
            changes.append(
                FileChange(
                    target,
                    "skip-exists" if target.exists() else "create",
                    "credentials and env already stripped",
                    source=settings,
                )
            )
        return changes


def _redact_mcp(project: dict) -> dict:
    """Blank MCP server env values — they routinely hold API keys."""
    if not isinstance(project, dict):
        return project
    servers = project.get("mcpServers")
    if not isinstance(servers, dict):
        return project
    out = dict(project)
    out["mcpServers"] = {
        name: ({**cfg, "env": {k: "<redacted>" for k in cfg["env"]}}
               if isinstance(cfg, dict) and isinstance(cfg.get("env"), dict)
               else cfg)
        for name, cfg in servers.items()
    }
    return out


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
