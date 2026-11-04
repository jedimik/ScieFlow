"""PRISMA 2020 reporting layer, derived entirely from the provenance ledger (D10).

PRISMA 2020 is a REPORTING view, never the architecture. The architecture is the
append-only event ledger in :mod:`researcher_core.provenance`; the flow diagram and the
checklist are aggregations OVER it. No count in this module is ever stored: every number
falls out of the events each time it is asked for, so a count can never drift away from
the evidence it claims to summarize (D10). Delete a screening event and the derived flow
changes, which is the property the tests pin down.

Where :meth:`ProvenanceLedger.prisma` gives the basic identified / deduplicated / screened
/ included counts, this module extends them into the full PRISMA 2020 shape:

* per-database identified counts (from ``retrieval`` payloads);
* duplicates removed (from ``dedup_decision`` payloads);
* records screened and records excluded at the title/abstract stage;
* reports sought, reports retrieved / assessed, and reports EXCLUDED WITH REASONS at the
  full-text stage (from ``screening_decision`` payloads, resolved through ``adjudication``
  events for dual-screened records);
* studies included.

It also derives a PRISMA 2020 checklist coverage report: each relevant checklist item is
mapped to the ledger evidence that satisfies it (item 7 search strategy from the verbatim
retrieval queries, item 16a flow from the derived flow, and so on), or marked
author-supplied when no event can stand in for the author's prose.

Nothing here reads the clock. It only reads events, so replays are byte-identical (D15).

Payload conventions this module relies on (the ledger payload is open by schema, so these
are conventions rather than schema constraints, and both the M4 vocabulary and the older
``derive_prisma`` vocabulary are accepted):

* ``screening_decision``:
  ``{"record_id", "stage", "verdict"|"decision", "reason", "screener_id"}``.
  ``stage`` is normalized (title/abstract vs full-text); a missing stage is treated as
  title/abstract. ``verdict`` (or ``decision``) is normalized to ``include`` / ``exclude``;
  any other value (``maybe``, ``unclear``) is a pending record, never an exclusion.
* ``adjudication``:
  ``{"record_id", "stage", "verdict"|"resolved_verdict", "reason", "resolves": [...]}``.
  An adjudication is the resolved decision for a dual-screened record and OVERRIDES the two
  streams for that ``(record_id, stage)``.
* ``protocol_locked`` / ``amendment``: presence is the evidence for the registration and
  protocol checklist items; the payload is not otherwise interpreted here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ._ledger import ProvenanceEvent, ProvenanceLedger, derive_prisma

__all__ = [
    "FULL_TEXT",
    "TITLE_ABSTRACT",
    "ChecklistItem",
    "PrismaChecklist",
    "PrismaFlow",
    "StageFlow",
    "derive_prisma_checklist",
    "derive_prisma_flow",
    "prisma_checklist",
    "prisma_flow",
]

#: The two canonical PRISMA screening stages. Custom stages keep their own label.
TITLE_ABSTRACT = "title-abstract"
FULL_TEXT = "full-text"

#: Free-text stage labels that emitters use, mapped onto the two canonical stages. A
#: ``screening_decision`` with no stage at all is treated as title/abstract, because
#: single-stage screening is title/abstract screening.
_STAGE_SYNONYMS: dict[str, str] = {
    "title-abstract": TITLE_ABSTRACT,
    "title_abstract": TITLE_ABSTRACT,
    "titleabstract": TITLE_ABSTRACT,
    "title/abstract": TITLE_ABSTRACT,
    "title-abstract-screening": TITLE_ABSTRACT,
    "ta": TITLE_ABSTRACT,
    "title": TITLE_ABSTRACT,
    "abstract": TITLE_ABSTRACT,
    "screen": TITLE_ABSTRACT,
    "screening": TITLE_ABSTRACT,
    "full-text": FULL_TEXT,
    "full_text": FULL_TEXT,
    "fulltext": FULL_TEXT,
    "full text": FULL_TEXT,
    "ft": FULL_TEXT,
    "eligibility": FULL_TEXT,
    "full-text-review": FULL_TEXT,
    "full-text-assessment": FULL_TEXT,
}

#: Verdict spellings normalized to ``include``.
_INCLUDE_WORDS = frozenset(
    {"include", "included", "in", "yes", "keep", "eligible", "accept", "accepted"}
)
#: Verdict spellings normalized to ``exclude``.
_EXCLUDE_WORDS = frozenset(
    {"exclude", "excluded", "out", "no", "drop", "dropped", "ineligible", "reject", "rejected"}
)

#: The reason recorded when a record is excluded but no reason string was supplied.
_UNSPECIFIED_REASON = "unspecified"


def _normalize_stage(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return TITLE_ABSTRACT
    return _STAGE_SYNONYMS.get(text, text)


def _normalize_verdict(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _INCLUDE_WORDS:
        return "include"
    if text in _EXCLUDE_WORDS:
        return "exclude"
    return text


def _stage_sort_key(stage: str) -> tuple[int, str]:
    """Order stages title/abstract first, full-text second, then custom stages by name."""
    if stage == TITLE_ABSTRACT:
        return (0, "")
    if stage == FULL_TEXT:
        return (1, "")
    return (2, stage)


# ---------------------------------------------------------------------------
# Resolved decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolved:
    """The single decision for one ``(record_id, stage)`` after dual streams are collapsed.

    ``verdict`` is ``include`` / ``exclude`` / ``pending``. ``pending`` covers an
    unadjudicated conflict between streams and any non-include/exclude verdict (``maybe``):
    a pending record is NEVER counted as excluded, because an unresolved record is not a
    decision.
    """

    verdict: str
    reason: str
    conflict: bool


def _resolve_stage_decisions(
    events: Iterable[ProvenanceEvent],
) -> dict[str, dict[str, _Resolved]]:
    """Collapse the two screening streams (and adjudications) into one decision per record.

    Returns ``{stage: {record_id: _Resolved}}``. Adjudication wins over the streams for its
    ``(record_id, stage)``; otherwise the streams must agree, and a disagreement with no
    adjudication is a pending conflict.
    """
    # (stage, record_id) -> list of (verdict, reason) in append order.
    screenings: dict[tuple[str, str], list[tuple[str, str]]] = {}
    adjudications: dict[tuple[str, str], list[tuple[str, str]]] = {}

    for event in events:
        payload = event.payload
        record_id = str(payload.get("record_id") or "").strip()
        if not record_id:
            continue
        stage = _normalize_stage(payload.get("stage"))
        key = (stage, record_id)
        reason = str(payload.get("reason") or "").strip()

        if event.type == "screening_decision":
            verdict = _normalize_verdict(payload.get("verdict") or payload.get("decision"))
            screenings.setdefault(key, []).append((verdict, reason))
        elif event.type == "adjudication":
            raw = (
                payload.get("verdict")
                or payload.get("resolved_verdict")
                or payload.get("decision")
            )
            adjudications.setdefault(key, []).append((_normalize_verdict(raw), reason))

    resolved: dict[str, dict[str, _Resolved]] = {}
    for key in set(screenings) | set(adjudications):
        stage, record_id = key
        resolved.setdefault(stage, {})[record_id] = _resolve_one(
            screenings.get(key, ()), adjudications.get(key, ())
        )
    return resolved


def _resolve_one(
    screenings: Iterable[tuple[str, str]],
    adjudications: Iterable[tuple[str, str]],
) -> _Resolved:
    adj = list(adjudications)
    if adj:
        verdict, reason = adj[-1]  # the latest adjudication is the resolution
        return _Resolved(
            verdict=verdict if verdict in {"include", "exclude"} else "pending",
            reason=_pick_reason(verdict, reason, [reason]),
            conflict=False,
        )

    votes = list(screenings)
    verdicts = {verdict for verdict, _ in votes}
    if len(verdicts) == 1:
        (verdict,) = verdicts
        reasons = [reason for _, reason in votes]
        return _Resolved(
            verdict=verdict if verdict in {"include", "exclude"} else "pending",
            reason=_pick_reason(verdict, "", reasons),
            conflict=False,
        )

    # Two streams disagree and nobody adjudicated: a pending conflict, not a decision.
    return _Resolved(verdict="pending", reason="", conflict=True)


def _pick_reason(verdict: str, direct: str, candidates: Iterable[str]) -> str:
    if verdict != "exclude":
        return ""
    if direct.strip():
        return direct.strip()
    for candidate in candidates:
        if candidate.strip():
            return candidate.strip()
    return _UNSPECIFIED_REASON


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageFlow:
    """One screening stage's derived counts (title/abstract, full-text, or a custom stage)."""

    stage: str
    screened: int
    included: int
    excluded: int
    pending: int
    excluded_by_reason: dict[str, int] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "screened": self.screened,
            "included": self.included,
            "excluded": self.excluded,
            "pending": self.pending,
            "excluded_by_reason": dict(sorted(self.excluded_by_reason.items())),
        }


