"""The systematic-review ledger adapter (M-H plan 5.3, M-H-D2).

Every sr.* event is written into the run's events.jsonl through core/events.emit. A detail event
counts only when a later sr.committed event names its op. view() returns a read-only ledger that
yields the committed detail events mapped back to the vendored types, plus protocol_locked and
amendment events synthesized from the design lock ledger (never written to events.jsonl).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from scieflow._vendor.researcher_core._ledger import ProvenanceEvent, ProvenanceLedger, RunContext
from scieflow.core import events
from scieflow.core.design import lock
from scieflow.research.sr.errors import SRError

VENDORED_TYPES = ("retrieval", "dedup_decision", "screening_decision", "adjudication")
DETAIL_TYPES = ("retrieval", "dedup_decision", "screening_decision", "adjudication", "appraisal")
COMMITTED = "sr.committed"
KINDS = ("search", "screening", "adjudication", "rob", "grade")
RESERVED = frozenset({"ws", "type_", "actor", "op"})
WRITERS = ("human", "agent")


def run_name(ws) -> str:
    """The run name the adapter stamps on events: the workspace slug (status ids are not names)."""
    return Path(ws).name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _reserved(keys) -> None:
    clash = sorted(RESERVED & set(keys))
    if clash:
        raise SRError(f"payload key {clash[0]!r} is reserved (events.emit parameters and op)")


class EventsLedger(ProvenanceLedger):
    """The write side: the vendored writers call append(); commit() closes one operation."""

    def __init__(self, ws, *, actor: str, op: str, extra: dict | None = None) -> None:
        if actor not in WRITERS:
            raise SRError(f"actor must be one of: {', '.join(WRITERS)}")
        if not op:
            raise SRError("an operation needs an op id")
        self.ws = Path(ws)
        self.actor = actor
        self.op = op
        self.extra = dict(extra or {})
        _reserved(self.extra)
        self.run = run_name(self.ws)

    def append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        if event.type not in VENDORED_TYPES:
            raise SRError(f"the sr ledger writes only {', '.join(VENDORED_TYPES)}; refused {event.type!r}")
        payload = dict(event.payload)
        _reserved(payload)
        data = {**payload, **self.extra, "op": self.op}
        emitted = events.emit(self.ws, f"sr.{event.type}", self.actor, **data)
        return ProvenanceEvent(run_id=self.run, type=event.type, ts=emitted["ts"], payload=data,
                               source_response_hashes=event.source_response_hashes,
                               event_id=emitted["id"])

    def commit(self, kind: str, **data) -> dict:
        """The last event of this operation: it names op and kind and makes the group count."""
        if kind not in KINDS:
            raise SRError(f"commit kind must be one of: {', '.join(KINDS)}")
        _reserved(data)
        payload = {**self.extra, **data, "op": self.op, "kind": kind}
        return events.emit(self.ws, COMMITTED, self.actor, **payload)

    def run_context(self, extra: dict | None = None) -> RunContext:
        return RunContext(run_id=self.run, extra={**self.extra, **dict(extra or {})})

    def iter_events(self, run_id: str | None = None, type: str | None = None) -> Iterator[ProvenanceEvent]:
        return view(self.ws).iter_events(run_id=run_id, type=type)


class View(ProvenanceLedger):
    """Read-only: committed sr.* detail events, plus protocol_locked and amendment from the lock."""

    def __init__(self, ws) -> None:
        self.ws = Path(ws)
        self.run = run_name(self.ws)
        self.uncommitted_ops: list[str] = []
        self._events = self._build()

    def _build(self) -> list[ProvenanceEvent]:
        out: list[ProvenanceEvent] = []
        ledger = lock.read_ledger(self.ws)
        if ledger.problem:
            raise SRError(f"the design lock ledger does not verify: {ledger.problem}")
        for index, record in enumerate(ledger.records):
            kind = "protocol_locked" if index == 0 else "amendment"
            out.append(ProvenanceEvent(
                run_id=self.run, type=kind, ts=str(record.get("approved_at") or ""),
                payload={"version": record["seq"], "content_hash": record["content_hash"]},
                event_id=f"design-v{record['seq']}"))
        rows = events.read(self.ws)
        commit_at: dict = {}
        for index, row in enumerate(rows):
            if row.get("type") != COMMITTED:
                continue
            op = (row.get("data") or {}).get("op")
            if op in commit_at:
                raise SRError(f"duplicate sr.committed for op {op}: the sr ledger is invalid")
            commit_at[op] = index
        pending: list[str] = []
        for index, row in enumerate(rows):
            kind = str(row.get("type", ""))
            if not kind.startswith("sr.") or kind == COMMITTED:
                continue
            data = dict(row.get("data") or {})
            op = data.get("op")
            if commit_at.get(op, -1) <= index:
                if op not in pending:
                    pending.append(op)
                continue
            vendored = kind[3:]
            if vendored not in DETAIL_TYPES:
                continue
            out.append(ProvenanceEvent(run_id=self.run, type=vendored, ts=str(row.get("ts", "")),
                                       payload=data, event_id=str(row.get("id", ""))))
        self.uncommitted_ops = pending
        return out

    def iter_events(self, run_id: str | None = None, type: str | None = None) -> Iterator[ProvenanceEvent]:
        # One ledger holds one run: a run_id argument is accepted and ignored (plan 5.3).
        for event in self._events:
            if type is not None and event.type != type:
                continue
            yield event

    def append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        raise SRError("the sr view is read-only; write through EventsLedger")


def view(ws) -> View:
    return View(ws)


def run_context(ws, extra: dict | None = None) -> RunContext:
    """The RunContext the vendored writers take: the run name and the extra keys."""
    return RunContext(run_id=run_name(ws), extra=dict(extra or {}))
