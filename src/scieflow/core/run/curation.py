"""The curation document: passages kept from agents' drafts, the researcher's
own words, and the order the two are meant to be read in.

A merge round reads several agents' manuscript drafts at once. Nothing in
this module decides what is worth keeping — a human does that, one passage
at a time — but once decided, the choice has to survive the next round
rewriting the file the passage came from. So a kept block stores the TEXT,
with who wrote it and where it stood, never a byte offset into a draft that
will not exist in that shape tomorrow.

Versions are append-only, exactly like `run/charter.py`: a revert restores
an earlier selection by appending a copy of it, not by rewinding, because
seeing that yesterday's trim happened at all is part of what makes reverting
it safe.

Nothing here interprets a passage or a note. They are text a human
assembled for an agent to read — no templating, no `.format()`, no
escaping, anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import events, store

CURATION_FILE = "manuscript/curation/document.yml"
KINDS = frozenset({"kept", "mine"})

_UNSET = object()


class CurationError(ValueError):
    """A curation change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CURATION_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_actor(actor: str) -> None:
    if actor not in events.ACTORS:
        raise CurationError(f"unknown actor {actor!r} (one of {', '.join(sorted(events.ACTORS))})")


def _check_text(text: str, what: str) -> None:
    if not text or not text.strip():
        raise CurationError(f"{what} needs text")


def _require_block(blocks: list, block_id: str) -> int:
    """Index of the block named `block_id`, or `CurationError`.

    Named lookup, never a position — the whole point of giving each block a
    stable `id` is that another tab reordering the document must not make an
    edit meant for one block land on another.
    """
    for i, block in enumerate(blocks):
        if block.get("id") == block_id:
            return i
    raise CurationError(f"no block {block_id!r} in this curation document")