@dataclass(frozen=True)
class PrismaFlow:
    """The full PRISMA 2020 flow, DERIVED by aggregating events (D10). Never stored.

    The named top-level fields are the boxes of the PRISMA 2020 flow diagram; ``stages``
    carries the per-stage breakdown they are computed from. ``studies_included`` is the
    included count at the most advanced stage that was screened.
    """

    run_id: str = ""
    identified: int = 0
    identified_by_source: dict[str, int] = field(default_factory=dict)
    duplicates_removed: int = 0
    records_after_dedup: int = 0
    records_screened: int = 0
    records_excluded: int = 0
    reports_sought_for_retrieval: int = 0
    reports_not_retrieved: int = 0
    reports_assessed: int = 0
    reports_excluded: int = 0
    reports_excluded_by_reason: dict[str, int] = field(default_factory=dict)
    studies_included: int = 0
    excluded_by_reason: dict[str, int] = field(default_factory=dict)
    protocol_locked: bool = False
    amendments: int = 0
    stages: tuple[StageFlow, ...] = ()
    event_counts: dict[str, int] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "identified": self.identified,
            "identified_by_source": dict(sorted(self.identified_by_source.items())),
            "duplicates_removed": self.duplicates_removed,
            "records_after_dedup": self.records_after_dedup,
            "records_screened": self.records_screened,
            "records_excluded": self.records_excluded,
            "reports_sought_for_retrieval": self.reports_sought_for_retrieval,
            "reports_not_retrieved": self.reports_not_retrieved,
            "reports_assessed": self.reports_assessed,
            "reports_excluded": self.reports_excluded,
            "reports_excluded_by_reason": dict(sorted(self.reports_excluded_by_reason.items())),
            "studies_included": self.studies_included,
            "excluded_by_reason": dict(sorted(self.excluded_by_reason.items())),
            "protocol_locked": self.protocol_locked,
            "amendments": self.amendments,
            "stages": [stage.to_json_dict() for stage in self.stages],
            "event_counts": dict(sorted(self.event_counts.items())),
        }


