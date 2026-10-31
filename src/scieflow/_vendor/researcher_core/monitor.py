"""Living-review monitoring: saved searches and diff-on-rerun (M4.11).

A living review is a systematic review that is re-run on a schedule instead of redone
from scratch. This module holds the piece that makes "re-run" mean "only the new records":
a saved monitoring state (the verbatim per-database strategies, the last-run timestamp, and
the list of record ids already seen) plus a diff that, on each rerun, reports ONLY the
records that were not seen before.

Three properties are load-bearing, so they live here rather than in the discipline of
callers:

1. **The diff is auditable offline.** :func:`run_monitor` executes strategies through a
   caller-supplied ``executor``. In production the CLI passes a search-layer executor that
   replays from snapshots (D15); tests pass a plain function returning canned ids. Either
   way the diff runs over recorded results, never a live call made from inside this module.

2. **``ts`` is caller-supplied, never read from the clock** (D19, D15). Nothing here calls
   ``time.time()`` or ``datetime.now()``: a self-generated timestamp would make two replays
   of the same rerun differ. The last-run timestamp is validated with the same
   :func:`researcher_core.provenance.normalize_ts` the ledger uses, and returned verbatim.

3. **The seen-id list only grows.** :class:`MonitorState` is immutable; :func:`update_state`
   returns a NEW state with the unseen ids appended (order-stable, deduplicated) and the
   last-run timestamp advanced. There is no path that shrinks or reorders the seen list, so
   a record reported as new in one rerun is never reported as new again.

The new batch a rerun returns is exactly what the M4.4 screening streams consume next: the
same locked protocol, a new set of records to screen. This module produces the batch and,
optionally, the ``retrieval`` provenance events that record it (:func:`retrieval_events`);
writing them to the ledger is the caller's job.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ._deps import ProvenanceEvent, RunContext, normalize_ts

__all__ = [
    "MONITOR_SCHEMA_VERSION",
    "MonitorError",
    "MonitorRerun",
    "MonitorState",
    "SearchStrategy",
    "StrategyExecutor",
    "default_monitor_path",
    "diff_new",
    "extract_ids",
    "load_monitor_state",
    "record_id",
    "retrieval_events",
    "run_monitor",
    "save_monitor_state",
    "update_state",
]

#: Version of the monitoring-state record shape. Bump the minor for additive fields, the
#: major for anything that invalidates a stored ``monitoring`` file.
MONITOR_SCHEMA_VERSION = "1.0"

#: What :func:`run_monitor` calls per strategy: given one strategy, return the records (or
#: bare ids) that strategy currently matches. The CLI passes a snapshot-replaying search
#: executor; tests pass a function returning canned lists. The module never fetches itself.
StrategyExecutor = Callable[["SearchStrategy"], Iterable[Any]]


class MonitorError(RuntimeError):
    """A monitoring state or diff operation was rejected."""


# ---------------------------------------------------------------------------
# Id extraction
# ---------------------------------------------------------------------------


def record_id(record: Any) -> str:
    """The stable id of one returned record.

    Accepts the three shapes an executor might hand back: a bare id string, a mapping with
    an ``id`` (falling back to ``DOI``), or any object exposing an ``id`` attribute (a
    :class:`researcher_core.model.CSLRecord`, for one). Anything else is stringified, so the
    diff still has something stable to compare.
    """
    if isinstance(record, str):
        return record.strip()
    if isinstance(record, Mapping):
        value = record.get("id") or record.get("DOI") or record.get("doi") or ""
        return str(value).strip()
    ident = getattr(record, "id", None)
    if ident:
        return str(ident).strip()
    return str(record).strip()


def extract_ids(records: Iterable[Any]) -> list[str]:
    """Ids of ``records``, order-stable and deduplicated (first occurrence wins).

    Empty ids are dropped: a record with no id cannot be diffed, and silently keeping it
    would let the same unidentifiable record be reported new on every rerun.
    """
    out: list[str] = []
    seen: set[str] = set()
    for record in records:
        ident = record_id(record)
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


# ---------------------------------------------------------------------------
# The pure diff
# ---------------------------------------------------------------------------


def diff_new(current_ids: Iterable[str], seen_ids: Iterable[str]) -> list[str]:
    """The ids in ``current_ids`` that are not in ``seen_ids``.

    Order-stable (the order they appear in ``current_ids``) and deduplicated, so a record
    that a rerun returns twice, or that two strategies both match, is reported once. This is
    the whole of "report only the new records"; everything else in the module is bookkeeping
    around it.
    """
    already = {str(i).strip() for i in seen_ids if str(i).strip()}
    out: list[str] = []
    emitted: set[str] = set()
    for raw in current_ids:
        ident = str(raw).strip()
        if not ident or ident in already or ident in emitted:
            continue
        emitted.add(ident)
        out.append(ident)
    return out


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchStrategy:
    """One verbatim per-database strategy (M4.3): the exact query string for one source.

    ``query`` is stored and replayed byte-for-byte; it is never paraphrased. ``label`` is a
    stable, human-readable handle for the strategy inside a rerun report (defaulting to the
    source), because two strategies can target the same source.
    """

    source: str
    query: str
    endpoint: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        source = str(self.source).strip()
        if not source:
            raise MonitorError("SearchStrategy.source is required.")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "query", str(self.query))
        object.__setattr__(self, "endpoint", str(self.endpoint or "").strip())
        object.__setattr__(self, "label", str(self.label or "").strip() or source)

    def to_json_dict(self) -> dict[str, str]:
        out = {"source": self.source, "query": self.query}
        if self.endpoint:
            out["endpoint"] = self.endpoint
        if self.label != self.source:
            out["label"] = self.label
        return out

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any]) -> SearchStrategy:
        return cls(
            source=str(data.get("source") or ""),
            query=str(data.get("query") or ""),
            endpoint=str(data.get("endpoint") or ""),
            label=str(data.get("label") or ""),
        )


# ---------------------------------------------------------------------------
# The saved monitoring state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorState:
    """A saved search: what to run, when it last ran, and which records it has already seen.

    Immutable. Mutation happens only through :func:`update_state`, which returns a new state
    with the seen-id list grown; there is no in-place setter, so the seen list cannot shrink
    or be reordered by accident. ``last_run`` is always a caller-supplied timestamp (or empty
    before the first run), never one this module generated.
    """

    monitor_id: str
    strategies: tuple[SearchStrategy, ...] = ()
    seen_ids: tuple[str, ...] = ()
    last_run: str = ""
    run_count: int = 0
    schema_version: str = MONITOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        monitor_id = str(self.monitor_id).strip()
        if not monitor_id:
            raise MonitorError("monitor_id is required: it names the saved search.")
        object.__setattr__(self, "monitor_id", monitor_id)
        object.__setattr__(self, "strategies", tuple(self.strategies))

        # Deduplicate the seen list defensively while preserving order, so a hand-edited or
        # legacy state file cannot smuggle a duplicate id into the diff baseline.
        deduped: list[str] = []
        seen: set[str] = set()
        for raw in self.seen_ids:
            ident = str(raw).strip()
            if ident and ident not in seen:
                seen.add(ident)
                deduped.append(ident)
        object.__setattr__(self, "seen_ids", tuple(deduped))

        object.__setattr__(self, "last_run", normalize_ts(self.last_run) if self.last_run else "")
        object.__setattr__(self, "run_count", max(int(self.run_count), 0))
        object.__setattr__(
            self, "schema_version", str(self.schema_version or MONITOR_SCHEMA_VERSION)
        )

    @property
    def sources(self) -> list[str]:
        """The distinct sources this state monitors, in first-appearance order."""
        out: list[str] = []
        seen: set[str] = set()
        for strategy in self.strategies:
            if strategy.source not in seen:
                seen.add(strategy.source)
                out.append(strategy.source)
        return out

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "monitor_id": self.monitor_id,
            "strategies": [s.to_json_dict() for s in self.strategies],
            "seen_ids": list(self.seen_ids),
            "last_run": self.last_run,
            "run_count": self.run_count,
        }

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any]) -> MonitorState:
        strategies_raw = data.get("strategies") or []
        if not isinstance(strategies_raw, Sequence) or isinstance(strategies_raw, (str, bytes)):
            raise MonitorError("strategies must be a list of strategy objects.")
        seen_raw = data.get("seen_ids") or []
        if not isinstance(seen_raw, Sequence) or isinstance(seen_raw, (str, bytes)):
            raise MonitorError("seen_ids must be a list of record ids.")
        return cls(
            monitor_id=str(data.get("monitor_id") or ""),
            strategies=tuple(SearchStrategy.from_json_dict(s) for s in strategies_raw),
            seen_ids=tuple(str(i) for i in seen_raw),
            last_run=str(data.get("last_run") or ""),
            run_count=int(data.get("run_count") or 0),
            schema_version=str(data.get("schema_version") or MONITOR_SCHEMA_VERSION),
        )

    def serialize(self) -> str:
        """The on-disk text: pretty-printed JSON, LF-terminated, key order preserved.

        Keys are NOT sorted: ``seen_ids`` order is meaningful (the diff is order-stable), and
        the top-level key order is fixed by :meth:`to_json_dict`. Round-trip equality, not
        byte-sorted canonicality, is what a monitoring file needs.
        """
        return json.dumps(self.to_json_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def default_monitor_path(manuscript_dir: Path | str = "manuscript") -> Path:
    """``manuscript/monitoring.json`` under a manuscript directory.

    JSON, not YAML: the core runtime deps are httpx, rapidfuzz, and platformdirs only, so a
    runtime module cannot import a YAML parser. The systematic-review skill layer may present
    the same content as ``monitoring.yaml`` to the user; the deterministic on-disk form the
    kernel reads and writes is this JSON file.
    """
    return Path(manuscript_dir) / "monitoring.json"


def save_monitor_state(path: Path | str, state: MonitorState) -> Path:
    """Write ``state`` to ``path`` as JSON. Returns the path written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(state.serialize())
    return target


