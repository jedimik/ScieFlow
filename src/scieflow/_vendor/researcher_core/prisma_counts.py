"""PRISMA counts: a verbatim excerpt of researcher v1.0.0 provenance.py, lines 527-625 (MIT; see VENDORED.md).
The ledger itself is not vendored: ScieFlow's ledger interface is _ledger.py. Nothing else from
provenance.py is vendored here."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PrismaCounts:
    """PRISMA flow numbers DERIVED by aggregating events (D10). Never stored, never mutable.

    ``identified`` counts records as retrieved, duplicates included (that is what PRISMA
    means by "records identified from databases"). ``duplicates_removed`` counts the
    distinct records that dedup collapsed away, and ``deduplicated`` is the difference:
    "records after duplicates removed".

    ``screened``, ``included``, and ``excluded`` derive from ``screening_decision`` events,
    which M2 does not emit; they are zero until M4 emits them.
    """

    run_id: str = ""
    identified: int = 0
    identified_by_source: dict[str, int] = field(default_factory=dict)
    duplicates_removed: int = 0
    deduplicated: int = 0
    screened: int = 0
    included: int = 0
    excluded: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "identified": self.identified,
            "identified_by_source": dict(sorted(self.identified_by_source.items())),
            "duplicates_removed": self.duplicates_removed,
            "deduplicated": self.deduplicated,
            "screened": self.screened,
            "included": self.included,
            "excluded": self.excluded,
            "event_counts": dict(sorted(self.event_counts.items())),
        }


def derive_prisma(events: Iterable[ProvenanceEvent], *, run_id: str = "") -> PrismaCounts:
    """Aggregate events into PRISMA counts. The only way counts are ever produced."""
    identified = 0
    by_source: dict[str, int] = {}
    removed_ids: set[str] = set()
    removed_countless = 0
    screened_ids: set[str] = set()
    included_ids: set[str] = set()
    excluded_ids: set[str] = set()
    event_counts: dict[str, int] = {}

    for event in events:
        event_counts[event.type] = event_counts.get(event.type, 0) + 1
        payload = event.payload

        if event.type == "retrieval":
            ids = payload.get("record_ids")
            if isinstance(ids, Sequence) and not isinstance(ids, (str, bytes)):
                count = len(ids)
            else:
                count = _as_count(payload.get("record_count"))
            identified += count
            source = str(payload.get("source") or "unknown")
            by_source[source] = by_source.get(source, 0) + count

        elif event.type == "dedup_decision":
            losers = payload.get("losers")
            if isinstance(losers, Sequence) and not isinstance(losers, (str, bytes)):
                removed_ids.update(str(loser) for loser in losers)
            else:
                removed_countless += _as_count(payload.get("removed_count"))

        elif event.type == "screening_decision":
            record_id = str(payload.get("record_id") or "")
            decision = str(payload.get("decision") or "").lower()
            if record_id:
                screened_ids.add(record_id)
                if decision in {"include", "included"}:
                    included_ids.add(record_id)
                elif decision in {"exclude", "excluded"}:
                    excluded_ids.add(record_id)

    duplicates_removed = len(removed_ids) + removed_countless
    return PrismaCounts(
        run_id=run_id,
        identified=identified,
        identified_by_source=by_source,
        duplicates_removed=duplicates_removed,
        deduplicated=max(identified - duplicates_removed, 0),
        screened=len(screened_ids),
        included=len(included_ids),
        excluded=len(excluded_ids),
        event_counts=event_counts,
    )


def _as_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(count, 0)
