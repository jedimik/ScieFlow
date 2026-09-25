"""The run charter: a versioned record of what has been agreed.

Long conversations drift. The charter is the fix: a human-curated document
that ScieFlow puts at the top of every prompt it composes, so the agent is
handed the goal again each time it speaks.

Versions are append-only and a revert appends a copy rather than rewinding,
because seeing how the goal moved is the point — a history that edits itself
cannot show you that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import events, store

CHARTER_FILE = "charter.yml"


class CharterError(ValueError):
    """A charter change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CHARTER_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(ws: Path) -> dict:
    """The whole document. A run without a charter reads as empty, because
    every run that predates this feature has none.

    A `charter.yml` that is not a mapping (a stray list, say) or that fails
    to parse at all is refused here, as `CharterError`, rather than left to
    surface as an `AttributeError` or a raw `yaml.YAMLError` wherever this
    is called from — `agent_run.compose_prompt` in particular, where an
    uncaught one used to crash every dispatch of the run with a traceback."""
    try:
        doc = store.read_yaml(_path(ws), default=None)
    except yaml.YAMLError as exc:
        raise CharterError(f"{_path(ws)}: cannot parse the charter: {exc}") from exc
    if not doc:
        return {"current": 0, "versions": []}
    if not isinstance(doc, dict):
        raise CharterError(
            f"{_path(ws)}: charter must be a mapping, got {type(doc).__name__}")
    return {"current": int(doc.get("current", 0)),
            "versions": list(doc.get("versions") or [])}


def _current_text_of(doc: dict) -> str:
    for version in doc["versions"]:
        if version.get("n") == doc["current"]:
            return str(version.get("text", ""))
    return ""


def _history_of(doc: dict) -> list[dict]:
    """Newest first — the order someone reviewing the drift wants."""
    return sorted(doc["versions"], key=lambda v: v.get("n", 0), reverse=True)


def current_text(ws: Path) -> str:
    return _current_text_of(read(ws))


def history(ws: Path) -> list[dict]:
    return _history_of(read(ws))


def snapshot(ws: Path) -> dict:
    """`current`, the current text, and the whole history — from one read.

    `current_text` and `history` each call `read` again, so calling more
    than one of them (as `service.run_charter` used to) reads `charter.yml`
    three separate times and can see a concurrent write partway through:
    `current: 2` from one read paired with version 3's text from another.
    Callers that need more than one of these values should use this
    instead.
    """
    doc = read(ws)
    return {"current": doc["current"], "text": _current_text_of(doc),
            "versions": _history_of(doc)}


def _append(ws: Path, text: str, actor: str, note: str) -> dict:
    """Append one version under the file lock, so concurrent writers cannot
    both claim the same number."""
    appended: dict = {}

    def bump(doc: dict) -> dict:
        versions = list((doc or {}).get("versions") or [])
        number = max((v.get("n", 0) for v in versions), default=0) + 1
        appended.update({"n": number, "ts": _now(), "actor": actor,
                         "text": text, "note": note})
        versions.append(dict(appended))
        return {"current": number, "versions": versions}

    store.update_yaml(_path(ws), bump)
    return appended


def set_text(ws: Path, text: str, actor: str = "human", note: str = "") -> dict:
    if not text or not text.strip():
        raise CharterError("a charter needs text")
    if actor not in events.ACTORS:
        raise CharterError(f"unknown actor {actor!r} (one of {', '.join(sorted(events.ACTORS))})")
    version = _append(ws, text, actor, note)
    events.emit(ws, "charter.set", actor, version=version["n"], note=note)
    return version


def revert(ws: Path, version: int, actor: str = "human") -> dict:
    """Make an earlier version current again by appending a copy of it."""
    try:
        wanted = int(version)
    except (TypeError, ValueError) as exc:
        raise CharterError(f"not a version number: {version!r}") from exc
    if actor not in events.ACTORS:
        raise CharterError(f"unknown actor {actor!r} (one of {', '.join(sorted(events.ACTORS))})")
    match = next((v for v in read(ws)["versions"] if v.get("n") == wanted), None)
    if match is None:
        raise CharterError(f"no charter version {wanted}")
    restored = _append(ws, str(match.get("text", "")), actor,
                       f"reverted to version {wanted}")
    events.emit(ws, "charter.reverted", actor,
                version=restored["n"], restored_from=wanted)
    return restored