def _summarize_stage(stage: str, decisions: Mapping[str, _Resolved]) -> StageFlow:
    included = 0
    excluded = 0
    pending = 0
    by_reason: dict[str, int] = {}
    for resolved in decisions.values():
        if resolved.verdict == "include":
            included += 1
        elif resolved.verdict == "exclude":
            excluded += 1
            reason = resolved.reason or _UNSPECIFIED_REASON
            by_reason[reason] = by_reason.get(reason, 0) + 1
        else:
            pending += 1
    return StageFlow(
        stage=stage,
        screened=len(decisions),
        included=included,
        excluded=excluded,
        pending=pending,
        excluded_by_reason=by_reason,
    )


def derive_prisma_flow(
    events: Iterable[ProvenanceEvent], *, run_id: str = ""
) -> PrismaFlow:
    """Aggregate events into the full PRISMA 2020 flow. The only way the flow is produced."""
    event_list = list(events)

    # Reuse the tested identified / duplicates / by-source derivation from the ledger.
    base = derive_prisma(event_list, run_id=run_id)

    resolved = _resolve_stage_decisions(event_list)
    stages = [
        _summarize_stage(stage, resolved[stage])
        for stage in sorted(resolved, key=_stage_sort_key)
    ]

    ta = next((s for s in stages if s.stage == TITLE_ABSTRACT), None)
    ft = next((s for s in stages if s.stage == FULL_TEXT), None)

    records_screened = ta.screened if ta else 0
    records_excluded = ta.excluded if ta else 0
    reports_sought = ta.included if ta else 0
    reports_assessed = ft.screened if ft else 0
    reports_not_retrieved = max(reports_sought - reports_assessed, 0)
    reports_excluded = ft.excluded if ft else 0
    reports_excluded_by_reason = dict(ft.excluded_by_reason) if ft else {}

    # Studies included: the included count at the most advanced stage that was screened.
    last_stage = stages[-1] if stages else None
    studies_included = last_stage.included if last_stage else 0

    excluded_by_reason: dict[str, int] = {}
    for stage in stages:
        for reason, count in stage.excluded_by_reason.items():
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + count

    return PrismaFlow(
        run_id=run_id,
        identified=base.identified,
        identified_by_source=dict(base.identified_by_source),
        duplicates_removed=base.duplicates_removed,
        records_after_dedup=base.deduplicated,
        records_screened=records_screened,
        records_excluded=records_excluded,
        reports_sought_for_retrieval=reports_sought,
        reports_not_retrieved=reports_not_retrieved,
        reports_assessed=reports_assessed,
        reports_excluded=reports_excluded,
        reports_excluded_by_reason=reports_excluded_by_reason,
        studies_included=studies_included,
        excluded_by_reason=excluded_by_reason,
        protocol_locked=base.event_counts.get("protocol_locked", 0) > 0,
        amendments=base.event_counts.get("amendment", 0),
        stages=tuple(stages),
        event_counts=dict(base.event_counts),
    )


