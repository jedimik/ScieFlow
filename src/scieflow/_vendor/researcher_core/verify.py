"""Axis (a): reference identity (D9, D16).

Does the reference a manuscript cites actually exist, and is it the work the entry claims
it is? Nothing else. Whether that work was later retracted is axis (b) (``status.py``);
whether it supports the sentence citing it is axis (c) (``faithfulness.py``). The axes are
independent and are reported side by side, never folded into one verdict.

Two vocabularies, and they must never be conflated.

**Per-source outcome** (what one index said):

* ``confirmed``    the source resolved the reference and the metadata matched within thresholds
* ``negative``     the query succeeded and produced no match within thresholds (a clean negative)
* ``source_error`` timeout, rate limit, 5xx, network failure: no clean answer was obtained

A ``negative`` splits further, and the split is load-bearing. ``resolved=False`` means the
source cleanly found nothing. ``resolved=True`` with ``mismatch_reasons`` means the source
found a record and it disagrees beyond thresholds. HOW the source found that record then
decides what the disagreement is worth (:attr:`SourceOutcome.matched_by`):

* ``identifier``   the source looked up the claimed DOI or arXiv ID and got a record back.
  Only such a source has an opinion ABOUT THE IDENTIFIER, so only such a source can produce
  a resolving disagreement, which is evidence for ``mismatch``.
* ``title_search`` the identifier did not resolve there and the source found a similar work
  by title. That is a record about a WORK, not about the identifier: an index that does not
  hold a DOI has no opinion about it, and "I do not have this DOI" is a clean negative, never
  "this DOI belongs to a different paper".

**Reference-level verdict** (what the report says), exactly four states:

* ``verified``      >= 2 sources confirmed
* ``mismatch``      a resolving source disagrees beyond thresholds (wrong DOI, mangled entry)
* ``unresolvable``  ALL queried sources returned a clean negative and none confirmed
* ``inconclusive``  evidence too thin or too dirty to decide

ONLY ``unresolvable`` and ``mismatch`` are refusal-grade. ``inconclusive`` is NEVER
refusal-grade. See :func:`decide`, where the precedence is implemented, for why that line is
the most important one in this file.

Thresholds are calibrated against ``core/tests/snapshots/verify-gold/`` and recorded in
``core/CALIBRATION.md``. The values in :data:`DEFAULT_THRESHOLDS` are the ones that file
documents; the report carries them so a verdict can never be read without the rule that
produced it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ._fuzz import fuzz

from ._deps import PARSER_VERSION, __version__
from ._deps import BaseConnector, SourceError, create_connector
from .model import CSLRecord, normalize_doi, normalize_title, title_fingerprint
from ._deps import SnapshotSession
from ._deps import StatusEntry, check_status_async

__all__ = [
    "BY_IDENTIFIER",
    "BY_TITLE_SEARCH",
    "DEFAULT_SOURCES",
    "DEFAULT_THRESHOLDS",
    "IDENTITY_PROTOCOL_VERSION",
    "REPORT_SCHEMA_VERSION",
    "Decision",
    "MatchAssessment",
    "ReferenceClaim",
    "SourceOutcome",
    "VerificationEntry",
    "assess_match",
    "build_report",
    "decide",
    "is_refusal_grade",
    "title_similarity",
    "verify_claim_async",
    "verify_claims",
    "verify_claims_async",
]

#: Version of the report shape (``core/schemas/verification-report.schema.json``).
REPORT_SCHEMA_VERSION = "1.0"

#: Version of the D9 decision rules implemented in :func:`decide` and :func:`assess_match`.
#: Bump it whenever a threshold or a precedence rule changes, because a stored verdict is
#: only interpretable against the rulebook that produced it.
#:
#: 1.1: a ``doi_mismatch`` from a source that never resolved the identifier is no longer a
#: resolving disagreement (rule 2b of :func:`decide`), and an exact title prefix of at least
#: ``truncated_prefix_min_chars`` is read as truncation. Both fix refusal-grade false
#: positives measured on the axis (a) gold set; see ``core/CALIBRATION.md``.
IDENTITY_PROTOCOL_VERSION = "1.1"

#: The sources queried when a caller names none. OpenAlex and Crossref alone satisfy the D9
#: two-confirmation gate for journal literature; DataCite covers the DOIs Crossref does not
#: mint (datasets, software, arXiv preprints), so a preprint or a dataset citation can reach
#: two confirmations instead of falling to ``inconclusive`` for want of an index that holds it.
DEFAULT_SOURCES: tuple[str, ...] = ("openalex", "crossref", "datacite")

#: Preference order for the axis (d) OA cascade. Sources absent from the connector set are
#: skipped; the first hit wins and the rest are not consulted.
OA_CASCADE_ORDER: tuple[str, ...] = ("unpaywall", "openalex", "semantic_scholar", "arxiv", "pubmed")

CONFIRMED = "confirmed"
NEGATIVE = "negative"
SOURCE_ERROR = "source_error"

VERIFIED = "verified"
MISMATCH = "mismatch"
UNRESOLVABLE = "unresolvable"
INCONCLUSIVE = "inconclusive"

#: The two refusal-grade verdicts, and the only two. A refusal-grade consumer (the citation
#: audit, the compile gate, the commit hook) may act ONLY on these.
REFUSAL_GRADE: frozenset[str] = frozenset({UNRESOLVABLE, MISMATCH})

# Crossref/DataCite/OpenAlex source-error kinds map onto the schema's error vocabulary.
_ERROR_TYPE_MAP = {
    "timeout": "timeout",
    "rate_limit": "rate_limit",
    "server_error": "http_error",
    "network": "network",
    "bad_response": "parse_error",
    "config": "auth_error",
}


def is_refusal_grade(verdict: str) -> bool:
    """True only for ``unresolvable`` and ``mismatch``. Never for ``inconclusive``."""
    return verdict in REFUSAL_GRADE


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    """The calibrated D9 thresholds. See ``core/CALIBRATION.md``.

    ``title_similarity``, ``year_tolerance``, ``require_first_author_surname`` and
    ``min_confirmations`` are the four D9 knobs and are the four the report emits.

    The remaining three are the calibrated refinements the gold subset forced, each of which
    only ever RELAXES a check, and only when identity is already strongly established by
    title and author. None of them can turn a non-matching record into a confirmation.

    * ``strong_title_similarity``: the bar a title must clear before any relaxation applies.
    * ``preprint_year_tolerance``: a preprint and its journal version are the same work with
      two DOIs and two years, often three years apart. Citing the published year against the
      preprint DOI is correct scholarship, and under a flat +/-1 year rule two indexes would
      both "disagree" and produce a refusal-grade ``mismatch`` on a real, honest reference.
      That is the worst failure this system can have, so the year window widens (only) when
      the resolved record is a preprint AND the title and first author already match.
    * ``truncation_min_chars``: a bib entry whose title was cut short is still the same work.
      Prefix truncation is detected explicitly rather than by a generic partial ratio, which
      would inflate the similarity of unrelated titles that happen to share a phrase.
    * ``truncated_prefix_min_chars``: the length at which an EXACT character-for-character
      prefix is accepted as truncation on its own, without the 40%-of-the-longer guard that
      ``truncation_min_chars`` carries. Reference managers and harvesters really do cut titles
      to a fixed width, and a dataset title can run four times that width, so the ratio guard
      refuses exactly the case it exists to serve. An exact 60-character prefix is roughly ten
      words of a title reproduced verbatim; it is truncation, not a different paper. The rule
      lifts the TITLE score only: the year and first-author checks still stand behind it.
    """

    title_similarity: float = 0.70
    year_tolerance: int = 1
    require_first_author_surname: bool = True
    min_confirmations: int = 2
    strong_title_similarity: float = 0.90
    preprint_year_tolerance: int = 3
    truncation_min_chars: int = 20
    truncated_prefix_min_chars: int = 60

    def to_json_dict(self) -> dict[str, Any]:
        """Only the four D9 knobs; the report schema pins these and forbids extras."""
        return {
            "title_similarity": self.title_similarity,
            "year_tolerance": self.year_tolerance,
            "require_first_author_surname": self.require_first_author_surname,
            "min_confirmations": self.min_confirmations,
        }


DEFAULT_THRESHOLDS = Thresholds()


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


@dataclass
class ReferenceClaim:
    """A reference AS CLAIMED, before any source resolution.

    This is what a ``.bib`` entry or a user-typed reference asserts. It is kept verbatim so
    a mismatch can be shown as claimed versus found.
    """

    key: str = ""
    title: str = ""
    doi: str = ""
    arxiv_id: str = ""
    year: int | None = None
    authors: list[str] = field(default_factory=list)
    container_title: str = ""
    entry_type: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        self.doi = normalize_doi(self.doi)
        self.title = normalize_title(self.title)
        self.container_title = normalize_title(self.container_title)
        self.authors = [normalize_title(a) for a in self.authors if str(a).strip()]
        if not self.key:
            self.key = self.doi or (title_fingerprint(self.title)[:48] or "reference")

    @property
    def surnames(self) -> list[str]:
        """Claimed author surnames, in order, casefolded."""
        return [s for s in (_surname_of(a) for a in self.authors) if s]

    @classmethod
    def from_record(cls, record: CSLRecord, *, key: str = "") -> ReferenceClaim:
        return cls(
            key=key or record.id,
            title=record.title,
            doi=record.DOI,
            arxiv_id=record.arxiv_id,
            year=record.year,
            authors=[a.display() for a in record.author],
            container_title=record.container_title,
            entry_type=record.type,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ReferenceClaim:
        """Build from a plain dict, which is what a ``.bib`` parser hands over."""
        authors = data.get("authors") or data.get("author") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(" and ") if a.strip()]
        year = data.get("year")
        try:
            year_int = int(str(year)) if str(year or "").strip() else None
        except ValueError:
            year_int = None
        return cls(
            key=str(data.get("key") or ""),
            title=str(data.get("title") or ""),
            doi=str(data.get("doi") or data.get("DOI") or ""),
            arxiv_id=str(data.get("arxiv_id") or data.get("eprint") or ""),
            year=year_int,
            authors=[str(a) for a in authors],
            container_title=str(
                data.get("container_title") or data.get("journal") or data.get("booktitle") or ""
            ),
            entry_type=str(data.get("entry_type") or data.get("type") or ""),
            raw=str(data.get("raw") or ""),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "title": self.title or None,
            "doi": self.doi or None,
            "arxiv_id": self.arxiv_id or None,
            "year": self.year,
            "authors": list(self.authors),
            "container_title": self.container_title or None,
            "entry_type": self.entry_type or None,
            "raw": self.raw or None,
        }


def _surname_of(name: str) -> str:
    """The family name of a free-form author string, casefolded ("Piwowar, H. A." -> piwowar)."""
    from .model import parse_name

    return parse_name(name).surname.casefold()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def title_similarity(
    claimed: str,
    found: str,
    *,
    truncation_min_chars: int = 20,
    truncated_prefix_min_chars: int = 60,
) -> float | None:
    """Similarity of two titles, 0.0 to 1.0. ``None`` when either side has no title.

    Base score is rapidfuzz ``token_sort_ratio`` over the title fingerprints (lowercased,
    punctuation stripped, whitespace collapsed), so word order and punctuation do not matter.

    Truncation is then handled explicitly, in two tiers, because a bib entry whose title was
    cut short by a citation manager is the same work and the base ratio punishes it purely for
    the words that were dropped.

    1. **Exact prefix containment.** The shorter fingerprint is character-for-character the
       opening of the longer one, and is at least ``truncated_prefix_min_chars`` long. That is
       truncation, and it is scored 1.0. This tier exists for the case the second tier cannot
       reach: a harvester or a reference manager that cuts every title to a fixed width, against
       a dataset or report title that runs several times that width. There the shorter title is
       a small FRACTION of the longer one, so the ratio guard below refuses it, and the base
       token_sort_ratio collapses (0.539 on the DANS archaeology dataset that this rule was
       added for), which on a resolving source is a refusal-grade ``mismatch`` against an honest
       reference. The tier is deliberately narrow: an exact prefix, not a fuzzy one, and long
       enough (roughly ten words) that two different papers sharing it is not a real risk. It
       lifts the TITLE score only, and the year and first-author checks still stand behind it.
    2. **Fuzzy leading window.** The shorter fingerprint is at least ``truncation_min_chars``
       long AND at least 40% of the longer one, so it is compared against the longer's leading
       window of the same length. This absorbs a truncation that also mangled a character. A
       generic ``partial_ratio`` would cover both tiers but would also lift unrelated titles
       that merely share a phrase ANYWHERE, so the relaxation stays anchored to the head.
    """
    a = title_fingerprint(claimed)
    b = title_fingerprint(found)
    if not a or not b:
        return None
    score = fuzz.token_sort_ratio(a, b) / 100.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < len(long):
        if len(short) >= truncated_prefix_min_chars and long.startswith(short):
            score = 1.0
        elif len(short) >= truncation_min_chars and len(short) >= 0.4 * len(long):
            prefix = fuzz.ratio(short, long[: len(short)]) / 100.0
            score = max(score, prefix)
    return round(min(1.0, score), 4)


def _is_preprint(record: CSLRecord) -> bool:
    """Is this resolved record a preprint (rather than the version of record)?"""
    if record.arxiv_id or record.DOI.startswith("10.48550/"):
        return True
    if record.DOI.startswith(("10.1101/", "10.31234/", "10.31219/", "10.21203/")):
        return True  # bioRxiv/medRxiv, PsyArXiv, OSF preprints, Research Square
    crossref_type = str(record.extra.get("crossref_type") or "")
    return crossref_type == "posted-content"


@dataclass(frozen=True)
class MatchAssessment:
    """Whether one resolved record matches the claim, and on which checks it failed."""

    similarity: float | None
    year_delta: int | None
    surname_overlap: bool | None
    reasons: tuple[str, ...]
    matched: bool
    year_relaxed: bool = False

    @property
    def identity_broken(self) -> bool:
        """True when the record fails a check that goes to identity itself.

        A title that does not match, or a first author who does not appear, means the record
        is a DIFFERENT work from the one claimed. A year outside tolerance, on its own, does
        not: it means the same work carries a different date somewhere.
        """
        return bool({"title_similarity", "first_author_surname"} & set(self.reasons))


def assess_match(
    claim: ReferenceClaim,
    record: CSLRecord,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> MatchAssessment:
    """Compare a resolved record against the claim under the calibrated thresholds.

    A check whose input is missing (the claim carries no year, the record carries no authors)
    is reported as ``None`` and is NOT counted as a failure: absence of a field in the bib
    entry is not disagreement, and treating it as such would manufacture mismatches out of
    sparse but honest entries.
    """
    reasons: list[str] = []

    similarity = title_similarity(
        claim.title,
        record.title,
        truncation_min_chars=thresholds.truncation_min_chars,
        truncated_prefix_min_chars=thresholds.truncated_prefix_min_chars,
    )
    if similarity is not None and similarity < thresholds.title_similarity:
        reasons.append("title_similarity")

    claimed_surnames = claim.surnames
    record_surnames = [a.surname.casefold() for a in record.author if a.surname]
    surname_overlap: bool | None = None
    if claimed_surnames and record_surnames:
        surname_overlap = _surnames_overlap(claimed_surnames, record_surnames)
        if thresholds.require_first_author_surname and not surname_overlap:
            reasons.append("first_author_surname")

    strong_identity = (
        similarity is not None
        and similarity >= thresholds.strong_title_similarity
        and surname_overlap is not False
    )

    year_delta: int | None = None
    year_relaxed = False
    if claim.year is not None and record.year is not None:
        year_delta = record.year - claim.year
        tolerance = thresholds.year_tolerance
        if strong_identity and _is_preprint(record):
            # The claim cites the journal version's year against the preprint's DOI, or the
            # reverse. Same work, two DOIs, two years. Widening the window here (and ONLY
            # here, behind a strong title and author match) is what keeps an honest preprint
            # citation out of the refusal-grade `mismatch` class. See CALIBRATION.md.
            tolerance = thresholds.preprint_year_tolerance
            year_relaxed = abs(year_delta) > thresholds.year_tolerance
        if abs(year_delta) > tolerance:
            reasons.append("year")
            year_relaxed = False

    return MatchAssessment(
        similarity=similarity,
        year_delta=year_delta,
        surname_overlap=surname_overlap,
        reasons=tuple(reasons),
        matched=not reasons,
        year_relaxed=year_relaxed,
    )


def _surnames_overlap(claimed: Sequence[str], found: Sequence[str]) -> bool:
    """Does the claimed first author appear among the found authors, or the reverse?

    Compared with a fuzzy ratio rather than equality, because transliteration and diacritics
    differ across indexes ("Nunez" versus "Nunez"), and checked in both directions, because
    author order is not always preserved by a source.
    """
    candidates = [(claimed[0], found), (found[0], claimed)]
    for needle, haystack in candidates:
        for other in haystack:
            if needle == other or fuzz.ratio(needle, other) >= 90:
                return True
    return False


# ---------------------------------------------------------------------------
# Per-source outcomes
# ---------------------------------------------------------------------------


#: How a source came to hold the record it reports. ``BY_IDENTIFIER`` means it looked up the
#: claimed DOI or arXiv ID and got a record back, so it HAS an opinion about that identifier.
#: ``BY_TITLE_SEARCH`` means the identifier did not resolve there and the record was found by
#: title, so the source has an opinion about the WORK and none at all about the identifier.
BY_IDENTIFIER = "identifier"
BY_TITLE_SEARCH = "title_search"


@dataclass
class SourceOutcome:
    """What one queried source returned about one reference."""

    source: str
    outcome: str
    matched_record: CSLRecord | None = None
    similarity: float | None = None
    year_delta: int | None = None
    surname_overlap: bool | None = None
    resolved: bool = False
    mismatch_reasons: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    #: ``identifier``, ``title_search``, or "" when the source matched nothing. Not emitted in
    #: the report JSON, which already carries the same fact: a ``doi_mismatch`` reason is
    #: produced by the title-search path and by nothing else.
    matched_by: str = ""

    @property
    def resolved_identifier(self) -> bool:
        """Did this source look up the claimed identifier and get a record back for it?"""
        return self.resolved and self.matched_by == BY_IDENTIFIER

    @property
    def disagrees(self) -> bool:
        """A RESOLVING disagreement: the source held the identifier and the metadata differs.

        The ``resolved_identifier`` half is the whole point, and a future refactor must not
        drop it as redundant. A source that never resolved the identifier, and merely found a
        similar-titled work, has NO opinion about the identifier: it cannot disagree about one.
        See :func:`decide`.
        """
        return (
            self.outcome == NEGATIVE
            and self.resolved_identifier
            and bool(self.mismatch_reasons)
        )

    @property
    def found_work_under_another_id(self) -> bool:
        """The source did not hold the claimed identifier but did find the claimed WORK.

        Evidence that the work is real. Evidence about the identifier ONLY when no source
        anywhere resolved it; see rule (2b) of :func:`decide`.
        """
        return (
            self.outcome == NEGATIVE
            and self.resolved
            and self.matched_by == BY_TITLE_SEARCH
            and "doi_mismatch" in self.mismatch_reasons
        )

    @property
    def clean_negative(self) -> bool:
        """A source that answered and cleanly found nothing."""
        return self.outcome == NEGATIVE and not self.resolved

    def to_json_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": self.source,
            "outcome": self.outcome,
            "resolved": self.resolved,
            "matched_id": self.matched_record.id if self.matched_record else None,
            "matched_record": (
                self.matched_record.to_csl_json() if self.matched_record else None
            ),
            "title_similarity": self.similarity,
            "year_delta": self.year_delta,
            "surname_overlap": self.surname_overlap,
            "mismatch_reasons": list(self.mismatch_reasons),
        }
        # The schema requires `error` exactly when the outcome is source_error, and forbids
        # it otherwise. That is not a formality: a typed error is what marks an answer dirty.
        if self.outcome == SOURCE_ERROR:
            out["error"] = self.error or {"type": "network", "message": "unspecified failure"}
        return out


def _error_dict(exc: SourceError) -> dict[str, Any]:
    return {
        "type": _ERROR_TYPE_MAP.get(exc.kind.value, "network"),
        "message": exc.message or str(exc),
        "http_status": exc.status_code,
    }


# ---------------------------------------------------------------------------
# The D9 precedence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """The reference-level verdict, why it fired, and which sources were overruled."""

    verdict: str
    reason: str
    disagreements: tuple[str, ...] = ()

    @property
    def refusal_grade(self) -> bool:
        return is_refusal_grade(self.verdict)


def decide(
    outcomes: Sequence[SourceOutcome],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Decision:
    """Aggregate per-source outcomes into the reference-level verdict (D9).

    THE LINE THAT MATTERS. Only ``unresolvable`` and ``mismatch`` are refusal-grade, which
    is to say: only those two let a consumer tell a researcher that a citation appears
    fabricated or wrong. ``inconclusive`` means "we could not tell" and is NEVER
    refusal-grade. Confusing the two is the single worst failure this system can have:
    a rate-limited index, a paper held by only one index, or a network blip would accuse an
    honest researcher of fabricating a real citation. So a ``source_error`` can never produce
    ``unresolvable``, and a lone disagreeing index can never overturn a two-source
    confirmation. Both properties are enforced below, in this order, top-down.

    THE SECOND LINE THAT MATTERS, and the one a refactor will want to delete as redundant:
    ``disagreeing`` counts only sources that RESOLVED THE IDENTIFIER THEMSELVES. A source that
    does not hold a DOI has no opinion about it. Ask DataCite about a Crossref-minted DOI and
    the honest answer is "I do not have this", which is a CLEAN NEGATIVE. It is emphatically
    not "this DOI belongs to a different paper", and it must not become that just because a
    title search then turned up a similar-looking record. Manufacturing a disagreement out of
    an absence is the same class of error as reading a rate limit as evidence of fabrication,
    and it is what D9 exists to forbid. Measured: two non-holding indexes doing exactly that
    outvoted one genuine confirmation and produced refusal-grade false positives on real
    references (``10.1109/taffc.2020.3014842``, ``10.5281/zenodo.21358727``); see
    ``evals/BENCHMARKS.md``. A title-search hit can therefore SUPPORT a confirmation, and it
    can convict a claimed identifier only in rule (2b), where NO source resolved it at all.
    """
    confirmed = [o for o in outcomes if o.outcome == CONFIRMED]
    errors = [o for o in outcomes if o.outcome == SOURCE_ERROR]
    disagreeing = [o for o in outcomes if o.disagrees]
    clean_negatives = [o for o in outcomes if o.clean_negative]
    work_found_elsewhere = [o for o in outcomes if o.found_work_under_another_id]
    identifier_resolved_somewhere = any(o.resolved_identifier for o in outcomes)

    # (1) verified. A two-confirmation majority OUTRANKS any single disagreeing or erroring
    # source: one bad index must never drag a corroborated reference into a refusal-grade
    # verdict. The disagreement is logged in `disagreements`, and acted on by nobody.
    if len(confirmed) >= thresholds.min_confirmations:
        names = tuple(o.source for o in disagreeing)
        reason = f"{len(confirmed)} sources confirmed ({', '.join(o.source for o in confirmed)})"
        if names:
            reason += f"; disagreement from {', '.join(names)} logged and outranked"
        if errors:
            reason += f"; {len(errors)} source(s) errored"
        return Decision(VERIFIED, reason, names)

    # (2) mismatch. No two-confirmation majority, AND either two resolving sources both
    # disagree beyond thresholds, or exactly one resolves and disagrees with no confirmation
    # anywhere and no source_error (a clean, unanimous, single-source disagreement).
    if len(disagreeing) >= 2:
        detail = "; ".join(
            f"{o.source}: {', '.join(o.mismatch_reasons)}" for o in disagreeing
        )
        return Decision(
            MISMATCH,
            f"{len(disagreeing)} sources resolved the reference and disagreed beyond "
            f"thresholds ({detail})",
            tuple(o.source for o in disagreeing),
        )
    if len(disagreeing) == 1 and not confirmed and not errors:
        only = disagreeing[0]
        return Decision(
            MISMATCH,
            f"{only.source} resolved the reference and disagreed beyond thresholds "
            f"({', '.join(only.mismatch_reasons)}); no source confirmed it and no source errored",
            (only.source,),
        )

    # (2b) mismatch, the wrong-DOI-on-a-real-paper case, and the ONLY route by which a
    # title-search hit may convict an identifier. It requires that NOT ONE source resolved the
    # claimed identifier (so the identifier is held nowhere, not merely absent from the index
    # that happened to answer), that nothing errored (so that absence is a clean finding rather
    # than an outage), and that the work itself was found under a different identifier by at
    # least `min_confirmations` sources (so one index's fuzzy title hit cannot do it alone).
    # A mistyped DOI on a real paper lands here. A Zenodo DOI that Crossref does not mint does
    # NOT, because DataCite resolved it, and one source resolving the identifier is enough to
    # prove it exists and to strip every non-holding index of any say in the matter.
    if (
        work_found_elsewhere
        and not identifier_resolved_somewhere
        and not errors
        and not confirmed
        and len(work_found_elsewhere) >= thresholds.min_confirmations
    ):
        names = tuple(o.source for o in work_found_elsewhere)
        return Decision(
            MISMATCH,
            f"the claimed identifier resolved at no queried source, while {len(names)} sources "
            f"({', '.join(names)}) found the claimed work under a different identifier by title "
            "search; the work is real and the identifier in the entry is not its own",
            names,
        )

    # (3) unresolvable. Every queried source answered cleanly, and every one of them found
    # nothing. This is the only "likely fabricated" state, and it requires a full sweep of
    # CLEAN negatives: a single source_error below would make it unreachable, by design.
    if outcomes and len(clean_negatives) == len(outcomes):
        queried = ", ".join(o.source for o in outcomes)
        return Decision(
            UNRESOLVABLE,
            f"all {len(outcomes)} queried sources returned a clean negative ({queried}) "
            "and none confirmed the reference",
        )

    # (4) inconclusive. Everything else: thin evidence (exactly one confirmation, which a
    # legitimate single-index paper produces) or dirty evidence (any source_error, so a clean
    # negative cannot be asserted). NEVER refusal-grade.
    if not outcomes:
        return Decision(INCONCLUSIVE, "no source was queried, so nothing can be asserted")
    parts: list[str] = []
    if confirmed:
        parts.append(
            f"only {len(confirmed)} source confirmed "
            f"({', '.join(o.source for o in confirmed)}), below the "
            f"{thresholds.min_confirmations}-confirmation bar"
        )
    if errors:
        parts.append(
            f"{len(errors)} source(s) could not be reached "
            f"({', '.join(o.source for o in errors)}), so a clean negative cannot be asserted"
        )
    if disagreeing and not parts:
        parts.append(
            f"{disagreeing[0].source} disagreed, but the evidence is too thin to call a mismatch"
        )
    if work_found_elsewhere:
        others = ", ".join(o.source for o in work_found_elsewhere)
        parts.append(
            f"{others} do not hold the claimed identifier and found the work by title search, "
            "which is no opinion about the identifier and cannot support a mismatch"
        )
    if not parts:
        parts.append("the evidence is too thin to decide")
    return Decision(INCONCLUSIVE, "; ".join(parts) + "; NOT refusal-grade")


# ---------------------------------------------------------------------------
# Querying one source
# ---------------------------------------------------------------------------


async def query_source(
    connector: BaseConnector,
    claim: ReferenceClaim,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    search_limit: int = 5,
) -> SourceOutcome:
    """Ask one source about one reference and classify what came back.

    Lookup order: the claimed DOI, then the claimed arXiv ID, then a title search. A
    :class:`SourceError` anywhere in that sequence produces ``source_error``, never a
    negative. A :class:`~researcher_core.snapshots.SnapshotMissingError` is deliberately NOT
    caught: a hole in the snapshot set is a defect in the eval, not a source outage, and
    swallowing it as ``source_error`` would quietly turn a broken test into a passing one.
    """
    name = connector.name
    try:
        record = await _resolve_identifier(connector, claim)
        if record is not None:
            assessment = assess_match(claim, record, thresholds)
            if assessment.matched:
                return SourceOutcome(
                    source=name,
                    outcome=CONFIRMED,
                    matched_record=record,
                    similarity=assessment.similarity,
                    year_delta=assessment.year_delta,
                    surname_overlap=assessment.surname_overlap,
                    resolved=True,
                    matched_by=BY_IDENTIFIER,
                )
            return SourceOutcome(
                source=name,
                outcome=NEGATIVE,
                matched_record=record,
                similarity=assessment.similarity,
                year_delta=assessment.year_delta,
                surname_overlap=assessment.surname_overlap,
                resolved=True,
                mismatch_reasons=assessment.reasons,
                matched_by=BY_IDENTIFIER,
            )

        return await _search_fallback(connector, claim, thresholds, search_limit)
    except SourceError as exc:
        return SourceOutcome(source=name, outcome=SOURCE_ERROR, error=_error_dict(exc))


async def _resolve_identifier(
    connector: BaseConnector, claim: ReferenceClaim
) -> CSLRecord | None:
    """Resolve the claimed DOI, then the claimed arXiv ID. ``None`` is a clean negative."""
    if claim.doi and connector.supports("resolve_doi"):
        record = await connector.resolve_doi(claim.doi)
        if record is not None:
            return record
    if claim.arxiv_id and connector.supports("get_by_id"):
        return await connector.get_by_id(claim.arxiv_id)
    return None


async def _search_fallback(
    connector: BaseConnector,
    claim: ReferenceClaim,
    thresholds: Thresholds,
    search_limit: int,
) -> SourceOutcome:
    """The identifier did not resolve HERE. Try the title, and read the result carefully.

    Three outcomes, and the middle one is the subtle one:

    * nothing found, or nothing that matches within thresholds -> a CLEAN NEGATIVE. A weak
      search hit never manufactures a mismatch; it is simply not this work.
    * a match found and the claim carried no DOI -> ``confirmed``. Title-only references are
      legitimate, and this is how they are verified.
    * a match found and the claim DID carry a DOI -> this source has the WORK, under some other
      identifier, and does not have the claimed one. It is tagged ``doi_mismatch`` /
      ``BY_TITLE_SEARCH``, and that tag is NOT a resolving disagreement: this source never
      looked the claimed DOI up successfully, so it has no opinion about it. Whether the
      claimed DOI is wrong is decided in :func:`decide`, from whether ANY source resolved it.
    """
    name = connector.name
    if not claim.title or not connector.supports("search"):
        return SourceOutcome(source=name, outcome=NEGATIVE, resolved=False)

    candidates = await connector.search(claim.title, limit=search_limit)
    best: CSLRecord | None = None
    best_assessment: MatchAssessment | None = None
    for candidate in candidates:
        assessment = assess_match(claim, candidate, thresholds)
        if not assessment.matched:
            continue
        if (
            best_assessment is None
            or (assessment.similarity or 0) > (best_assessment.similarity or 0)
        ):
            best, best_assessment = candidate, assessment

    if best is None or best_assessment is None:
        return SourceOutcome(source=name, outcome=NEGATIVE, resolved=False)

    if not claim.doi:
        return SourceOutcome(
            source=name,
            outcome=CONFIRMED,
            matched_record=best,
            similarity=best_assessment.similarity,
            year_delta=best_assessment.year_delta,
            surname_overlap=best_assessment.surname_overlap,
            resolved=True,
            matched_by=BY_TITLE_SEARCH,
        )

    return SourceOutcome(
        source=name,
        outcome=NEGATIVE,
        matched_record=best,
        similarity=best_assessment.similarity,
        year_delta=best_assessment.year_delta,
        surname_overlap=best_assessment.surname_overlap,
        resolved=True,
        mismatch_reasons=("doi_mismatch",),
        matched_by=BY_TITLE_SEARCH,
    )


# ---------------------------------------------------------------------------
# One reference, all sources
# ---------------------------------------------------------------------------


@dataclass
class VerificationEntry:
    """One reference: axis (a) verdict, every per-source outcome, axis (b), axis (d)."""

    claim: ReferenceClaim
    decision: Decision
    outcomes: list[SourceOutcome]
    best_match: CSLRecord | None = None
    status: StatusEntry | None = None
    accessibility: dict[str, Any] = field(
        default_factory=lambda: {"verdict": "unavailable", "cascade": []}
    )

    @property
    def verdict(self) -> str:
        return self.decision.verdict

    def tally(self) -> dict[str, int]:
        return {
            CONFIRMED: sum(1 for o in self.outcomes if o.outcome == CONFIRMED),
            NEGATIVE: sum(1 for o in self.outcomes if o.outcome == NEGATIVE),
            SOURCE_ERROR: sum(1 for o in self.outcomes if o.outcome == SOURCE_ERROR),
        }

    def to_json_dict(self) -> dict[str, Any]:
        # With no DOI, or with no status-carrying source in the connector set, axis (b) was
        # never asked. `checked: false` is the whole point: an unchecked status is an absence
        # of evidence, not evidence of currency, and must not read as a clean bill of health.
        status = (
            self.status.to_block_dict()
            if self.status is not None and self.status.sources
            else {"verdict": "current", "checked": False, "notices": [], "sources": []}
        )
        return {
            "key": self.claim.key,
            "reference": self.claim.to_json_dict(),
            "verdict": self.decision.verdict,
            "refusal_grade": self.decision.refusal_grade,
            "reason": self.decision.reason,
            "source_outcomes": [o.to_json_dict() for o in self.outcomes],
            "tally": self.tally(),
            "best_match": self.best_match.to_csl_json() if self.best_match else None,
            "disagreements": list(self.decision.disagreements),
            "status": status,
            "accessibility": self.accessibility,
        }


async def verify_claim_async(
    claim: ReferenceClaim,
    connectors: Sequence[BaseConnector],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    check_status: bool = True,
    check_accessibility: bool = True,
) -> VerificationEntry:
    """Verify one reference across every connector, then attach axes (b) and (d).

    Sources are queried concurrently but the outcomes are reported in the order the
    connectors were given, never in completion order: a report that reshuffles itself between
    identical runs is not deterministic (D15).
    """
    outcomes = list(
        await asyncio.gather(*(query_source(c, claim, thresholds) for c in connectors))
    )
    decision = decide(outcomes, thresholds)
    best_match = _best_match(outcomes)

    status: StatusEntry | None = None
    if check_status and claim.doi:
        status = await check_status_async(claim.doi, connectors, key=claim.key, title=claim.title)

    accessibility = {"verdict": "unavailable", "cascade": []}
    if check_accessibility:
        accessibility = await _accessibility(claim, connectors, best_match)

    return VerificationEntry(
        claim=claim,
        decision=decision,
        outcomes=outcomes,
        best_match=best_match,
        status=status,
        accessibility=accessibility,
    )


def _best_match(outcomes: Sequence[SourceOutcome]) -> CSLRecord | None:
    """The best record any source resolved: a confirmation first, then the best similarity."""
    confirmed = [o for o in outcomes if o.outcome == CONFIRMED and o.matched_record]
    pool = confirmed or [o for o in outcomes if o.matched_record is not None]
    if not pool:
        return None
    best = max(pool, key=lambda o: (o.similarity or 0.0))
    return best.matched_record


async def _accessibility(
    claim: ReferenceClaim,
    connectors: Sequence[BaseConnector],
    best_match: CSLRecord | None,
) -> dict[str, Any]:
    """Axis (d): what evidence depth was even possible for this reference.

    The OA cascade is walked in :data:`OA_CASCADE_ORDER` and stops at the first hit.
    ``full-text`` requires an actual OA location; it is never inferred. With no OA location,
    a record that carries an abstract is ``abstract-only`` (that IS the evidence available),
    and a record with neither is ``unavailable``. A source error in the cascade is recorded as
    a miss for that source and never as a full-text hit.
    """
    doi = claim.doi or (best_match.DOI if best_match else "")
    cascade: list[dict[str, Any]] = []
    if doi:
        def cascade_rank(connector: BaseConnector) -> int:
            if connector.name in OA_CASCADE_ORDER:
                return OA_CASCADE_ORDER.index(connector.name)
            return len(OA_CASCADE_ORDER)

        ordered = sorted(
            (c for c in connectors if c.supports("get_oa_pdf")), key=cascade_rank
        )
        for connector in ordered:
            try:
                location = await connector.get_oa_pdf(doi)
            except SourceError:
                location = None
            if location is not None and location.url:
                cascade.append({"source": connector.name, "hit": True, "url": location.url})
                return {
                    "verdict": "full-text",
                    "oa_url": location.url,
                    "content_type": location.content_type or None,
                    "cascade": cascade,
                }
            cascade.append({"source": connector.name, "hit": False, "url": None})

    verdict = "abstract-only" if (best_match is not None and best_match.abstract) else "unavailable"
    return {"verdict": verdict, "oa_url": None, "content_type": None, "cascade": cascade}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _schema_version(value: str) -> str:
    """Coerce a bare version ("1") into the major.minor form the schemas require."""
    return value if "." in value else f"{value}.0"


def build_report(
    entries: Sequence[VerificationEntry],
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    sources: Sequence[str] = (),
    input_kind: str = "bib",
    input_path: str = "",
    input_reference: str = "",
    run_id: str = "",
    generated_at: str = "",
) -> dict[str, Any]:
    """Assemble the axis (a) report (``core/schemas/verification-report.schema.json``).

    ``generated_at`` is caller-supplied and omitted when blank, so two replays of the same
    snapshot set produce byte-identical JSON (D15).
    """
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "protocol_version": IDENTITY_PROTOCOL_VERSION,
        "versions": {"core": __version__, "parser": _schema_version(PARSER_VERSION)},
        "input": {"kind": input_kind},
        "thresholds": thresholds.to_json_dict(),
        "sources_queried": list(sources),
        "entries": [e.to_json_dict() for e in entries],
        "summary": _summary(entries),
    }
    if input_kind == "bib" and input_path:
        payload["input"]["path"] = input_path
    if input_kind == "reference" and input_reference:
        payload["input"]["reference"] = input_reference
    if run_id:
        payload["run_id"] = run_id
    if generated_at:
        payload["generated_at"] = generated_at
    return payload


def _summary(entries: Sequence[VerificationEntry]) -> dict[str, Any]:
    identity = {VERIFIED: 0, MISMATCH: 0, UNRESOLVABLE: 0, INCONCLUSIVE: 0}
    source_outcomes = {CONFIRMED: 0, NEGATIVE: 0, SOURCE_ERROR: 0}
    status = {"current": 0, "corrected": 0, "retracted": 0, "expression-of-concern": 0}
    accessibility = {"full-text": 0, "abstract-only": 0, "unavailable": 0}
    for entry in entries:
        identity[entry.verdict] += 1
        for key, count in entry.tally().items():
            source_outcomes[key] += count
        verdict = entry.status.verdict if entry.status is not None else "current"
        status[verdict] += 1
        accessibility[str(entry.accessibility.get("verdict") or "unavailable")] += 1
    return {
        "total": len(entries),
        "identity": identity,
        "source_outcomes": source_outcomes,
        "status": status,
        "accessibility": accessibility,
        "refusal_grade": identity[UNRESOLVABLE] + identity[MISMATCH],
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def verify_claims_async(
    claims: Iterable[ReferenceClaim],
    connectors: Sequence[BaseConnector],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    check_status: bool = True,
    check_accessibility: bool = True,
    **report_kwargs: Any,
) -> dict[str, Any]:
    """Verify every claim against an already-built connector set and build the report."""
    entries = [
        await verify_claim_async(
            claim,
            connectors,
            thresholds,
            check_status=check_status,
            check_accessibility=check_accessibility,
        )
        for claim in claims
    ]
    report_kwargs.setdefault("sources", [c.name for c in connectors])
    return build_report(entries, thresholds=thresholds, **report_kwargs)


def verify_claims(
    claims: Iterable[ReferenceClaim],
    *,
    sources: Sequence[str] = DEFAULT_SOURCES,
    snapshots: SnapshotSession | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    check_status: bool = True,
    check_accessibility: bool = True,
    **report_kwargs: Any,
) -> dict[str, Any]:
    """Synchronous entry point: build the connectors, verify, close them, return the report."""

    async def run() -> dict[str, Any]:
        connectors = [create_connector(name, snapshots=snapshots) for name in sources]
        try:
            return await verify_claims_async(
                claims,
                connectors,
                thresholds,
                check_status=check_status,
                check_accessibility=check_accessibility,
                **report_kwargs,
            )
        finally:
            for connector in connectors:
                await connector.aclose()

    return asyncio.run(run())