def load_monitor_state(path: Path | str) -> MonitorState:
    """Read a monitoring state from ``path``."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MonitorError(f"Cannot read monitoring state at {target}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MonitorError(f"Monitoring state at {target} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise MonitorError(f"Monitoring state at {target} must be a JSON object.")
    return MonitorState.from_json_dict(data)


# ---------------------------------------------------------------------------
# State transition
# ---------------------------------------------------------------------------


def update_state(state: MonitorState, new_ids: Iterable[str], ts: str) -> MonitorState:
    """Append ``new_ids`` to the seen list and advance ``last_run`` to ``ts``.

    Returns a NEW state; the input is untouched. The seen list grows monotonically: only ids
    not already present are appended, in the order given, so it never shrinks or reorders.
    ``ts`` is caller-supplied and validated as an RFC 3339 timestamp; this function never
    reads the clock (D19, D15).
    """
    checked_ts = normalize_ts(ts)
    already = set(state.seen_ids)
    appended: list[str] = list(state.seen_ids)
    for raw in new_ids:
        ident = str(raw).strip()
        if ident and ident not in already:
            already.add(ident)
            appended.append(ident)
    return replace(
        state,
        seen_ids=tuple(appended),
        last_run=checked_ts,
        run_count=state.run_count + 1,
    )


# ---------------------------------------------------------------------------
# The rerun
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorRerun:
    """The result of one diff-on-rerun.

    ``current_ids`` is everything the strategies matched this run (order-stable across
    strategies, deduplicated). ``new_ids`` is the subset not in the pre-run seen list: the
    new screening batch. ``state`` is the post-run state, with ``new_ids`` folded into
    ``seen_ids`` and ``last_run`` advanced to ``ts``. ``per_strategy`` records what each
    strategy returned, so the diff is auditable strategy by strategy.
    """

    monitor_id: str
    ts: str
    current_ids: tuple[str, ...]
    new_ids: tuple[str, ...]
    per_strategy: dict[str, list[str]] = field(default_factory=dict)
    state: MonitorState | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "monitor_id": self.monitor_id,
            "ts": self.ts,
            "current_ids": list(self.current_ids),
            "new_ids": list(self.new_ids),
            "per_strategy": {k: list(v) for k, v in self.per_strategy.items()},
        }


def run_monitor(
    state: MonitorState,
    executor: StrategyExecutor,
    *,
    ts: str,
) -> MonitorRerun:
    """Execute every saved strategy through ``executor`` and diff against the seen list.

    ``executor`` maps one :class:`SearchStrategy` to the records (or bare ids) it currently
    matches. This module never fetches: production passes a snapshot-replaying search
    executor (D15), tests pass a function returning canned lists, and the diff runs over
    whichever recorded results come back. ``ts`` is the caller's timestamp for this run and
    is validated but never generated.

    Returns a :class:`MonitorRerun` whose ``new_ids`` is the batch the caller feeds into
    screening and whose ``state`` is the advanced state to persist.
    """
    checked_ts = normalize_ts(ts)
    per_strategy: dict[str, list[str]] = {}
    ordered_ids: list[str] = []
    combined_seen: set[str] = set()

    for index, strategy in enumerate(state.strategies):
        label = _unique_label(strategy.label, per_strategy, index)
        ids = extract_ids(executor(strategy))
        per_strategy[label] = ids
        for ident in ids:
            if ident not in combined_seen:
                combined_seen.add(ident)
                ordered_ids.append(ident)

    new_ids = diff_new(ordered_ids, state.seen_ids)
    advanced = update_state(state, new_ids, checked_ts)
    return MonitorRerun(
        monitor_id=state.monitor_id,
        ts=checked_ts,
        current_ids=tuple(ordered_ids),
        new_ids=tuple(new_ids),
        per_strategy=per_strategy,
        state=advanced,
    )


def _unique_label(label: str, taken: Mapping[str, Any], index: int) -> str:
    """A per-strategy report label that stays distinct when strategies share a source."""
    if label not in taken:
        return label
    return f"{label}#{index}"


# ---------------------------------------------------------------------------
# Optional provenance bridge
# ---------------------------------------------------------------------------


def retrieval_events(
    rerun: MonitorRerun,
    run_context: RunContext,
    *,
    source_response_hashes: Mapping[str, Iterable[str]] | None = None,
) -> list[ProvenanceEvent]:
    """Build one ``retrieval`` event per strategy of a rerun, ready for the ledger.

    The caller (the CLI orchestrator) writes these; this helper only constructs them, so the
    module stays a pure importable API with no ledger of its own. Each event carries the
    strategy's verbatim query, its source, and the ids it returned this run, with
    ``rerun.ts`` as the caller-supplied timestamp (D19). ``source_response_hashes`` maps a
    strategy label to the snapshot hashes that produced it, when the search layer has them.
    """
    hashes = source_response_hashes or {}
    events: list[ProvenanceEvent] = []
    labels = list(rerun.per_strategy)
    for label in labels:
        ids = rerun.per_strategy[label]
        source, query = _label_source_query(label, rerun)
        events.append(
            run_context.retrieval(
                rerun.ts,
                source=source,
                query=query,
                record_ids=ids,
                source_response_hashes=tuple(hashes.get(label, ())),
                extra={"monitor_id": rerun.monitor_id, "strategy_label": label},
            )
        )
    return events


def _label_source_query(label: str, rerun: MonitorRerun) -> tuple[str, str]:
    """Recover the (source, query) a rerun label came from, from its post-run state."""
    state = rerun.state
    if state is not None:
        for index, strategy in enumerate(state.strategies):
            if strategy.label == label or f"{strategy.label}#{index}" == label:
                return strategy.source, strategy.query
    return label, ""
