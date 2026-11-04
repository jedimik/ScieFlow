"""Tests for the PRISMA 2020 reporting layer (:mod:`researcher_core.sr_prisma`).

The load-bearing property is D10: every count is DERIVED by aggregating ledger events and
none is stored. The tests pin that down three ways the milestone requires:

* hand-recomputing the flow from the raw events matches the derived flow exactly;
* DELETING a screening event changes the derived flow (proving derivation, not storage);
* full-text exclusion reasons are grouped and counted.

Everything runs offline: the events are built by hand, no network and no snapshots needed.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_sr_prisma.py. Imports are rewritten to
# scieflow._vendor.researcher_core; the ledger fixture is MemoryLedger (the SQLite ledger is not
# vendored, see _ledger.py). Test bodies are unchanged except the removals listed below. See VENDORED.md.
# Removed upstream tests: none.

from __future__ import annotations

from pathlib import Path

import pytest

from scieflow._vendor.researcher_core._ledger import MemoryLedger, ProvenanceEvent, RunContext
from scieflow._vendor.researcher_core.sr_prisma import (
    FULL_TEXT,
    STATUS_AUTHOR,
    STATUS_DERIVED,
    STATUS_MISSING,
    TITLE_ABSTRACT,
    PrismaChecklist,
    PrismaFlow,
    derive_prisma_checklist,
    derive_prisma_flow,
    prisma_checklist,
    prisma_flow,
)

TS1 = "2026-07-14T12:00:00Z"
TS2 = "2026-07-14T12:00:05Z"
TS3 = "2026-07-14T12:00:09Z"
TS4 = "2026-07-14T12:00:12Z"

RUN_ID = "run-2026-07-14-sr"


@pytest.fixture()
def run() -> RunContext:
    return RunContext(run_id=RUN_ID)


@pytest.fixture()
def ledger():
    instance = MemoryLedger()
    try:
        yield instance
    finally:
        instance.close()


def _screen(
    run: RunContext,
    ts: str,
    *,
    record_id: str,
    verdict: str,
    stage: str,
    reason: str = "",
    screener_id: str = "s1",
) -> ProvenanceEvent:
    payload = {
        "record_id": record_id,
        "stage": stage,
        "verdict": verdict,
        "screener_id": screener_id,
    }
    if reason:
        payload["reason"] = reason
    return run.event("screening_decision", payload, ts)


def _adjudicate(
    run: RunContext,
    ts: str,
    *,
    record_id: str,
    verdict: str,
    stage: str,
    reason: str = "",
    resolves: list[str] | None = None,
) -> ProvenanceEvent:
    payload: dict[str, object] = {
        "record_id": record_id,
        "stage": stage,
        "verdict": verdict,
        "resolves": resolves or [],
    }
    if reason:
        payload["reason"] = reason
    return run.event("adjudication", payload, ts)


def _seed(run: RunContext) -> list[ProvenanceEvent]:
    """A small but complete review run.

    Identified: openalex 3 + crossref 2 = 5 records, one shared duplicate removed -> 4
    after dedup. Four records (a, b, c, d) go to title/abstract screening: a and b pass,
    c and d are excluded (off-topic, wrong-population). a and b are sought and assessed at
    full text: a is included, b is excluded (wrong-outcome).
    """
    return [
        run.retrieval(
            TS1,
            source="openalex",
            query="self-supervised ECG",
            record_ids=["a", "b", "c"],
        ),
        run.retrieval(
            TS1,
            source="crossref",
            query="self-supervised ECG classification",
            record_ids=["d", "b-dup"],
        ),
        run.dedup_decision(
            TS2, winner="b", losers=["b-dup"], reason="doi_exact"
        ),
        # Title/abstract stage.
        _screen(run, TS2, record_id="a", verdict="include", stage="title-abstract"),
        _screen(run, TS2, record_id="b", verdict="include", stage="title-abstract"),
        _screen(
            run, TS2, record_id="c", verdict="exclude", stage="title-abstract",
            reason="off-topic",
        ),
        _screen(
            run, TS3, record_id="d", verdict="exclude", stage="title-abstract",
            reason="wrong-population",
        ),
        # Full-text stage.
        _screen(run, TS3, record_id="a", verdict="include", stage="full-text"),
        _screen(
            run, TS3, record_id="b", verdict="exclude", stage="full-text",
            reason="wrong-outcome",
        ),
    ]


# ---------------------------------------------------------------------------
# Hand-recomputed counts match the derived flow exactly
# ---------------------------------------------------------------------------


def test_flow_matches_a_hand_recomputation_of_the_events(run):
    events = _seed(run)
    flow = derive_prisma_flow(events, run_id=RUN_ID)

    # Identification, recomputed by hand from the two retrieval events.
    assert flow.identified == 5
    assert flow.identified_by_source == {"openalex": 3, "crossref": 2}
    assert flow.duplicates_removed == 1
    assert flow.records_after_dedup == 4

    # Title/abstract: 4 screened, 2 excluded (c, d), 2 pass (a, b).
    assert flow.records_screened == 4
    assert flow.records_excluded == 2
    assert flow.reports_sought_for_retrieval == 2

    # Full-text: 2 assessed, 1 excluded (b), 1 included (a).
    assert flow.reports_assessed == 2
    assert flow.reports_not_retrieved == 0
    assert flow.reports_excluded == 1
    assert flow.studies_included == 1

    # The two stages are present and ordered title/abstract then full-text.
    assert [s.stage for s in flow.stages] == [TITLE_ABSTRACT, FULL_TEXT]


def test_flow_derived_from_a_real_ledger_equals_the_events_derivation(ledger, run):
    events = _seed(run)
    ledger.append_many(events)
    from_ledger = prisma_flow(ledger, RUN_ID)
    from_events = derive_prisma_flow(events, run_id=RUN_ID)
    assert from_ledger.to_json_dict() == from_events.to_json_dict()


def test_flow_is_scoped_by_run_id(ledger, run):
    ledger.append_many(_seed(run))
    other = RunContext(run_id="run-other")
    ledger.append(
        other.retrieval(TS1, source="pubmed", query="q", record_ids=["z1", "z2"])
    )
    assert prisma_flow(ledger, RUN_ID).identified == 5
    assert prisma_flow(ledger, "run-other").identified == 2
    assert prisma_flow(ledger).identified == 7  # every run pooled


# ---------------------------------------------------------------------------
# Deleting a screening event changes the derived flow (D10: derived, not stored)
# ---------------------------------------------------------------------------


def test_deleting_a_screening_event_changes_the_derived_flow(run):
    events = _seed(run)
    full = derive_prisma_flow(events, run_id=RUN_ID)

    # Drop the full-text exclusion of record b. Because the flow is DERIVED, not stored,
    # the counts must move: b is no longer excluded at full text.
    trimmed = [
        e
        for e in events
        if not (
            e.type == "screening_decision"
            and e.payload.get("record_id") == "b"
            and e.payload.get("stage") == "full-text"
        )
    ]
    assert len(trimmed) == len(events) - 1

    after = derive_prisma_flow(trimmed, run_id=RUN_ID)
    assert full.reports_excluded == 1
    assert after.reports_excluded == 0
    assert after.reports_assessed == 1
    assert "wrong-outcome" not in after.reports_excluded_by_reason
    assert after.to_json_dict() != full.to_json_dict()


def test_removing_a_retrieval_event_changes_identified_counts(run):
    events = _seed(run)
    full = derive_prisma_flow(events, run_id=RUN_ID)
    trimmed = [
        e
        for e in events
        if not (e.type == "retrieval" and e.payload.get("source") == "crossref")
    ]
    after = derive_prisma_flow(trimmed, run_id=RUN_ID)
    assert full.identified == 5
    assert after.identified == 3
    assert "crossref" not in after.identified_by_source


# ---------------------------------------------------------------------------
# Exclusion reasons are grouped and counted
# ---------------------------------------------------------------------------


def test_full_text_exclusion_reasons_are_grouped_and_counted(run):
    events = list(_seed(run))
    # Two more full-text exclusions sharing a reason, plus one with a fresh reason.
    events += [
        run.retrieval(TS1, source="arxiv", query="ecg", record_ids=["e", "f", "g"]),
        _screen(run, TS2, record_id="e", verdict="include", stage="title-abstract"),
        _screen(run, TS2, record_id="f", verdict="include", stage="title-abstract"),
        _screen(run, TS2, record_id="g", verdict="include", stage="title-abstract"),
        _screen(
            run, TS3, record_id="e", verdict="exclude", stage="full-text",
            reason="wrong-outcome",
        ),
        _screen(
            run, TS3, record_id="f", verdict="exclude", stage="full-text",
            reason="wrong-outcome",
        ),
        _screen(
            run, TS4, record_id="g", verdict="exclude", stage="full-text",
            reason="no-full-text",
        ),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    assert flow.reports_excluded_by_reason == {"no-full-text": 1, "wrong-outcome": 3}
    assert flow.reports_excluded == 4
    # The reason totals sum to the excluded total.
    assert sum(flow.reports_excluded_by_reason.values()) == flow.reports_excluded


def test_exclusion_without_a_reason_is_counted_as_unspecified(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a"]),
        _screen(run, TS2, record_id="a", verdict="exclude", stage="title-abstract"),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    (stage,) = flow.stages
    assert stage.excluded_by_reason == {"unspecified": 1}


# ---------------------------------------------------------------------------
# Dual screening: adjudication overrides the streams; conflicts stay pending
# ---------------------------------------------------------------------------


def test_adjudication_overrides_two_disagreeing_streams(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a"]),
        _screen(
            run, TS2, record_id="a", verdict="include", stage="full-text",
            screener_id="s1",
        ),
        _screen(
            run, TS2, record_id="a", verdict="exclude", stage="full-text",
            reason="wrong-population", screener_id="s2",
        ),
        _adjudicate(
            run, TS3, record_id="a", verdict="exclude", stage="full-text",
            reason="wrong-population",
        ),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    (stage,) = flow.stages
    assert stage.screened == 1
    assert stage.excluded == 1
    assert stage.included == 0
    assert stage.pending == 0
    assert stage.excluded_by_reason == {"wrong-population": 1}


def test_unadjudicated_conflict_is_pending_not_an_exclusion(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a"]),
        _screen(
            run, TS2, record_id="a", verdict="include", stage="full-text",
            screener_id="s1",
        ),
        _screen(
            run, TS2, record_id="a", verdict="exclude", stage="full-text",
            reason="wrong-population", screener_id="s2",
        ),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    (stage,) = flow.stages
    assert stage.screened == 1
    assert stage.included == 0
    assert stage.excluded == 0
    assert stage.pending == 1
    # A pending record is neither included nor excluded: no fabricated decision.
    assert stage.excluded_by_reason == {}


def test_two_agreeing_streams_resolve_without_adjudication(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a"]),
        _screen(
            run, TS2, record_id="a", verdict="include", stage="title-abstract",
            screener_id="s1",
        ),
        _screen(
            run, TS2, record_id="a", verdict="include", stage="title-abstract",
            screener_id="s2",
        ),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    (stage,) = flow.stages
    assert stage.screened == 1
    assert stage.included == 1


# ---------------------------------------------------------------------------
# Stage handling
# ---------------------------------------------------------------------------


def test_missing_stage_defaults_to_title_abstract(run):
    event = run.event(
        "screening_decision", {"record_id": "a", "verdict": "include"}, TS2
    )
    flow = derive_prisma_flow([event], run_id=RUN_ID)
    (stage,) = flow.stages
    assert stage.stage == TITLE_ABSTRACT


def test_reports_not_retrieved_is_derived_from_sought_minus_assessed(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a", "b"]),
        _screen(run, TS2, record_id="a", verdict="include", stage="title-abstract"),
        _screen(run, TS2, record_id="b", verdict="include", stage="title-abstract"),
        # Only a is assessed at full text; b was sought but not retrieved.
        _screen(run, TS3, record_id="a", verdict="include", stage="full-text"),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    assert flow.reports_sought_for_retrieval == 2
    assert flow.reports_assessed == 1
    assert flow.reports_not_retrieved == 1


def test_legacy_decision_key_is_accepted(run):
    events = [
        run.retrieval(TS1, source="openalex", query="q", record_ids=["a", "b"]),
        run.event(
            "screening_decision", {"record_id": "a", "decision": "include"}, TS2
        ),
        run.event(
            "screening_decision",
            {"record_id": "b", "decision": "exclude", "reason": "off-topic"},
            TS2,
        ),
    ]
    flow = derive_prisma_flow(events, run_id=RUN_ID)
    assert flow.records_screened == 2
    assert flow.records_excluded == 1


# ---------------------------------------------------------------------------
# Determinism (D15)
# ---------------------------------------------------------------------------


def test_flow_json_is_deterministic(run):
    events = _seed(run)
    first = derive_prisma_flow(events, run_id=RUN_ID).to_json_dict()
    second = derive_prisma_flow(list(reversed(events)), run_id=RUN_ID).to_json_dict()
    # Aggregation is order-independent for the counts (dedup and screening are set-based),
    # so a reordered event list yields the same derived flow.
    assert first == second


def test_module_reads_no_clock():
    """No time call anywhere: a self-generated ts would break replay determinism (D15)."""
    import ast

    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "scieflow" / "_vendor" / "researcher_core"
        / "sr_prisma.py"
    ).read_text(encoding="utf-8")
    forbidden = {"time", "now", "utcnow", "today", "monotonic", "perf_counter", "uuid4"}
    called: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
    assert not (called & forbidden), sorted(called & forbidden)


# ---------------------------------------------------------------------------
# Checklist coverage
# ---------------------------------------------------------------------------


def test_checklist_maps_items_to_ledger_evidence_or_author(run):
    events = list(_seed(run))
    events.insert(0, run.event("protocol_locked", {"protocol_hash": "h"}, TS1))
    events.append(run.event("amendment", {"what": "added a source", "why": "coverage"}, TS4))

    checklist = derive_prisma_checklist(events, run_id=RUN_ID)
    by_item = {item.item: item for item in checklist.items}

    # Item 7 (search strategy) is satisfied by the verbatim retrieval queries.
    assert by_item["7"].status == STATUS_DERIVED
    assert "verbatim query" in by_item["7"].evidence

    # Item 16a (flow) is satisfied by the derived flow.
    assert by_item["16a"].status == STATUS_DERIVED
    assert "identified" in by_item["16a"].evidence

    # Item 16b (excluded with reasons) is satisfied by grouped full-text reasons.
    assert by_item["16b"].status == STATUS_DERIVED

    # Item 8 (selection process) satisfied by screening events.
    assert by_item["8"].status == STATUS_DERIVED

    # Registration/protocol and amendments satisfied by the M4 lifecycle events.
    assert by_item["24a"].status == STATUS_DERIVED
    assert by_item["24b"].status == STATUS_DERIVED

    # Rationale and objectives can never come from the ledger.
    assert by_item["3"].status == STATUS_AUTHOR
    assert by_item["4"].status == STATUS_AUTHOR

    # Coverage tallies each status and sums to the item count.
    assert sum(checklist.coverage.values()) == len(checklist.items)


def test_checklist_marks_missing_evidence_when_the_ledger_is_empty(run):
    # A ledger with only a protocol lock: search, screening, and flow are not yet recorded.
    events = [run.event("protocol_locked", {"protocol_hash": "h"}, TS1)]
    checklist = derive_prisma_checklist(events, run_id=RUN_ID)
    by_item = {item.item: item for item in checklist.items}

    assert by_item["5"].status == STATUS_DERIVED  # protocol locked
    assert by_item["7"].status == STATUS_MISSING  # no retrieval events
    assert by_item["8"].status == STATUS_MISSING  # no screening events
    assert by_item["16a"].status == STATUS_MISSING  # nothing to flow


def test_checklist_from_a_real_ledger(ledger, run):
    ledger.append_many(_seed(run))
    checklist = prisma_checklist(ledger, RUN_ID)
    assert isinstance(checklist, PrismaChecklist)
    by_item = {item.item: item for item in checklist.items}
    assert by_item["16a"].status == STATUS_DERIVED
    # No protocol was locked in _seed, so item 5 is missing rather than author-supplied.
    assert by_item["5"].status == STATUS_MISSING


def test_flow_and_checklist_types_are_public():
    assert PrismaFlow().to_json_dict()["identified"] == 0
    assert PrismaChecklist().to_json_dict()["items"] == []
