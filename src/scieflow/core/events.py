"""Per-run event log — workspace/<slug>/events.jsonl, one JSON object per line.

status.yml is the snapshot; events are the history: what happened, when, and
who did it. Append-only under a lock, so the web app, the CLI and agents can
all write at once. A dashboard replays it; `scieflow run events --follow`
tails it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import store

EVENTS_FILE = "events.jsonl"
ACTORS = frozenset({"human", "agent", "system"})
TYPES = frozenset({
    "run.created", "run.resumed",
    "phase.pending", "phase.started", "phase.done", "phase.failed",
    "iteration.advanced", "checkpoint", "budget.recorded",
    "job.queued", "job.started", "job.finished", "job.failed", "job.timeout",
    "job.cancelled", "job.lost", "job.refused", "sandbox.disabled",
    "gate.opened", "gate.answered", "gate.withdrawn",
    "integration.call", "sync.pushed", "sync.pulled",
})


def _run_id(ws: Path) -> str:
    st = store.read_yaml(Path(ws) / "status.yml") or {}
    return str(st.get("id") or Path(ws).name)


def emit(ws: Path, type_: str, actor: str = "system", **data) -> dict:
    if type_ not in TYPES and not type_.startswith("note."):
        raise ValueError(f"unknown event type {type_!r} (free-form events use 'note.<name>')")
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r} (one of {', '.join(sorted(ACTORS))})")
    event = {
        "id": store.new_id(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run": _run_id(ws),
        "slug": Path(ws).name,
        "type": type_,
        "actor": actor,
        "data": data,
    }
    store.append_jsonl(Path(ws) / EVENTS_FILE, event)
    return event


def _matches(event_type: str, types: tuple[str, ...]) -> bool:
    return any(event_type == t or (t.endswith("*") and event_type.startswith(t[:-1]))
               for t in types)


def read(ws: Path, since: str | None = None, types: tuple[str, ...] = ()) -> list[dict]:
    """Events in file order; `since` = an event id, returns only what came after it."""
    evs = store.read_jsonl(Path(ws) / EVENTS_FILE)
    if since is not None:
        ids = [e.get("id") for e in evs]
        if since in ids:
            evs = evs[ids.index(since) + 1:]
    if types:
        evs = [e for e in evs if _matches(e.get("type", ""), types)]
    return evs


def follow(ws: Path, since: str | None = None, poll: float = 1.0,
           stop: Callable[[], bool] = lambda: False) -> Iterator[dict]:
    last = since
    while not stop():
        for event in read(ws, since=last):
            last = event["id"]
            yield event
            if stop():
                return
        time.sleep(poll)