def _load(ws: Path, raw: dict | None = _UNSET) -> dict:
    """Load, validate and coerce the on-disk document into one safe shape:
    `{"current": int, "versions": [...]}`, with every version's `n` and
    `round` as ints, `note` as a str, `blocks` as a list.

    `read`, `history` and `_mutate`'s `bump` each used to re-implement
    "read, wrap `yaml.YAMLError`, assert it's a mapping" on their own, which
    is how a fourth case — a hand-edited `round: abc` — slipped through all
    three and reached a caller as a raw `ValueError` instead of
    `CurationError`. This is the one place that wraps a `yaml.YAMLError`, a
    non-mapping document, and a bad `int()` coercion, so none of them can
    surface as a stdlib error to whatever composes the merge prompt.

    Pass `raw` when the document has already been read under the lock
    (`_mutate`'s `bump`, which gets it from `store.update_yaml`) so this
    does not read the file a second time — `read` and `history` still each
    call this exactly once, so the single-read guarantee holds either way.
    """
    if raw is _UNSET:
        try:
            raw = store.read_yaml(_path(ws), default=None)
        except yaml.YAMLError as exc:
            raise CurationError(
                f"{_path(ws)}: cannot parse the curation document: {exc}") from exc
    if raw and not isinstance(raw, dict):
        raise CurationError(
            f"{_path(ws)}: curation document must be a mapping, got {type(raw).__name__}")
    raw = raw or {}
    try:
        versions = [{**v, "n": int(v.get("n", 0)), "round": int(v.get("round", 1)),
                    "note": str(v.get("note", "")), "blocks": list(v.get("blocks") or [])}
                    for v in (raw.get("versions") or [])]
        current = int(raw.get("current", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise CurationError(f"{_path(ws)}: malformed curation document: {exc}") from exc
    return {"current": current, "versions": versions}


def _snapshot_of(loaded: dict) -> dict:
    """A fresh `{"round", "note", "blocks"}` for the current version.

    Always a new dict with a new `blocks` list, never a shared literal —
    `read()` hands this straight to callers, and a caller mutating what it
    got back (a long-lived `scieflow serve` process handling one request
    after another) must not be able to leak a block into another run or
    another call.
    """
    match = next((v for v in loaded["versions"] if v.get("n") == loaded["current"]), None)
    if match is None:
        return {"round": 1, "note": "", "blocks": []}
    return {"round": match["round"], "note": match["note"], "blocks": list(match["blocks"])}


def read(ws: Path) -> dict:
    """`{"round", "note", "blocks", "version"}` — from one read of the file.

    A run without a curation document reads as empty: round 1, no note, no
    blocks, version 0.

    This returns `version`, singular — the current version *number*. The
    snapshot list itself is `versions`, on disk and from `history()`; a
    return value here using the same word for a count would give one key
    two meanings.
    """
    loaded = _load(ws)
    return {**_snapshot_of(loaded), "version": loaded["current"]}


def history(ws: Path) -> list[dict]:
    """Every version, oldest first — the order a merge-round narrative reads in."""
    return sorted(_load(ws)["versions"], key=lambda v: v.get("n", 0))


def _mutate(ws: Path, actor: str,
            fn: Callable[[dict], tuple[int, str, list]]) -> dict:
    """Apply `fn(snapshot) -> (round, note, blocks)` under one lock and
    append the result as a new version; return the appended snapshot.

    `fn` is handed the state as it stands *under the lock* — never a value
    read before it — so two overlapping writers (two browser tabs, or
    `test_concurrent_appends_do_not_lose_a_block`) each build their new
    `blocks` list on top of the other's write instead of clobbering it.

    Any `CurationError` `fn` raises (an id that no longer resolves, say)
    propagates before `store.update_yaml` writes anything: raising happens
    while computing the replacement document, never after.
    """
    appended: dict = {}

    def bump(raw: dict) -> dict:
        loaded = _load(ws, raw)
        versions = list(loaded["versions"])
        snapshot = _snapshot_of(loaded)
        round_, note, blocks = fn(snapshot)
        number = max((v.get("n", 0) for v in versions), default=0) + 1
        appended.update({"n": number, "at": _now(), "actor": actor,
                         "round": round_, "note": note, "blocks": blocks})
        versions.append(dict(appended))
        return {"current": number, "versions": versions}

    try:
        store.update_yaml(_path(ws), bump)
    except CurationError:
        raise
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise CurationError(f"failed to update the curation document: {exc}") from exc
    return appended


def _doc(appended: dict) -> dict:
    return {"round": appended["round"], "note": appended["note"],
            "blocks": appended["blocks"], "version": appended["n"]}


def keep(ws: Path, text: str, *, agent: str, section: str, actor: str = "human") -> dict:
    """Add a passage kept from an agent's draft, with where it came from.

    The text is stored verbatim, with its provenance — never an offset into
    a draft file the next round will rewrite out from under it.
    """
    _check_actor(actor)
    _check_text(text, "a kept passage")
    if not agent or not str(agent).strip():
        raise CurationError("a kept passage needs the agent it came from")
    if not section or not str(section).strip():
        raise CurationError("a kept passage needs the section it came from")

    created: dict = {}

    def do_keep(snapshot: dict) -> tuple[int, str, list]:
        block = {"id": store.new_id(), "kind": "kept", "text": text,
                 "agent": agent, "section": section, "round": snapshot["round"]}
        created.update(block)
        return snapshot["round"], snapshot["note"], [*snapshot["blocks"], block]

    appended = _mutate(ws, actor, do_keep)
    events.emit(ws, "curation.changed", actor, version=appended["n"], block=created["id"])
    return created


def add_own(ws: Path, text: str, actor: str = "human") -> dict:
    """Add the researcher's own words. They claim no provenance — there is
    no agent or section to credit them to."""
    _check_actor(actor)
    _check_text(text, "your own text")

    created: dict = {}

    def do_add(snapshot: dict) -> tuple[int, str, list]:
        block = {"id": store.new_id(), "kind": "mine", "text": text, "round": snapshot["round"]}
        created.update(block)
        return snapshot["round"], snapshot["note"], [*snapshot["blocks"], block]

    appended = _mutate(ws, actor, do_add)
    events.emit(ws, "curation.changed", actor, version=appended["n"], block=created["id"])
    return created


def edit_block(ws: Path, block_id: str, text: str, actor: str = "human") -> dict:
    """Replace a block's text in place, by id, keeping its provenance and
    position."""
    _check_actor(actor)
    _check_text(text, "an edited block")

    def do_edit(snapshot: dict) -> tuple[int, str, list]:
        blocks = list(snapshot["blocks"])
        idx = _require_block(blocks, block_id)
        blocks[idx] = {**blocks[idx], "text": text}
        return snapshot["round"], snapshot["note"], blocks

    appended = _mutate(ws, actor, do_edit)
    events.emit(ws, "curation.changed", actor, version=appended["n"], block=block_id)
    return _doc(appended)


def move_block(ws: Path, block_id: str, position: int, actor: str = "human") -> dict:
    """Move a block to `position` in the list, by id. `position` is clamped
    to the document's current length, so a stale index from a slower tab
    lands at the nearest end rather than raising."""
    _check_actor(actor)
    try:
        position = int(position)
    except (TypeError, ValueError) as exc:
        raise CurationError(f"not a position: {position!r}") from exc

    def do_move(snapshot: dict) -> tuple[int, str, list]:
        blocks = list(snapshot["blocks"])
        idx = _require_block(blocks, block_id)
        block = blocks.pop(idx)
        pos = max(0, min(position, len(blocks)))
        blocks.insert(pos, block)
        return snapshot["round"], snapshot["note"], blocks

    appended = _mutate(ws, actor, do_move)
    events.emit(ws, "curation.changed", actor, version=appended["n"], block=block_id)
    return _doc(appended)


def remove_block(ws: Path, block_id: str, actor: str = "human") -> dict:
    """Drop a block, by id, leaving the rest in place."""
    _check_actor(actor)

    def do_remove(snapshot: dict) -> tuple[int, str, list]:
        blocks = list(snapshot["blocks"])
        idx = _require_block(blocks, block_id)
        blocks.pop(idx)
        return snapshot["round"], snapshot["note"], blocks

    appended = _mutate(ws, actor, do_remove)
    events.emit(ws, "curation.changed", actor, version=appended["n"], block=block_id)
    return _doc(appended)


def set_note(ws: Path, note: str, actor: str = "human") -> dict:
    """Replace the free-text note attached to the document."""
    _check_actor(actor)
    note = "" if note is None else str(note)

    def do_set(snapshot: dict) -> tuple[int, str, list]:
        return snapshot["round"], note, list(snapshot["blocks"])

    appended = _mutate(ws, actor, do_set)
    events.emit(ws, "curation.changed", actor, version=appended["n"])
    return _doc(appended)


def advance_round(ws: Path, actor: str = "human") -> int:
    """Move to the next round, keeping every block — each already remembers
    the round it was added in, so advancing never rewrites that history."""
    _check_actor(actor)

    def do_advance(snapshot: dict) -> tuple[int, str, list]:
        return snapshot["round"] + 1, snapshot["note"], list(snapshot["blocks"])

    appended = _mutate(ws, actor, do_advance)
    events.emit(ws, "curation.round", actor, version=appended["n"], round=appended["round"])
    return appended["round"]


def revert(ws: Path, version: int, actor: str = "human") -> dict:
    """Make an earlier selection current again by appending a copy of its
    blocks and note — a new version, not a rewind, so the record of what was
    trimmed since stays intact."""
    _check_actor(actor)
    try:
        wanted = int(version)
    except (TypeError, ValueError) as exc:
        raise CurationError(f"not a version number: {version!r}") from exc
    match = next((v for v in history(ws) if v.get("n") == wanted), None)
    if match is None:
        raise CurationError(f"no curation version {wanted}")
    restored_blocks = list(match.get("blocks") or [])
    restored_note = str(match.get("note", ""))

    def do_revert(snapshot: dict) -> tuple[int, str, list]:
        return snapshot["round"], restored_note, restored_blocks

    appended = _mutate(ws, actor, do_revert)
    events.emit(ws, "curation.changed", actor, version=appended["n"], reverted_from=wanted)
    return _doc(appended)


def _fenced(text: str) -> str:
    """Indent every line of a block's body with `> `.

    A passage is someone else's text, and nothing here reads it — but it can
    still contain a line shaped exactly like one of this function's own
    headings, such as `## Written by the author`. Indenting reserves the
    heading shape for lines this function writes itself, so a kept passage
    cannot forge the provenance framing the merging agent relies on.
    """
    if not text:
        return text
    return "\n".join(f"> {line}" for line in text.splitlines())


def as_text(ws: Path) -> str:
    """Render the document for a merge prompt: each block under a heading
    naming where it came from, then the note.

    Every passage and the note are inserted verbatim — nothing here escapes,
    templates, or `.format()`s them. A `.format()`-shaped bug anywhere
    downstream would mangle a passage that contains `{prompt}`-like braces;
    this function is not that bug.
    """
    doc = read(ws)
    parts = []
    for block in doc["blocks"]:
        kind = block.get("kind")
        if kind not in KINDS:
            parts.append(f"## Unrecognized block kind {kind!r} (not attributed)")
        elif kind == "kept":
            parts.append(f"## Kept from {block.get('agent', '')} "
                         f"({block.get('section', '')}, round {block.get('round', '')})")
        else:  # kind == "mine"
            parts.append("## Written by the author")
        parts.append(_fenced(block.get("text", "")))
    if doc["note"]:
        parts.append("## Note")
        parts.append(doc["note"])
    return "\n\n".join(parts)