def prisma_flow(ledger: ProvenanceLedger, run_id: str | None = None) -> PrismaFlow:
    """The PRISMA 2020 flow for ``run_id`` (or the whole ledger), derived from its events."""
    return derive_prisma_flow(ledger.iter_events(run_id=run_id), run_id=run_id or "")


# ---------------------------------------------------------------------------
# Checklist coverage
# ---------------------------------------------------------------------------

#: The three coverage states a checklist item can be in.
STATUS_DERIVED = "derived"  # the ledger carries evidence that satisfies the item
STATUS_MISSING = "missing"  # the item could be derived, but no evidence is present yet
STATUS_AUTHOR = "author-supplied"  # no event can stand in for the author's prose


@dataclass(frozen=True)
class ChecklistItem:
    """One PRISMA 2020 checklist item mapped to the ledger evidence (or to the author)."""

    item: str
    section: str
    name: str
    status: str
    evidence: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "section": self.section,
            "name": self.name,
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class PrismaChecklist:
    """PRISMA 2020 checklist coverage, DERIVED from the ledger (D10). Never stored."""

    run_id: str = ""
    items: tuple[ChecklistItem, ...] = ()
    coverage: dict[str, int] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "coverage": dict(sorted(self.coverage.items())),
            "items": [item.to_json_dict() for item in self.items],
        }


