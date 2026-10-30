"""Journal working files of one run (M-F plan 5.1, 5.2): the phase state, atomic writes that refuse
symlinks, and the journal slug.

Every write is atomic (temp file in the same folder, then `os.replace`). A symlinked target or
folder is refused. Nothing here reads or writes `config/journals/` (that is `adopt`, a later
ticket).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from scieflow.core import events

FOLDER = "journal"
STATE = "state.json"
PHASES = ("facts", "sources-manual", "gate-open", "denied", "fetched")
WORKING = ("state.json", "facts.json", "candidates.json", "sources.json", "profile.draft.yml")
SLUG_SHAPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class StateError(Exception):
    """A refusal or a bad file in the journal folder: exit 1, nothing written."""


def journal_dir(run: Path) -> Path:
    return Path(run) / FOLDER


def slugify(name: str) -> str:
    """NFKD, ASCII only, lowercase, runs of non-alphanumerics -> '-', trimmed, at most 64."""
    ascii_text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:64].rstrip("-")


def check_slug(slug: str) -> str:
    if not SLUG_SHAPE.match(slug or ""):
        raise StateError(f"not a journal slug: {slug!r} (lowercase letters, digits and hyphens, "
                         "at most 64 characters)")
    return slug


def _refuse_link(path: Path) -> None:
    """Refuse a symlink at the target or in the folders it is written into (the run's journal
    folder and its sources/ folder). The project's own ancestors may legitimately be links."""
    for part in (path, path.parent, path.parent.parent):
        if part.name in ("journal", "sources", "workspace") or part == path:
            if part.is_symlink():
                raise StateError(f"refused: {part.name} is a symbolic link")


def atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically; a symlink anywhere on the way is refused."""
    path = Path(path)
    _refuse_link(path.parent)
    if path.is_symlink():
        raise StateError(f"refused: {path.name} is a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    atomic_write(Path(path), text.encode("utf-8"))


def read_json(path: Path):
    path = Path(path)
    if not path.exists():
        return None
    _refuse_link(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"{path.name} is not readable JSON ({type(exc).__name__})") from None


def read_state(folder: Path) -> dict:
    state = read_json(Path(folder) / STATE)
    if state is None:
        return {}
    if not isinstance(state, dict) or state.get("phase") not in (None, *PHASES):
        raise StateError(f"{STATE}: unknown phase or shape")
    return state


def write_state(folder: Path, state: dict) -> None:
    write_text(Path(folder) / STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def remove_working(folder: Path) -> None:
    """`--restart`: remove the working files, never `adopted/` (M-F 5.1)."""
    folder = Path(folder)
    if not folder.is_dir():
        return
    for name in WORKING:
        target = folder / name
        if target.is_symlink():
            raise StateError(f"refused: {name} is a symbolic link")
        if target.is_file():
            target.unlink()
    sources = folder / "sources"
    if sources.is_symlink():
        raise StateError("refused: sources is a symbolic link")
    if sources.is_dir():
        for child in sources.iterdir():
            if child.is_symlink() or not child.is_file():
                raise StateError(f"refused: sources/{child.name} is not a regular file")
            child.unlink()


def note(run: Path, *, journal: str, phase: str, sources_ok: int, sources_failed: int,
         gate: str | None) -> dict:
    """The one note per phase change (M-F 5.4). Only `note.*` events; no new event type."""
    return events.emit(run, "note.journal_research", actor="agent", journal=journal, phase=phase,
                       sources_ok=sources_ok, sources_failed=sources_failed, gate=gate)
