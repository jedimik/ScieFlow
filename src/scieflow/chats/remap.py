"""Path rewriting shared by every store.

Rewriting is JSON-aware, never `sed`: a transcript that merely *mentions* a
path in prose or in a tool result must not be edited, so only string values
under known path-bearing keys are substituted.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

#: JSON keys whose string value is an absolute path (or a file:// URI).
PATH_KEYS: frozenset[str] = frozenset(
    {
        "cwd",
        "trackingPath",
        "rollout_path",
        "workspace",
        "workspace_uris",
        "app_data_dir",
        "project",
        "projectPath",
        "worktree",
        "source",
    }
)


def slug(path: str) -> str:
    """Claude Code's project directory name: an absolute path, `/` -> `-`."""
    return path.replace("/", "-")


def unslug(name: str) -> str:
    """Best-effort inverse of `slug`; ambiguous when a path contains a dash."""
    return name.replace("-", "/")


def build_mapping(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Longest-prefix-first substitution map from (old, new) pairs."""
    cleaned: dict[str, str] = {}
    for old, new in pairs:
        old = old.rstrip("/")
        new = new.rstrip("/")
        if old and new and old != new:
            cleaned[old] = new
    return dict(sorted(cleaned.items(), key=lambda kv: -len(kv[0])))


def remap_string(value: str, mapping: dict[str, str]) -> str:
    """Replace a mapped prefix, honouring `file://` URIs. First match wins."""
    for old, new in mapping.items():
        for prefix in ("", "file://"):
            head = prefix + old
            if value == head or value.startswith(head + "/"):
                return prefix + new + value[len(head) :]
    return value


def remap_obj(obj, mapping: dict[str, str], keys: frozenset[str] = PATH_KEYS):
    """Recursively rewrite path-bearing values in parsed JSON."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in keys:
                if isinstance(value, str):
                    value = remap_string(value, mapping)
                elif isinstance(value, list):
                    value = [
                        remap_string(v, mapping) if isinstance(v, str) else v for v in value
                    ]
            else:
                value = remap_obj(value, mapping, keys)
            out[key] = value
        return out
    if isinstance(obj, list):
        return [remap_obj(v, mapping, keys) for v in obj]
    return obj


def remap_keys(mapping_obj: dict, mapping: dict[str, str]) -> dict:
    """Rewrite a dict whose *keys* are absolute paths (`~/.claude.json` projects)."""
    return {remap_string(k, mapping) if isinstance(k, str) else k: v
            for k, v in mapping_obj.items()}


@dataclass
class FileChange:
    """One pending restore action. Rendered in the dry-run table."""

    path: Path
    action: str  # create | merge | rename | skip-exists | sqlite-upsert
    detail: str = ""
    data: bytes | None = None
    source: Path | None = None
    note: str = ""
    rows: list = field(default_factory=list)

    def diff(self) -> str:
        """Unified diff against the current contents, for text members."""
        if self.data is None:
            return ""
        try:
            new = self.data.decode()
        except UnicodeDecodeError:
            return f"(binary, {len(self.data)} bytes)"
        old = ""
        if self.path.exists():
            try:
                old = self.path.read_text()
            except (OSError, UnicodeDecodeError):
                return "(unreadable existing file)"
        if old == new:
            return ""
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"{self.path} (current)",
                tofile=f"{self.path} (restored)",
                n=2,
            )
        )

    def render(self) -> str:
        suffix = f"  — {self.detail}" if self.detail else ""
        return f"  {self.action:<14} {self.path}{suffix}"
