"""Axis (a) reference-identity tests (D9), ported from researcher v1.0.0 (MIT).

The assertion this file exists for: ``inconclusive`` is never refusal-grade, and
``unresolvable`` is unreachable when any source errored. Everything else is detail.
"""

# Ported from researcher v1.0.0 (MIT), core/tests/test_verify.py. Imports are rewritten to
# scieflow._vendor.researcher_core; rapidfuzz is the ScieFlow shim (M-C2-D1). Not ported,
# because they need upstream connectors, the gold snapshot store or the report fixture
# (M-C2 t1 plan list): test_gold_* (8), test_report_* (4), test_replay_is_byte_identical,
# test_a_reference_with_no_doi_is_verified_by_title_and_reports_status_unchecked,
# test_verify_claims_sync_entry_point_runs_offline, and their helpers gold_session,
# gold_connectors, _make_it_fail, run_case, _verify_with, full_report, and the gold
# fixture loading. Every other test body is unchanged. See VENDORED.md.

from __future__ import annotations

from typing import Any

import pytest

from scieflow._vendor.researcher_core._fuzz import fuzz
from scieflow._vendor.researcher_core.errors import SourceErrorKind
from scieflow._vendor.researcher_core.model import CSLName, CSLRecord, title_fingerprint
from scieflow._vendor.researcher_core.verify import (
    BY_IDENTIFIER,
    BY_TITLE_SEARCH,
    CONFIRMED,
    DEFAULT_THRESHOLDS,
    INCONCLUSIVE,
    MISMATCH,
    NEGATIVE,
    SOURCE_ERROR,
    UNRESOLVABLE,
    VERIFIED,
    ReferenceClaim,
    SourceOutcome,
    Thresholds,
    assess_match,
    decide,
    is_refusal_grade,
    title_similarity,
)


def outcome(source: str, kind: str, **kwargs: Any) -> SourceOutcome:
    return SourceOutcome(source=source, outcome=kind, **kwargs)


