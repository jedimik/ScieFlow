"""The ledger interface the vendored screening and PRISMA modules run on (M-H t1, plan 5.3-5.4).

ScieFlow-authored, not pinned (like _deps.py). researcher's SQLite provenance ledger (provenance.py)
is not vendored: the systematic-review ledger is ScieFlow's events.jsonl, written and read through
scieflow.research.sr.ledger (M-H-D2). ``screen.py`` and ``sr_prisma.py`` need only the small surface
defined here: a frozen event record, a run context that builds events from caller-supplied
timestamps, the abstract ledger base (it refuses to write or read and names the ScieFlow
replacement), and ``MemoryLedger``, a list-backed ledger for tests and the evaluation harness.

It imports only the standard library and this package's ``prisma_counts`` excerpt, and it never
reads the clock (D15): every ``ts`` is supplied by the caller.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .prisma_counts import PrismaCounts, derive_prisma

__all__ = [
    "LedgerError",
    "MemoryLedger",
    "PrismaCounts",
    "ProvenanceEvent",
    "ProvenanceLedger",
    "RunContext",
    "derive_prisma",
]

REPLACEMENT = "scieflow.research.sr.ledger (EventsLedger over events.jsonl)"


class LedgerError(RuntimeError):
    """A ledger refused a write: a duplicate event id."""


def _ts_text(ts: str | datetime) -> str:
    return ts.isoformat() if isinstance(ts, datetime) else str(ts)


@dataclass(frozen=True)
class ProvenanceEvent:
    """One event of the ledger. Immutable; ``ts`` is the caller's timestamp text."""

    run_id: str
    type: str
    ts: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_response_hashes: tuple[str, ...] = ()
    event_id: str = ""


@dataclass(frozen=True)
class RunContext:
    """Binds ``run_id`` and the ``extra`` keys (for example ``batch``, ``gate``) that every event of
    one ScieFlow operation carries. The payload keys of ``extra`` win over the event's own keys."""

    run_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def event(
        self,
        type: str,
        payload: Mapping[str, Any],
        ts: str | datetime,
        *,
        source_response_hashes: Iterable[str] = (),
        event_id: str = "",
    ) -> ProvenanceEvent:
        return ProvenanceEvent(
            run_id=str(self.run_id),
            type=str(type),
            ts=_ts_text(ts),
            payload={**dict(payload), **dict(self.extra)},
            source_response_hashes=tuple(str(h) for h in source_response_hashes),
            event_id=str(event_id),
        )

    def retrieval(
        self,
        ts: str | datetime,
        *,
        source: str,
        query: str,
        record_ids: Sequence[str] = (),
        record_count: int | None = None,
        source_response_hashes: Iterable[str] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> ProvenanceEvent:
        """One source answered one query with N records. Feeds PRISMA "identified"."""
        payload: dict[str, Any] = {
            "source": source,
            "query": query,
            "record_count": int(record_count if record_count is not None else len(record_ids)),
        }
        if record_ids:
            payload["record_ids"] = [str(r) for r in record_ids]
        if extra:
            payload.update(dict(extra))
        return self.event("retrieval", payload, ts, source_response_hashes=source_response_hashes)

    def dedup_decision(
        self,
        ts: str | datetime,
        *,
        winner: str,
        losers: Sequence[str],
        reason: str,
        similarity: float | None = None,
        source_response_hashes: Iterable[str] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> ProvenanceEvent:
        """N records collapsed into one. Feeds PRISMA "duplicates removed"."""
        payload: dict[str, Any] = {
            "winner": str(winner),
            "losers": [str(loser) for loser in losers],
            "reason": reason,
        }
        if similarity is not None:
            payload["similarity"] = float(similarity)
        if extra:
            payload.update(dict(extra))
        return self.event("dedup_decision", payload, ts, source_response_hashes=source_response_hashes)


class ProvenanceLedger:
    """The read and write surface the vendored code calls. The base refuses every operation;
    the ScieFlow ledger (``REPLACEMENT``) or :class:`MemoryLedger` implements it."""

    def append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        raise NotImplementedError(f"ledger writes go through {REPLACEMENT}; this base class does not write")

    def append_many(self, events: Iterable[ProvenanceEvent]) -> list[ProvenanceEvent]:
        return [self.append(event) for event in events]

    def iter_events(self, run_id: str | None = None, type: str | None = None) -> Iterator[ProvenanceEvent]:
        raise NotImplementedError(f"ledger reads go through {REPLACEMENT}; this base class does not read")

    def events(self, run_id: str | None = None, type: str | None = None) -> list[ProvenanceEvent]:
        return list(self.iter_events(run_id=run_id, type=type))

    def prisma(self, run_id: str | None = None) -> PrismaCounts:
        """PRISMA counts derived from the events each time, never stored (D10)."""
        return derive_prisma(self.events(run_id=run_id), run_id=run_id or "")

    def close(self) -> None:
        """Nothing to release in the base class."""


class MemoryLedger(ProvenanceLedger):
    """A list-backed ledger in append order. An event without an id gets
    ``sha256(canonical JSON of (sequence, type, payload, ts))``, so two identical builds have
    identical ids. A duplicate id is refused."""

    def __init__(self) -> None:
        self._events: list[ProvenanceEvent] = []
        self._ids: set[str] = set()

    def append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        sequence = len(self._events) + 1
        event_id = event.event_id or hashlib.sha256(
            json.dumps(
                [sequence, event.type, event.payload, event.ts],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if event_id in self._ids:
            raise LedgerError(f"duplicate event id {event_id}")
        stored = replace(event, event_id=event_id)
        self._events.append(stored)
        self._ids.add(event_id)
        return stored

    def iter_events(self, run_id: str | None = None, type: str | None = None) -> Iterator[ProvenanceEvent]:
        for event in self._events:
            if run_id is not None and event.run_id != run_id:
                continue
            if type is not None and event.type != type:
                continue
            yield event