def derive_prisma_checklist(
    events: Iterable[ProvenanceEvent],
    *,
    run_id: str = "",
    flow: PrismaFlow | None = None,
) -> PrismaChecklist:
    """Map each relevant PRISMA 2020 item to ledger evidence, or mark it author-supplied."""
    event_list = list(events)
    if flow is None:
        flow = derive_prisma_flow(event_list, run_id=run_id)

    by_type: dict[str, int] = {}
    sources: set[str] = set()
    verbatim_queries = 0
    for event in event_list:
        by_type[event.type] = by_type.get(event.type, 0) + 1
        if event.type == "retrieval":
            if str(event.payload.get("query") or "").strip():
                verbatim_queries += 1
            sources.add(str(event.payload.get("source") or "unknown"))

    screening = by_type.get("screening_decision", 0)
    adjudication = by_type.get("adjudication", 0)
    extraction = by_type.get("record_lineage", 0) + by_type.get("artifact_hash", 0)

    items: list[ChecklistItem] = [
        _author_item("3", "Introduction", "Rationale"),
        _author_item("4", "Introduction", "Objectives"),
        _derived_item(
            "5",
            "Methods",
            "Eligibility criteria",
            flow.protocol_locked,
            f"protocol_locked event present ({by_type.get('protocol_locked', 0)})",
            "no protocol_locked event: the eligibility profile is not locked in the ledger",
        ),
        _derived_item(
            "6",
            "Methods",
            "Information sources",
            bool(sources),
            f"{len(sources)} source(s) in retrieval events: {', '.join(sorted(sources))}",
            "no retrieval events: information sources are not recorded",
        ),
        _derived_item(
            "7",
            "Methods",
            "Search strategy",
            verbatim_queries > 0,
            f"{verbatim_queries} retrieval event(s) carry a verbatim query string",
            "no retrieval events with a verbatim query string",
        ),
        _derived_item(
            "8",
            "Methods",
            "Selection process",
            screening > 0,
            f"{screening} screening_decision and {adjudication} adjudication event(s)",
            "no screening_decision events: the selection process is not recorded",
        ),
        _derived_item(
            "9",
            "Methods",
            "Data collection process",
            extraction > 0,
            f"{extraction} extraction lineage event(s)",
            "no extraction lineage events; describe the data-collection process in prose",
            missing_status=STATUS_AUTHOR,
        ),
        _derived_item(
            "16a",
            "Results",
            "Study selection (flow)",
            flow.identified > 0 or flow.records_screened > 0,
            (
                f"flow derived: {flow.identified} identified, "
                f"{flow.duplicates_removed} duplicates removed, "
                f"{flow.records_screened} screened, {flow.studies_included} included"
            ),
            "no retrieval or screening events: the flow cannot be derived",
        ),
        _derived_item(
            "16b",
            "Results",
            "Studies excluded with reasons",
            bool(flow.reports_excluded_by_reason),
            (
                f"{len(flow.reports_excluded_by_reason)} full-text exclusion reason(s) "
                f"grouped, {flow.reports_excluded} report(s) excluded"
            ),
            "no full-text exclusions with reasons recorded",
        ),
        _author_item("23a", "Discussion", "Limitations of the evidence"),
        _derived_item(
            "24a",
            "Other information",
            "Registration and protocol",
            flow.protocol_locked,
            f"protocol_locked event present ({by_type.get('protocol_locked', 0)})",
            "no protocol_locked event: registration and protocol are not recorded",
        ),
        _derived_item(
            "24b",
            "Other information",
            "Amendments to the protocol",
            flow.amendments > 0,
            f"{flow.amendments} amendment event(s) in the ledger",
            "no amendment events (report 'no amendments' if the protocol was unchanged)",
            missing_status=STATUS_AUTHOR,
        ),
    ]

    coverage: dict[str, int] = {}
    for item in items:
        coverage[item.status] = coverage.get(item.status, 0) + 1

    return PrismaChecklist(run_id=run_id, items=tuple(items), coverage=coverage)


def _author_item(item: str, section: str, name: str) -> ChecklistItem:
    return ChecklistItem(
        item=item,
        section=section,
        name=name,
        status=STATUS_AUTHOR,
        evidence="author-supplied: not derivable from the ledger",
    )


def _derived_item(
    item: str,
    section: str,
    name: str,
    satisfied: bool,
    satisfied_evidence: str,
    missing_evidence: str,
    *,
    missing_status: str = STATUS_MISSING,
) -> ChecklistItem:
    if satisfied:
        return ChecklistItem(item, section, name, STATUS_DERIVED, satisfied_evidence)
    return ChecklistItem(item, section, name, missing_status, missing_evidence)


def prisma_checklist(ledger: ProvenanceLedger, run_id: str | None = None) -> PrismaChecklist:
    """The PRISMA 2020 checklist coverage for ``run_id`` (or the whole ledger)."""
    events = ledger.events(run_id=run_id)
    flow = derive_prisma_flow(events, run_id=run_id or "")
    return derive_prisma_checklist(events, run_id=run_id or "", flow=flow)
