"""Tests for dual screening, blinded adjudication, kappa, and the ranked queue (M4.4, M4.5).

The four acceptance properties are asserted directly:

* a seeded conflict set is surfaced BLIND (the adjudication payload carries no votes);
* an adjudication event carries BOTH original screening_decision event ids;
* Cohen's kappa matches a hand computation;
* the ranked queue reorders only and drops nothing; disabling restores insertion order.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_screen.py. Imports are rewritten to
# scieflow._vendor.researcher_core; the ledger fixture is MemoryLedger (the SQLite ledger is not
# vendored, see _ledger.py). Test bodies are unchanged except the removals listed below. See VENDORED.md.
# Removed upstream tests: none.

from __future__ import annotations

import json
import random

import pytest

from scieflow._vendor.researcher_core.model import CSLRecord
from scieflow._vendor.researcher_core._ledger import MemoryLedger, RunContext
from scieflow._vendor.researcher_core.screen import (
    EXCLUDE,
    INCLUDE,
    Conflict,
    ScreenError,
    cohens_kappa,
    queue_similarity,
    rank_queue,
    record_adjudication,
    record_screening_decision,
    screen_conflicts,
    screening_streams,
)

RUN_ID = "run-2026-07-16-0001"

# A monotonically stepped, caller-supplied clock. Nothing in screen.py reads the wall
# clock (D15); tests supply every ts.
_BASE = "2026-07-16T12:00:"


def ts(step: int) -> str:
    return f"{_BASE}{step:02d}Z"


@pytest.fixture()
def ledger():
    instance = MemoryLedger()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture()
def run() -> RunContext:
    return RunContext(run_id=RUN_ID)


def decide(ledger, run, screener, record, decision, step, *, stage="title-abstract", reason=""):
    return record_screening_decision(
        ledger,
        run,
        screener_id=screener,
        record_id=record,
        stage=stage,
        decision=decision,
        ts=ts(step),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The kappa fixture: a hand-computable dual-screening set (10 shared records)
# ---------------------------------------------------------------------------
#
# Confusion (rows = screener alice, cols = screener bob), categories include/exclude:
#
#                  bob:include   bob:exclude
#   alice:include        5             2
#   alice:exclude        1             2
#
#   n  = 10
#   po = (5 + 2) / 10                     = 0.70
#   alice marginals: include 7, exclude 3 -> 0.7, 0.3
#   bob   marginals: include 6, exclude 4 -> 0.6, 0.4
#   pe = 0.7*0.6 + 0.3*0.4 = 0.42 + 0.12 = 0.54
#   kappa = (0.70 - 0.54) / (1 - 0.54) = 0.16 / 0.46 = 0.34782608695652173


def _seed_kappa_set(ledger, run) -> None:
    step = 0

    def turn(screener, record, decision):
        nonlocal step
        decide(ledger, run, screener, record, decision, step)
        step += 1

    # 5 both-include
    for i in range(5):
        turn("alice", f"bi{i}", INCLUDE)
        turn("bob", f"bi{i}", INCLUDE)
    # 2 both-exclude
    for i in range(2):
        turn("alice", f"be{i}", EXCLUDE)
        turn("bob", f"be{i}", EXCLUDE)
    # 2 alice-include / bob-exclude  (conflicts)
    for i in range(2):
        turn("alice", f"ab{i}", INCLUDE)
        turn("bob", f"ab{i}", EXCLUDE)
    # 1 alice-exclude / bob-include  (conflict)
    turn("alice", "ba0", EXCLUDE)
    turn("bob", "ba0", INCLUDE)


# ---------------------------------------------------------------------------
# Kappa matches a hand computation
# ---------------------------------------------------------------------------


def test_cohens_kappa_matches_hand_computation(ledger, run):
    _seed_kappa_set(ledger, run)
    result = cohens_kappa(ledger, RUN_ID, "title-abstract")

    assert result.n == 10
    assert result.screener_ids == ("alice", "bob")
    assert result.observed_agreement == pytest.approx(0.70)
    assert result.expected_agreement == pytest.approx(0.54)
    assert result.kappa == pytest.approx(0.16 / 0.46)
    assert result.kappa == pytest.approx(0.34782608695652173)
    assert result.single_screener is False
    # The confusion table reproduces the hand-drawn matrix.
    assert result.table["include"]["include"] == 5
    assert result.table["include"]["exclude"] == 2
    assert result.table["exclude"]["include"] == 1
    assert result.table["exclude"]["exclude"] == 2


def test_kappa_is_derived_not_stored(ledger, run):
    # Adding a fresh agreement changes the derived kappa: it is aggregated, never cached.
    _seed_kappa_set(ledger, run)
    before = cohens_kappa(ledger, RUN_ID, "title-abstract").kappa
    decide(ledger, run, "alice", "extra", INCLUDE, 100)
    decide(ledger, run, "bob", "extra", INCLUDE, 101)
    after = cohens_kappa(ledger, RUN_ID, "title-abstract")
    assert after.n == 11
    assert after.kappa != before


def test_single_screener_is_reported_not_hidden(ledger, run):
    decide(ledger, run, "solo", "r1", INCLUDE, 0)
    decide(ledger, run, "solo", "r2", EXCLUDE, 1)
    result = cohens_kappa(ledger, RUN_ID, "title-abstract")
    assert result.single_screener is True
    assert result.kappa is None
    assert "one" in result.note.lower()

    summary = screening_streams(ledger, RUN_ID, "title-abstract")
    assert summary.single_screener is True
    assert summary.screener_ids == ("solo",)
    assert summary.records_screened == 2


def test_dual_screener_summary_reports_both_streams(ledger, run):
    _seed_kappa_set(ledger, run)
    summary = screening_streams(ledger, RUN_ID, "title-abstract")
    assert summary.single_screener is False
    assert summary.screener_ids == ("alice", "bob")
    assert summary.records_screened == 10
    assert summary.records_decided_by_all == 10
    assert summary.per_screener_counts["alice"] == {"include": 7, "exclude": 3}
    assert summary.per_screener_counts["bob"] == {"include": 6, "exclude": 4}


def test_perfect_agreement_kappa_is_undefined_but_agreement_reported(ledger, run):
    # Both streams include everything: expected agreement is 1, kappa undefined.
    for i in range(3):
        decide(ledger, run, "alice", f"r{i}", INCLUDE, 2 * i)
        decide(ledger, run, "bob", f"r{i}", INCLUDE, 2 * i + 1)
    result = cohens_kappa(ledger, RUN_ID, "title-abstract")
    assert result.observed_agreement == pytest.approx(1.0)
    assert result.kappa is None
    assert "undefined" in result.note.lower()


def test_more_than_two_screeners_raises(ledger, run):
    decide(ledger, run, "alice", "r1", INCLUDE, 0)
    decide(ledger, run, "bob", "r1", INCLUDE, 1)
    decide(ledger, run, "carol", "r1", EXCLUDE, 2)
    with pytest.raises(ScreenError):
        cohens_kappa(ledger, RUN_ID, "title-abstract")


# ---------------------------------------------------------------------------
# Blinded conflicts + adjudication
# ---------------------------------------------------------------------------

_PROFILE = {
    "framing": "PICO",
    "population": "adults with atrial fibrillation",
    "intervention": "wearable single-lead ECG",
    "outcome": "detection sensitivity",
}


def _corpus() -> dict:
    return {
        "ab0": CSLRecord(
            title="Wearable ECG for atrial fibrillation detection",
            abstract="A cohort study.",
        ),
        "ab1": {"title": "Single-lead ECG sensitivity", "abstract": "Adults with AF."},
        "ba0": CSLRecord(
            title="Photoplethysmography screening", abstract="Wrist-based detection."
        ),
    }


def test_conflicts_are_surfaced_blind(ledger, run):
    _seed_kappa_set(ledger, run)
    conflicts = screen_conflicts(
        ledger, RUN_ID, "title-abstract", corpus=_corpus(), profile=_PROFILE
    )
    # Three seeded disagreements: ab0, ab1 (alice-inc/bob-exc), ba0 (alice-exc/bob-inc).
    assert [c.record_id for c in conflicts] == ["ab0", "ab1", "ba0"]

    for conflict in conflicts:
        prompt = conflict.adjudication_prompt()
        # The blinded prompt carries ONLY the record, the profile, and identifiers.
        assert set(prompt) == {"record_id", "stage", "record", "eligibility_profile"}
        assert prompt["eligibility_profile"] == _PROFILE
        # No vote key, and neither "include" nor "exclude" appears anywhere in the payload.
        blob = json.dumps(prompt).lower()
        for banned in ("include", "exclude", "verdict", "decision", "reason", "screener", "vote"):
            assert banned not in blob
        # The serialized conflict adds only opaque linkage ids, still no votes.
        serialized = json.dumps(conflict.to_json_dict()).lower()
        for banned in ("verdict", "vote", "\"decision\"", "screener_id", "reason"):
            assert banned not in serialized


def test_conflict_has_no_vote_carrying_field(ledger, run):
    _seed_kappa_set(ledger, run)
    conflict = screen_conflicts(ledger, RUN_ID, "title-abstract")[0]
    # The dataclass simply has no field a verdict could live in.
    field_names = set(conflict.__dataclass_fields__)
    assert field_names == {
        "record_id",
        "stage",
        "decision_event_ids",
        "record",
        "eligibility_profile",
    }
    assert len(conflict.decision_event_ids) == 2


def test_agreements_are_not_conflicts(ledger, run):
    # Both include the same record: not a conflict.
    decide(ledger, run, "alice", "agree", INCLUDE, 0)
    decide(ledger, run, "bob", "agree", INCLUDE, 1)
    assert screen_conflicts(ledger, RUN_ID, "title-abstract") == []


def test_single_stream_record_is_not_a_conflict(ledger, run):
    # Only one screener decided it: cannot be a disagreement.
    decide(ledger, run, "alice", "lonely", INCLUDE, 0)
    assert screen_conflicts(ledger, RUN_ID, "title-abstract") == []


def test_revised_decision_can_resolve_a_conflict(ledger, run):
    # alice flips to match bob on a later event; the final positions agree, so no conflict.
    decide(ledger, run, "alice", "r1", INCLUDE, 0)
    decide(ledger, run, "bob", "r1", EXCLUDE, 1)
    assert len(screen_conflicts(ledger, RUN_ID, "title-abstract")) == 1
    decide(ledger, run, "alice", "r1", EXCLUDE, 2)  # revision
    assert screen_conflicts(ledger, RUN_ID, "title-abstract") == []


def test_adjudication_carries_both_original_event_ids(ledger, run):
    alice = decide(ledger, run, "alice", "r1", INCLUDE, 0)
    bob = decide(ledger, run, "bob", "r1", EXCLUDE, 1)
    conflict = screen_conflicts(ledger, RUN_ID, "title-abstract")[0]

    event = record_adjudication(
        ledger,
        run,
        conflict=conflict,
        decision=INCLUDE,
        rationale="Meets the population and intervention criteria on full read.",
        ts=ts(2),
    )
    assert event.type == "adjudication"
    resolves = event.payload["resolves"]
    assert set(resolves) == {alice.event_id, bob.event_id}
    assert event.payload["record_id"] == "r1"
    assert event.payload["decision"] == INCLUDE
    assert event.payload["rationale"]

    # It is persisted in the append-only ledger.
    stored = ledger.events(run_id=RUN_ID, type="adjudication")
    assert len(stored) == 1
    assert set(stored[0].payload["resolves"]) == {alice.event_id, bob.event_id}


def test_adjudication_requires_two_event_ids(ledger, run):
    with pytest.raises(ScreenError):
        record_adjudication(
            ledger,
            run,
            record_id="r1",
            stage="title-abstract",
            decision_event_ids=["only-one"],
            decision=INCLUDE,
            rationale="x",
            ts=ts(0),
        )


def test_adjudication_requires_rationale(ledger, run):
    alice = decide(ledger, run, "alice", "r1", INCLUDE, 0)
    bob = decide(ledger, run, "bob", "r1", EXCLUDE, 1)
    conflict = screen_conflicts(ledger, RUN_ID, "title-abstract")[0]
    with pytest.raises(ScreenError):
        record_adjudication(
            ledger, run, conflict=conflict, decision=INCLUDE, rationale="  ", ts=ts(2)
        )
    assert alice.event_id != bob.event_id


# ---------------------------------------------------------------------------
# Screening decision recording / validation
# ---------------------------------------------------------------------------


def test_screening_decision_feeds_prisma_counts(ledger, run):
    decide(ledger, run, "alice", "r1", INCLUDE, 0)
    decide(ledger, run, "alice", "r2", EXCLUDE, 1)
    counts = ledger.prisma(run_id=RUN_ID)
    assert counts.screened == 2
    assert counts.included == 1
    assert counts.excluded == 1


def test_unknown_stage_is_rejected(ledger, run):
    with pytest.raises(ScreenError):
        record_screening_decision(
            ledger, run, screener_id="a", record_id="r1", stage="middle",
            decision=INCLUDE, ts=ts(0),
        )


def test_missing_screener_or_record_is_rejected(ledger, run):
    with pytest.raises(ScreenError):
        record_screening_decision(
            ledger, run, screener_id="", record_id="r1", stage="title-abstract",
            decision=INCLUDE, ts=ts(0),
        )
    with pytest.raises(ScreenError):
        record_screening_decision(
            ledger, run, screener_id="a", record_id="", stage="title-abstract",
            decision=INCLUDE, ts=ts(0),
        )


def test_stage_filters_streams(ledger, run):
    decide(ledger, run, "alice", "r1", INCLUDE, 0, stage="title-abstract")
    decide(ledger, run, "bob", "r1", EXCLUDE, 1, stage="title-abstract")
    decide(ledger, run, "alice", "r1", INCLUDE, 2, stage="full-text")
    decide(ledger, run, "bob", "r1", INCLUDE, 3, stage="full-text")
    assert len(screen_conflicts(ledger, RUN_ID, "title-abstract")) == 1
    assert screen_conflicts(ledger, RUN_ID, "full-text") == []


# ---------------------------------------------------------------------------
# Ranked queue (M4.5): order only, drops nothing
# ---------------------------------------------------------------------------


def _relevance_corpus() -> dict:
    included = {
        "inc0": "deep learning ecg arrhythmia classification neural network",
        "inc1": "convolutional neural network electrocardiogram atrial fibrillation detection",
    }
    relevant = {
        f"rel{i}": "deep neural network ecg arrhythmia detection classification model"
        for i in range(5)
    }
    irrelevant = {
        "irr0": "medieval french lyric poetry manuscript tradition",
        "irr1": "quantum chromodynamics lattice gauge simulation",
        "irr2": "sourdough fermentation baking bread yeast culture",
        "irr3": "roman aqueduct hydraulic engineering construction",
        "irr4": "orbital mechanics interplanetary transfer trajectory",
    }
    corpus = {**included, **relevant, **irrelevant}
    return corpus


def test_ranked_queue_concentrates_relevant_records_earlier():
    corpus = _relevance_corpus()
    included = ["inc0", "inc1"]
    relevant = [f"rel{i}" for i in range(5)]
    irrelevant = [f"irr{i}" for i in range(5)]

    remaining = relevant + irrelevant
    rng = random.Random(20260716)
    rng.shuffle(remaining)

    ranked = rank_queue(remaining, included, corpus)

    positions = {record_id: index for index, record_id in enumerate(ranked)}
    mean_relevant = sum(positions[r] for r in relevant) / len(relevant)
    mean_irrelevant = sum(positions[r] for r in irrelevant) / len(irrelevant)
    assert mean_relevant < mean_irrelevant
    # Every relevant record sits ahead of every irrelevant one on this cleanly separated set.
    assert max(positions[r] for r in relevant) < min(positions[r] for r in irrelevant)


def test_ranked_queue_drops_nothing():
    corpus = _relevance_corpus()
    remaining = [f"rel{i}" for i in range(5)] + [f"irr{i}" for i in range(5)]
    ranked = rank_queue(remaining, ["inc0", "inc1"], corpus)
    assert sorted(ranked) == sorted(remaining)
    assert len(ranked) == len(remaining)


def test_disabling_prioritization_restores_insertion_order():
    corpus = _relevance_corpus()
    remaining = [f"irr{i}" for i in range(5)] + [f"rel{i}" for i in range(5)]
    assert rank_queue(remaining, ["inc0", "inc1"], corpus, enabled=False) == remaining


def test_no_included_records_leaves_order_unchanged():
    corpus = _relevance_corpus()
    remaining = [f"rel{i}" for i in range(5)] + [f"irr{i}" for i in range(5)]
    assert rank_queue(remaining, [], corpus) == remaining


def test_ranked_queue_is_deterministic():
    corpus = _relevance_corpus()
    remaining = [f"rel{i}" for i in range(5)] + [f"irr{i}" for i in range(5)]
    first = rank_queue(remaining, ["inc0", "inc1"], corpus)
    second = rank_queue(remaining, ["inc0", "inc1"], corpus)
    assert first == second


def test_equal_scores_keep_insertion_order():
    # Two records identical to each other and equally (dis)similar to the included set keep
    # their relative insertion order: the sort is stable via the index tiebreak.
    corpus = {
        "inc": "alpha beta gamma",
        "x": "delta epsilon zeta",
        "y": "delta epsilon zeta",
    }
    assert rank_queue(["x", "y"], ["inc"], corpus) == ["x", "y"]
    assert rank_queue(["y", "x"], ["inc"], corpus) == ["y", "x"]


def test_queue_similarity_bounds_and_missing_corpus():
    corpus = _relevance_corpus()
    score = queue_similarity("rel0", ["inc0", "inc1"], corpus)
    assert 0.0 <= score <= 1.0
    # A record absent from the corpus contributes no invented similarity.
    assert queue_similarity("nope", ["inc0"], corpus) == 0.0


def test_conflict_prompt_records_render_without_votes():
    # A CSLRecord in the corpus renders to CSL-JSON in the prompt, carrying no verdict.
    conflict = Conflict(
        record_id="ab0",
        stage="title-abstract",
        decision_event_ids=("h1", "h2"),
        record=CSLRecord(title="Wearable ECG", abstract="AF cohort."),
        eligibility_profile=_PROFILE,
    )
    prompt = conflict.adjudication_prompt()
    assert prompt["record"]["title"] == "Wearable ECG"
    assert "decision" not in json.dumps(prompt).lower()