def record(
    title: str = "Sharing Detailed Research Data Is Associated with Increased Citation Rate",
    year: int | None = 2007,
    surname: str = "Piwowar",
    doi: str = "10.1371/journal.pone.0000308",
    **kwargs: Any,
) -> CSLRecord:
    from scieflow._vendor.researcher_core.model import CSLDate

    return CSLRecord(
        title=title,
        author=[CSLName(family=surname, given="Heather A.")] if surname else [],
        issued=CSLDate.from_year(year),
        DOI=doi,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The D9 precedence, in isolation
# ---------------------------------------------------------------------------


def test_two_confirmations_verify() -> None:
    decision = decide([outcome("openalex", CONFIRMED), outcome("crossref", CONFIRMED)])
    assert decision.verdict == VERIFIED
    assert decision.refusal_grade is False


def test_two_confirmations_outrank_a_lone_disagreeing_source() -> None:
    """D9 rule 1. A lone bad index must never produce a refusal-grade verdict."""
    decision = decide(
        [
            outcome("openalex", CONFIRMED),
            outcome("crossref", CONFIRMED),
            outcome(
                "datacite",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("year",),
                matched_record=record(year=2019),
            ),
        ]
    )
    assert decision.verdict == VERIFIED
    assert decision.disagreements == ("datacite",)  # logged, not acted on
    assert "outranked" in decision.reason


def test_two_confirmations_outrank_an_erroring_source() -> None:
    decision = decide(
        [
            outcome("openalex", CONFIRMED),
            outcome("crossref", CONFIRMED),
            outcome("datacite", SOURCE_ERROR, error={"type": "timeout", "message": "timed out"}),
        ]
    )
    assert decision.verdict == VERIFIED
    assert decision.refusal_grade is False


def test_two_resolving_disagreements_are_a_mismatch() -> None:
    decision = decide(
        [
            outcome(
                "openalex",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("title_similarity",),
            ),
            outcome(
                "crossref",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("title_similarity",),
            ),
        ]
    )
    assert decision.verdict == MISMATCH
    assert decision.refusal_grade is True


def test_one_clean_disagreement_with_no_confirmation_is_a_mismatch() -> None:
    decision = decide(
        [
            outcome(
                "openalex",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("year",),
            ),
            outcome("crossref", NEGATIVE, resolved=False),
        ]
    )
    assert decision.verdict == MISMATCH


def test_one_disagreement_alongside_an_error_is_not_a_mismatch() -> None:
    """D9 rule 2 requires the single-disagreement case to be clean. An error makes it dirty."""
    decision = decide(
        [
            outcome(
                "openalex",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("year",),
            ),
            outcome("crossref", SOURCE_ERROR, error={"type": "timeout", "message": "timed out"}),
        ]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_one_disagreement_alongside_a_confirmation_is_not_a_mismatch() -> None:
    decision = decide(
        [
            outcome("openalex", CONFIRMED),
            outcome(
                "crossref",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("year",),
            ),
        ]
    )
    assert decision.verdict == INCONCLUSIVE


def test_all_clean_negatives_are_unresolvable() -> None:
    decision = decide(
        [
            outcome("openalex", NEGATIVE, resolved=False),
            outcome("crossref", NEGATIVE, resolved=False),
            outcome("datacite", NEGATIVE, resolved=False),
        ]
    )
    assert decision.verdict == UNRESOLVABLE
    assert decision.refusal_grade is True


@pytest.mark.parametrize(
    "kind",
    [
        SourceErrorKind.TIMEOUT,
        SourceErrorKind.RATE_LIMIT,
        SourceErrorKind.SERVER_ERROR,
        SourceErrorKind.NETWORK,
    ],
)
def test_a_single_source_error_makes_unresolvable_unreachable(kind: SourceErrorKind) -> None:
    """THE line. A downed index must never be counted as evidence of fabrication."""
    decision = decide(
        [
            outcome("openalex", NEGATIVE, resolved=False),
            outcome("crossref", NEGATIVE, resolved=False),
            outcome("datacite", SOURCE_ERROR, error={"type": kind.value, "message": "down"}),
        ]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False
    assert "clean negative cannot be asserted" in decision.reason


def test_a_single_confirmation_is_inconclusive_not_verified() -> None:
    """A legitimate single-index paper. Thin evidence, never a refusal."""
    decision = decide(
        [outcome("openalex", CONFIRMED), outcome("crossref", NEGATIVE, resolved=False)]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_no_sources_queried_is_inconclusive_not_unresolvable() -> None:
    decision = decide([])
    assert decision.verdict == INCONCLUSIVE


def test_only_two_verdicts_are_refusal_grade() -> None:
    assert is_refusal_grade(UNRESOLVABLE)
    assert is_refusal_grade(MISMATCH)
    assert not is_refusal_grade(INCONCLUSIVE)
    assert not is_refusal_grade(VERIFIED)


def test_min_confirmations_is_configurable_but_defaults_to_two() -> None:
    assert DEFAULT_THRESHOLDS.min_confirmations == 2
    single = decide([outcome("openalex", CONFIRMED)], Thresholds(min_confirmations=1))
    assert single.verdict == VERIFIED


# ---------------------------------------------------------------------------
# Matching and thresholds
# ---------------------------------------------------------------------------


def test_title_similarity_ignores_case_punctuation_and_word_order() -> None:
    assert title_similarity("Deep Learning: A Review", "deep learning - a review") == 1.0


def test_title_similarity_survives_truncation() -> None:
    full = "Sharing Detailed Research Data Is Associated with Increased Citation Rate"
    truncated = "Sharing Detailed Research Data Is Associated"
    assert title_similarity(truncated, full) == 1.0


def test_title_similarity_alone_cannot_establish_identity() -> None:
    """The measured reason the surname check is mandatory (see CALIBRATION.md).

    The fabricated title from the gold set scores 0.746 against a REAL and completely
    unrelated paper, above the 0.70 title bar. Title similarity alone would confirm a
    fabricated reference against the wrong work. It is the conjunction with the first-author
    surname (and the year) that stops it, which is why require_first_author_surname is True.
    """
    claimed = "Contrastive masked autoencoders for single-lead ECG anomaly detection"
    other = "Masked Contrastive Learning for Anomaly Detection"
    score = title_similarity(claimed, other)
    assert score is not None and score > DEFAULT_THRESHOLDS.title_similarity

    claim = ReferenceClaim(title=claimed, year=2021, authors=["Kessler, Marta"])
    assessment = assess_match(claim, record(title=other, year=2022, surname="Cho"))
    assert not assessment.matched
    assert "first_author_surname" in assessment.reasons


def test_title_similarity_is_none_without_a_title() -> None:
    assert title_similarity("", "anything") is None


def test_a_missing_year_is_not_a_disagreement() -> None:
    """Absence of a field in the bib entry is not evidence against the reference."""
    claim = ReferenceClaim(title="Sharing Detailed Research Data", authors=["Piwowar, H."])
    assessment = assess_match(claim, record())
    assert assessment.year_delta is None
    assert "year" not in assessment.reasons


def test_year_outside_tolerance_disagrees() -> None:
    claim = ReferenceClaim(
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        year=2001,
        authors=["Piwowar, Heather"],
    )
    assessment = assess_match(claim, record(year=2007))
    assert assessment.reasons == ("year",)
    assert assessment.year_delta == 6
    # A wrong year is not a wrong work: identity is intact, only the date disagrees.
    assert assessment.identity_broken is False


def test_year_within_tolerance_matches() -> None:
    claim = ReferenceClaim(
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        year=2006,
        authors=["Piwowar, Heather"],
    )
    assert assess_match(claim, record(year=2007)).matched


def test_surname_overlap_tolerates_initials_and_ordering() -> None:
    claim = ReferenceClaim(
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        year=2007,
        authors=["Fridsma, D. B.", "Piwowar, H. A."],
    )
    assert assess_match(claim, record()).matched


def test_a_different_first_author_breaks_identity() -> None:
    claim = ReferenceClaim(
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        year=2007,
        authors=["Vaswani, Ashish"],
    )
    assessment = assess_match(claim, record())
    assert "first_author_surname" in assessment.reasons
    assert assessment.identity_broken is True


def test_the_preprint_year_relaxation_needs_a_strong_title_and_author_match() -> None:
    """It only ever relaxes the year, and only when identity is already established."""
    preprint = record(
        title="Mask R-CNN", year=2017, surname="He", doi="10.48550/arxiv.1703.06870"
    )
    same_work = ReferenceClaim(title="Mask R-CNN", year=2020, authors=["He, Kaiming"])
    assert assess_match(same_work, preprint).matched
    assert assess_match(same_work, preprint).year_relaxed

    other_work = ReferenceClaim(title="Attention Is All You Need", year=2020, authors=["He, K."])
    assessment = assess_match(other_work, preprint)
    assert not assessment.matched
    assert "title_similarity" in assessment.reasons  # relaxation cannot rescue a wrong title


def test_the_year_relaxation_does_not_apply_to_a_journal_article() -> None:
    journal = record(year=2007)
    claim = ReferenceClaim(
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        year=2010,
        authors=["Piwowar, H."],
    )
    assert not assess_match(claim, journal).matched


# ---------------------------------------------------------------------------
# The gold set, replayed offline
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A source that does not hold a DOI has no opinion about it
#
# The refusal-grade false positives measured in evals/BENCHMARKS.md (3/99, of which these
# rules kill all three). An index that does not mint or hold a DOI, asked about it, must
# return a CLEAN NEGATIVE. It must not fall back to a title search, find a similar-looking
# record, and report `doi_mismatch`, because it never had an opinion about the identifier at
# all. Two such non-holding indexes were outvoting one genuine confirmation and producing a
# refusal-grade `mismatch` on real, correctly cited references.
# ---------------------------------------------------------------------------


def title_hit(source: str) -> SourceOutcome:
    """A source that does NOT hold the claimed DOI and found the work by title search."""
    return outcome(
        source,
        NEGATIVE,
        resolved=True,
        matched_by=BY_TITLE_SEARCH,
        mismatch_reasons=("doi_mismatch",),
        matched_record=record(doi="10.5555/some.other.id"),
    )


def test_a_title_search_hit_is_not_a_resolving_disagreement() -> None:
    hit = title_hit("datacite")
    assert hit.disagrees is False  # it never resolved the identifier: it cannot disagree
    assert hit.resolved_identifier is False
    assert hit.found_work_under_another_id is True


def test_two_non_holding_indexes_cannot_outvote_one_confirmation() -> None:
    """10.5281/zenodo.21358727, measured: DataCite confirms, the other two do not hold it.

    Under the old rule the two title-search hits were counted as resolving disagreements and
    D9 rule 2 fired: refusal-grade `mismatch` on a real Zenodo dataset. It must be
    `inconclusive` (one confirmation, below the bar), which is never refusal-grade.
    """
    decision = decide(
        [outcome("datacite", CONFIRMED), title_hit("openalex"), title_hit("crossref")]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_a_non_holding_index_cannot_disagree_with_a_resolving_one() -> None:
    """10.1109/taffc.2020.3014842, measured: OpenAlex confirms, Crossref resolves with a year
    two off (IEEE early access versus issue), DataCite does not mint the DOI at all.

    One resolving disagreement plus one confirmation is thin evidence, not a refusal.
    """
    decision = decide(
        [
            outcome("openalex", CONFIRMED),
            outcome(
                "crossref",
                NEGATIVE,
                resolved=True,
                matched_by=BY_IDENTIFIER,
                mismatch_reasons=("year",),
            ),
            title_hit("datacite"),
        ]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_wrong_doi_on_a_real_paper_is_still_a_mismatch() -> None:
    """Rule 2b, the ONLY route by which a title-search hit may convict an identifier.

    Nobody resolved the DOI, nobody errored, and two indexes hold the work under a different
    identifier. The work is real and the identifier in the entry is not its own.
    """
    decision = decide([title_hit("openalex"), title_hit("crossref"), outcome("datacite", NEGATIVE)])
    assert decision.verdict == MISMATCH
    assert decision.refusal_grade is True
    assert decision.disagreements == ("openalex", "crossref")


def test_rule_2b_needs_two_sources_not_one() -> None:
    """One index's fuzzy title hit is never enough to call an identifier wrong."""
    decision = decide([title_hit("openalex"), outcome("crossref", NEGATIVE)])
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_rule_2b_is_unreachable_once_any_source_errored() -> None:
    """A downed index might have been the one holding the DOI. Same law as `unresolvable`."""
    decision = decide(
        [
            title_hit("openalex"),
            title_hit("crossref"),
            outcome("datacite", SOURCE_ERROR, error={"type": "timeout", "message": "down"}),
        ]
    )
    assert decision.verdict == INCONCLUSIVE
    assert decision.refusal_grade is False


def test_one_source_resolving_the_doi_strips_the_non_holding_indexes_of_a_say() -> None:
    """The DOI exists. Whatever the indexes that do not hold it found by title is irrelevant."""
    decision = decide(
        [
            outcome("datacite", CONFIRMED),
            outcome("semantic_scholar", CONFIRMED),
            title_hit("openalex"),
            title_hit("crossref"),
        ]
    )
    assert decision.verdict == VERIFIED
    assert decision.refusal_grade is False


# ---------------------------------------------------------------------------
# Truncated titles: an exact prefix is truncation, not a different paper
# ---------------------------------------------------------------------------

DANS_TITLE = (
    "DO Beatrixstraat 2-12 Zetten Vondsten van laat-middeleeuwse en nieuwetijdse dorpsbewoning "
    "in plangebied Beatrixstraat 2-12 te Zetten, gemeente Overbetuwe. Een inventariserend "
    "veldonderzoek door middel van proefsleuven (IVO-P) en een opgraving (DO)"
)
DANS_TITLE_TRUNCATED = (
    "DO Beatrixstraat 2-12 Zetten Vondsten van laat-middeleeuwse en nieuwetijdse dorpsbewoning"
)


def test_a_long_exact_prefix_is_read_as_truncation() -> None:
    """10.17026/ar/sraj8f, measured: a real DANS dataset whose title a harvester cut at 88
    characters. DataCite resolves the DOI, token_sort_ratio scores 0.539 against the full
    title (the real one runs nearly three times as long, so the 40%-of-the-longer guard also
    refuses to fire), and the verdict was refusal-grade `mismatch` on an honest reference.
    """
    base = fuzz.token_sort_ratio(
        title_fingerprint(DANS_TITLE_TRUNCATED), title_fingerprint(DANS_TITLE)
    ) / 100.0
    assert base < DEFAULT_THRESHOLDS.title_similarity  # what the base ratio alone scores
    assert title_similarity(DANS_TITLE_TRUNCATED, DANS_TITLE) == 1.0


def test_the_prefix_rule_does_not_fire_on_a_short_prefix() -> None:
    """The guard against the rule's own risk: a short shared opening is NOT identity.

    Two different papers routinely open with the same handful of words. The rule requires an
    exact prefix of at least `truncated_prefix_min_chars`, which no such opening reaches.
    """
    short = "Deep learning for medical"  # 25 chars: a real, common title opening
    longer = "Deep learning for medical image segmentation: a comprehensive survey"
    assert len(title_fingerprint(short)) < DEFAULT_THRESHOLDS.truncated_prefix_min_chars
    score = title_similarity(short, longer)
    assert score is not None and score < 1.0


def test_the_prefix_rule_is_exact_not_fuzzy() -> None:
    """A title that DIVERGES from the longer one is not a truncation of it, however early."""
    claimed = "Cardiologist-level arrhythmia detection using support vector machines in ambulatory"
    found = (
        "Cardiologist-level arrhythmia detection and classification in ambulatory "
        "electrocardiograms using a deep neural network"
    )
    score = title_similarity(claimed, found)
    assert score is not None and score < 1.0


def test_the_prefix_rule_lifts_the_title_only_and_the_author_check_still_stands() -> None:
    """It cannot confirm a different work: year and first author are still checked."""
    claim = ReferenceClaim(
        title=DANS_TITLE_TRUNCATED, year=2026, authors=["Someone, Entirely Different"]
    )
    assessment = assess_match(claim, record(title=DANS_TITLE, year=2026, surname="Bakker"))
    assert assessment.similarity == 1.0
    assert not assessment.matched
    assert "first_author_surname" in assessment.reasons
    assert assessment.identity_broken is True


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# ScieFlow-authored (M-C2 t1, plan item 5): the axis (a) report validates against the vendored schema
# ---------------------------------------------------------------------------


def test_build_report_over_hand_made_entries_validates_against_the_vendored_schema() -> None:
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    from scieflow._vendor.researcher_core.verify import VerificationEntry, build_report

    schema_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "scieflow"
        / "_vendor"
        / "researcher_core"
        / "schemas"
        / "verification-report.schema.json"
    )
    claim = ReferenceClaim(
        key="piwowar2007",
        title="Sharing Detailed Research Data Is Associated with Increased Citation Rate",
        doi="10.1371/journal.pone.0000308",
        year=2007,
        authors=["Piwowar, Heather A."],
    )
    outcomes = [outcome("openalex", CONFIRMED), outcome("crossref", CONFIRMED)]
    entry = VerificationEntry(claim=claim, decision=decide(outcomes), outcomes=outcomes)
    report = build_report(
        [entry], sources=["openalex", "crossref"], input_kind="bib", input_path="library.bib"
    )
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(report)
    assert report["entries"][0]["verdict"] == VERIFIED
    assert report["entries"][0]["refusal_grade"] is False
