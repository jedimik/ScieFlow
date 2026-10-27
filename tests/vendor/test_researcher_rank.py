"""Ranking tests.

Two properties matter more than the exact ordering of any particular pair:

1. **Determinism (D15).** Equal scores break on the record id, and the recency reference
   year comes from the data rather than from the wall clock, so a snapshot replayed a year
   from now ranks identically. Both are asserted below.
2. **The weights are the contract.** Each component is tested in isolation, so a future
   change to a weight shows up as a failing, named test rather than as a quietly reordered
   result list.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_rank.py. Imports are
# rewritten to scieflow._vendor.researcher_core; test bodies are unchanged. See VENDORED.md.

from __future__ import annotations

from scieflow._vendor.researcher_core.model import CSLDate, CSLRecord, canonical_json
from scieflow._vendor.researcher_core.rank import (
    CITATION_WEIGHT,
    DEFAULT_WEIGHTS,
    RECENCY_UNKNOWN_SCORE,
    RECENCY_WEIGHT,
    RECENCY_WINDOW_YEARS,
    RELEVANCE_WEIGHT,
    RankWeights,
    citation_score,
    rank_records,
    recency_score,
    relevance_score,
    score_records,
)


def record(
    id: str = "",
    *,
    title: str = "A paper",
    doi: str = "",
    year: int | None = None,
    citations: int | None = None,
    source_ranks: dict[str, int] | None = None,
) -> CSLRecord:
    rec = CSLRecord(
        id=id,
        title=title,
        DOI=doi,
        issued=CSLDate.from_year(year),
        citation_count=citations,
    )
    if source_ranks is not None:
        rec.extra["source_ranks"] = dict(source_ranks)
    return rec


# ---------------------------------------------------------------------------
# The weights themselves
# ---------------------------------------------------------------------------


def test_the_default_weights_are_the_documented_ones() -> None:
    assert (RELEVANCE_WEIGHT, CITATION_WEIGHT, RECENCY_WEIGHT) == (0.50, 0.30, 0.20)
    assert RELEVANCE_WEIGHT + CITATION_WEIGHT + RECENCY_WEIGHT == 1.0
    assert DEFAULT_WEIGHTS.to_json_dict() == {
        "relevance": 0.50,
        "citations": 0.30,
        "recency": 0.20,
    }


# ---------------------------------------------------------------------------
# Relevance: reciprocal-rank fusion over the per-source ranks
# ---------------------------------------------------------------------------


def test_a_top_hit_from_one_source_scores_one_on_the_single_source_scale() -> None:
    assert relevance_score(record(source_ranks={"openalex": 0}), 0) == 1.0


def test_agreement_across_sources_beats_a_single_source_top_hit() -> None:
    """The one thing a fan-out knows that no single index does: three indexes agreed."""
    agreed = relevance_score(record(source_ranks={"openalex": 2, "crossref": 2, "arxiv": 2}), 0)
    alone = relevance_score(record(source_ranks={"openalex": 0}), 0)

    assert agreed > alone


def test_a_worse_rank_scores_lower_within_one_source() -> None:
    better = relevance_score(record(source_ranks={"openalex": 0}), 0)
    worse = relevance_score(record(source_ranks={"openalex": 9}), 0)

    assert better > worse


def test_relevance_falls_back_to_the_input_position_when_no_ranks_were_recorded() -> None:
    """Graph output and hand-assembled lists carry no source ranks; they still rank."""
    assert relevance_score(record(), 0) > relevance_score(record(), 5)


def test_relevance_ignores_dict_iteration_order() -> None:
    forward = relevance_score(record(source_ranks={"openalex": 0, "crossref": 3}), 0)
    backward = relevance_score(record(source_ranks={"crossref": 3, "openalex": 0}), 0)

    assert forward == backward


# ---------------------------------------------------------------------------
# Citations: log-scaled, set-normalized, never invented
# ---------------------------------------------------------------------------


def test_citation_score_is_log_scaled_and_normalized_to_the_set_maximum() -> None:
    assert citation_score(record(citations=1000), 1000) == 1.0
    # Log scaling: 100 of a 1000-citation maximum is worth much more than a tenth.
    assert citation_score(record(citations=100), 1000) > 0.6


def test_an_unknown_citation_count_contributes_nothing_rather_than_an_average() -> None:
    assert citation_score(record(citations=None), 1000) == 0.0
    assert citation_score(record(citations=0), 1000) == 0.0


# ---------------------------------------------------------------------------
# Recency: a ramp, and never the wall clock
# ---------------------------------------------------------------------------


def test_recency_ramps_linearly_over_the_window() -> None:
    assert recency_score(record(year=2026), 2026) == 1.0
    assert recency_score(record(year=2021), 2026) == 0.5
    assert recency_score(record(year=2026 - RECENCY_WINDOW_YEARS), 2026) == 0.0
    assert recency_score(record(year=1970), 2026) == 0.0


def test_a_future_year_is_clamped_not_rewarded() -> None:
    assert recency_score(record(year=2030), 2026) == 1.0


def test_a_missing_year_is_neutral() -> None:
    assert recency_score(record(year=None), 2026) == RECENCY_UNKNOWN_SCORE


def test_the_reference_year_comes_from_the_data_not_from_today() -> None:
    """D15: replaying the same snapshot next year must produce the same scores."""
    items = [record("a", year=2015, citations=10), record("b", year=2020, citations=10)]

    scores = {s.record_id: s.recency for s in score_records(items)}

    # 2020 is the newest year IN THE SET, so it is the reference: it scores 1.0, and 2015 is
    # five years back on a ten-year ramp. Nothing here depends on the current date.
    assert scores["b"] == 1.0
    assert scores["a"] == 0.5


# ---------------------------------------------------------------------------
# The composite
# ---------------------------------------------------------------------------


def test_the_composite_is_the_weighted_sum_of_the_three_components() -> None:
    items = [
        record("a", year=2024, citations=100, source_ranks={"openalex": 0}),
        record("b", year=2014, citations=1, source_ranks={"openalex": 4}),
    ]

    scores = score_records(items, reference_year=2024)

    for score in scores:
        expected = (
            DEFAULT_WEIGHTS.relevance * score.relevance
            + DEFAULT_WEIGHTS.citations * score.citations
            + DEFAULT_WEIGHTS.recency * score.recency
        )
        assert score.score == round(expected, 6)


def test_a_relevant_recent_well_cited_paper_outranks_a_stale_obscure_one() -> None:
    good = record("good", year=2024, citations=500, source_ranks={"openalex": 0, "crossref": 0})
    poor = record("poor", year=2005, citations=1, source_ranks={"arxiv": 20})

    ranked = rank_records([poor, good])

    assert [r.id for r in ranked] == ["good", "poor"]
    assert ranked[0].extra["score"] > ranked[1].extra["score"]


def test_an_off_topic_blockbuster_cannot_outrank_an_on_topic_paper() -> None:
    """Relevance outweighs citations by design; a 100k-citation off-topic hit stays below."""
    on_topic = record("on", year=2024, citations=5, source_ranks={"openalex": 0, "crossref": 0})
    blockbuster = record("off", year=2024, citations=100_000, source_ranks={"openalex": 24})

    ranked = rank_records([blockbuster, on_topic])

    assert [r.id for r in ranked] == ["on", "off"]


def test_ties_break_on_the_record_id_not_on_input_order() -> None:
    """Two identical records must rank identically no matter which order they arrive in."""
    first = record("zzz", year=2020, citations=10, source_ranks={"openalex": 0})
    second = record("aaa", year=2020, citations=10, source_ranks={"openalex": 0})

    forward = [r.id for r in rank_records([first, second])]
    backward = [r.id for r in rank_records([second, first])]

    assert forward == backward == ["aaa", "zzz"]


def test_the_score_lands_in_the_csl_custom_slot() -> None:
    ranked = rank_records([record("a", year=2024, citations=10)])

    payload = ranked[0].to_csl_json()
    assert payload["custom"]["score"] == ranked[0].extra["score"]
    assert isinstance(payload["custom"]["score"], float)


def test_ranking_copies_rather_than_mutating_and_is_idempotent() -> None:
    items = [record("a", year=2020, citations=5), record("b", year=2024, citations=50)]

    once = rank_records(items)
    twice = rank_records(items)

    assert "score" not in items[0].extra  # the inputs were not touched
    assert [r.canonical_json() for r in once] == [r.canonical_json() for r in twice]


def test_limit_truncates_after_ranking_not_before() -> None:
    items = [
        record("a", year=2000, citations=1, source_ranks={"openalex": 9}),
        record("b", year=2024, citations=900, source_ranks={"openalex": 0}),
    ]

    top = rank_records(items, limit=1)

    assert [r.id for r in top] == ["b"]


def test_custom_weights_change_the_order() -> None:
    recent = record("recent", year=2024, citations=1, source_ranks={"openalex": 3})
    cited = record("cited", year=2004, citations=900, source_ranks={"openalex": 3})

    by_citations = rank_records([recent, cited], weights=RankWeights(0.0, 1.0, 0.0))
    by_recency = rank_records([recent, cited], weights=RankWeights(0.0, 0.0, 1.0))

    assert [r.id for r in by_citations] == ["cited", "recent"]
    assert [r.id for r in by_recency] == ["recent", "cited"]


def test_empty_input_ranks_to_nothing() -> None:
    assert rank_records([]) == []
    assert score_records([]) == []


def test_two_runs_produce_byte_identical_json() -> None:
    items = [
        record("a", year=2020, citations=5, source_ranks={"openalex": 1}),
        record("b", year=2024, citations=50, source_ranks={"crossref": 0}),
    ]

    first = canonical_json([r.to_csl_json() for r in rank_records(items)])
    second = canonical_json([r.to_csl_json() for r in rank_records(items)])

    assert first == second
