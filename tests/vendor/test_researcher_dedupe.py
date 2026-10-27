"""Dedupe tests.

The two cases the plan names as acceptance for M2.4 are pinned here on planted duplicates:
the same DOI arriving from two sources, and the same title arriving with no DOI at all.
Everything else in this file exists to protect the thing that makes dedupe safe rather than
merely tidy: a merge must never lose an identifier or a source attribution, because
``verify.py`` counts confirming sources to reach a D9 verdict, and a lost attribution
silently downgrades ``verified`` to ``inconclusive``.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_dedupe.py. Imports are
# rewritten to scieflow._vendor.researcher_core; test bodies are unchanged. See VENDORED.md.

from __future__ import annotations

from scieflow._vendor.researcher_core.dedupe import (
    MIN_TITLE_FINGERPRINT_CHARS,
    REASON_DOI_EXACT,
    REASON_TITLE_SIMILARITY,
    TITLE_SIMILARITY_THRESHOLD,
    dedupe,
    identifier_key,
    identity_key,
    merge_records,
    title_similarity,
)
from scieflow._vendor.researcher_core.model import CSLDate, CSLRecord, canonical_json


def record(
    *,
    title: str = "",
    doi: str = "",
    source: str = "openalex",
    source_id: str = "",
    year: int | None = None,
    citations: int | None = None,
    **kwargs: object,
) -> CSLRecord:
    return CSLRecord(
        title=title,
        DOI=doi,
        source=source,
        source_id=source_id,
        issued=CSLDate.from_year(year),
        citation_count=citations,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Identity keys
# ---------------------------------------------------------------------------


def test_identity_key_prefers_the_doi() -> None:
    rec = record(title="A paper", doi="10.1000/abc", source_id="W1", openalex_id="W1")
    assert identity_key(rec) == "doi:10.1000/abc"


def test_identity_key_falls_back_through_the_identifier_ladder() -> None:
    assert identity_key(record(title="X", openalex_id="W99")) == "openalex:w99"
    assert identity_key(record(title="X", arxiv_id="2103.00020")) == "arxiv:2103.00020"
    assert identity_key(record(title="X", pmid="12345")) == "pmid:12345"
    assert identity_key(record(title="X", s2_id="abc123")) == "s2:abc123"
    assert identity_key(record(title="Deep learning: a review")) == "title:deep learning a review"


def test_identifier_key_matches_the_key_a_record_would_produce() -> None:
    rec = record(title="A paper", doi="10.1000/ABC")
    assert identifier_key("https://doi.org/10.1000/abc") == identity_key(rec)
    assert identifier_key("W2741809807") == "openalex:w2741809807"
    assert identifier_key("") == ""


# ---------------------------------------------------------------------------
# Title similarity
# ---------------------------------------------------------------------------


def test_title_similarity_ignores_case_punctuation_and_word_order() -> None:
    assert title_similarity("Deep Learning: A Review", "deep learning - a review") == 1.0
    assert title_similarity("A Review of Deep Learning", "Deep Learning: A Review") >= 0.90


def test_title_similarity_separates_different_papers_on_one_topic() -> None:
    left = "Self-supervised ECG representation learning for emotion recognition"
    right = "Supervised deep learning for arrhythmia detection in ECG signals"
    assert title_similarity(left, right) < TITLE_SIMILARITY_THRESHOLD


def test_title_similarity_of_an_empty_title_is_zero() -> None:
    assert title_similarity("", "Deep learning") == 0.0


# ---------------------------------------------------------------------------
# Pass 1: DOI exact
# ---------------------------------------------------------------------------


def test_same_doi_from_two_sources_collapses_to_one_record() -> None:
    openalex = record(
        title="Self-Supervised ECG Representation Learning",
        doi="10.1109/taffc.2020.3014842",
        source="openalex",
        source_id="W3005055041",
        openalex_id="W3005055041",
        year=2020,
        citations=150,
    )
    crossref = record(
        # Resolver prefix and different case: normalize_doi makes these the same DOI.
        title="Self-supervised ECG representation learning",
        doi="https://doi.org/10.1109/TAFFC.2020.3014842",
        source="crossref",
        source_id="10.1109/taffc.2020.3014842",
        year=2020,
        citations=161,
    )

    result = dedupe([openalex, crossref])

    assert len(result.records) == 1
    merged = result.records[0]
    assert merged.DOI == "10.1109/taffc.2020.3014842"
    assert merged.extra["sources"] == ["openalex", "crossref"]
    assert merged.extra["source_ids"]["crossref"] == "10.1109/taffc.2020.3014842"
    assert len(result.decisions) == 1
    assert result.decisions[0].reason == REASON_DOI_EXACT
    assert result.decisions[0].similarity == 1.0


def test_doi_merge_keeps_the_union_of_identifiers() -> None:
    """A lost identifier is a lost lookup later; the union is the whole point of merging."""
    a = record(
        title="A paper",
        doi="10.1000/abc",
        source="openalex",
        openalex_id="W1",
        arxiv_id="2103.00020",
    )
    b = record(title="A paper", doi="10.1000/abc", source="pubmed", pmid="123", pmcid="PMC9")
    c = record(title="A paper", doi="10.1000/abc", source="semantic_scholar", s2_id="s2xyz")

    merged = dedupe([a, b, c]).records[0]

    assert merged.openalex_id == "W1"
    assert merged.arxiv_id == "2103.00020"
    assert merged.pmid == "123"
    assert merged.pmcid == "PMC9"
    assert merged.s2_id == "s2xyz"
    assert merged.extra["sources"] == ["openalex", "pubmed", "semantic_scholar"]


def test_merge_takes_the_larger_citation_count_and_any_retraction() -> None:
    stale = record(title="A paper", doi="10.1000/abc", source="crossref", citations=10)
    stale.is_retracted = False
    fresh = record(title="A paper", doi="10.1000/abc", source="openalex", citations=99)
    fresh.is_retracted = True

    merged = merge_records(stale, fresh)

    assert merged.citation_count == 99
    # Axis (b) must never lose a retraction to a source that has not caught up.
    assert merged.is_retracted is True


def test_merge_fills_blanks_but_never_overwrites_the_primary() -> None:
    primary = record(title="A paper", doi="10.1000/abc", source="openalex")
    other = record(title="A paper (extended)", doi="10.1000/abc", source="crossref")
    other.abstract = "An abstract only Crossref carried."
    other.container_title = "Journal of Things"

    merged = merge_records(primary, other)

    assert merged.title == "A paper"  # primary wins a populated field
    assert merged.abstract == "An abstract only Crossref carried."  # other fills the blank
    assert merged.container_title == "Journal of Things"


# ---------------------------------------------------------------------------
# Pass 2: title similarity
# ---------------------------------------------------------------------------


def test_same_title_with_no_doi_collapses_into_the_doi_bearing_record() -> None:
    with_doi = record(
        title="A Study of Wearable ECG Signal Quality",
        doi="10.1000/quality",
        source="crossref",
        year=2022,
    )
    without_doi = record(
        title="A study of wearable ECG signal quality!",
        source="openalex",
        openalex_id="W3000000001",
        year=2022,
    )

    result = dedupe([without_doi, with_doi])

    assert len(result.records) == 1
    merged = result.records[0]
    # The no-DOI record came first, so it survives, and it ACQUIRES the DOI on merge.
    assert merged.openalex_id == "W3000000001"
    assert merged.DOI == "10.1000/quality"
    assert merged.extra["sources"] == ["openalex", "crossref"]
    assert result.decisions[0].reason == REASON_TITLE_SIMILARITY
    assert result.decisions[0].similarity >= TITLE_SIMILARITY_THRESHOLD


def test_two_different_dois_never_merge_on_a_title_match() -> None:
    """A preprint and its version of record share a title and are two registered works."""
    preprint = record(title="Attention is all you need", doi="10.48550/arxiv.1706.03762")
    published = record(title="Attention Is All You Need", doi="10.5555/3295222.3295349")

    result = dedupe([preprint, published])

    assert len(result.records) == 2
    assert result.decisions == []


def test_a_short_generic_title_never_merges_on_similarity_alone() -> None:
    left = record(title="Editorial", source="crossref", source_id="a")
    right = record(title="Editorial", source="openalex", openalex_id="W2")

    result = dedupe([left, right])

    assert len("editorial") < MIN_TITLE_FINGERPRINT_CHARS
    assert len(result.records) == 2


def test_unrelated_records_are_left_alone() -> None:
    a = record(title="Self-supervised ECG representation learning", doi="10.1000/a")
    b = record(title="Transformer models for arrhythmia detection", doi="10.1000/b")

    result = dedupe([a, b])

    assert len(result.records) == 2
    assert result.decisions == []
    assert result.duplicates_removed == 0


# ---------------------------------------------------------------------------
# Decisions, key map, and determinism
# ---------------------------------------------------------------------------


def test_every_input_key_maps_to_the_record_that_survived_it() -> None:
    a = record(title="A paper about ECG signals", doi="10.1000/abc", source="openalex")
    b = record(title="A paper about ECG signals", doi="10.1000/abc", source="crossref")
    c = record(title="Something else entirely here", doi="10.1000/xyz", source="openalex")

    result = dedupe([a, b, c])

    assert result.key_map == {
        "doi:10.1000/abc": "doi:10.1000/abc",
        "doi:10.1000/xyz": "doi:10.1000/xyz",
    }
    assert set(result.key_map) >= {identity_key(a), identity_key(b), identity_key(c)}


def test_decisions_carry_both_sides_for_the_provenance_ledger() -> None:
    a = record(title="A paper about ECG signals", doi="10.1000/abc", source="openalex")
    b = record(title="A paper about ECG signals", doi="10.1000/abc", source="crossref")

    decision = dedupe([a, b]).decisions[0]
    payload = decision.to_json_dict()

    assert payload["kept_id"] == "10.1000/abc"
    assert payload["duplicate_id"] == "10.1000/abc"
    assert payload["kept_key"] == "doi:10.1000/abc"
    assert payload["reason"] == REASON_DOI_EXACT
    assert payload["kept_sources"] == ["openalex", "crossref"]
    assert payload["duplicate_sources"] == ["crossref"]


def test_a_single_source_record_still_carries_its_attribution() -> None:
    result = dedupe([record(title="A lonely paper on ECG", doi="10.1000/abc", source="arxiv")])

    assert result.records[0].extra["sources"] == ["arxiv"]


def test_dedupe_is_deterministic_and_does_not_mutate_its_input() -> None:
    items = [
        record(title="A paper about ECG signals", doi="10.1000/abc", source="openalex"),
        record(title="A paper about ECG signals", doi="10.1000/abc", source="crossref"),
        record(title="Another paper about ECG signals", source="arxiv", arxiv_id="2103.1"),
    ]
    before = [r.canonical_json() for r in items]

    first = dedupe(items)
    second = dedupe(items)

    assert [r.canonical_json() for r in items] == before  # inputs untouched
    assert canonical_json(first.to_json_dict()) == canonical_json(second.to_json_dict())


def test_empty_input_is_an_empty_result() -> None:
    result = dedupe([])

    assert result.records == []
    assert result.decisions == []
    assert result.key_map == {}
