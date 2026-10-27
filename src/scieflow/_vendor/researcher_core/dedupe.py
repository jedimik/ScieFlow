"""Record deduplication: DOI exact match first, then normalized-title similarity.

Fanning out one query across eight indexes returns the same paper several times, in
several shapes. Collapsing those copies is not cosmetic: :mod:`researcher_core.verify`
counts CONFIRMING SOURCES to reach a D9 identity verdict, so a merge that forgets which
sources contributed silently weakens the verdict (three confirmations look like one, and
``verified`` degrades to ``inconclusive``). Every merge here therefore carries two things
forward, always:

1. **The union of the identifiers.** DOI, OpenAlex ID, arXiv ID, PMID, PMCID, and S2 ID
   are unioned across the merged records. Whichever copy carried an identifier, the
   survivor has it.
2. **The source attribution.** ``custom.sources`` lists every connector that contributed,
   in merge order, and ``custom.source_ids`` maps each of those connectors to its native
   identifier for the record.

Two passes, in this order:

* **Pass 1, DOI exact.** DOIs are normalized by :func:`researcher_core.model.normalize_doi`
  (resolver prefix stripped, lowercased), then compared for equality. This is the only
  comparison that is certain, so it runs first and nothing overrides it.
* **Pass 2, title similarity.** Remaining groups are compared on their title fingerprints
  (:func:`researcher_core.model.title_fingerprint`: NFC, casefolded, punctuation stripped)
  with ``rapidfuzz.fuzz.token_sort_ratio``, merging at :data:`TITLE_SIMILARITY_THRESHOLD`
  (0.90) or better. Two groups whose DOI sets are both non-empty and disjoint NEVER merge
  on title alone: distinct DOIs are the strongest evidence available that these are two
  different records (a preprint and its version of record, an erratum and its article), and
  a fuzzy title match must not overturn it.

Every merge emits a :class:`DedupDecision`. This module only RETURNS the decisions;
``provenance.py`` is what logs them as ``dedup_decision`` events. Deduplication is pure and
does no I/O, which is also what makes it trivially replayable under D15.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ._fuzz import fuzz

from .model import CSLRecord, title_fingerprint

__all__ = [
    "DedupDecision",
    "DedupeResult",
    "IDENTIFIER_FIELDS",
    "MIN_TITLE_FINGERPRINT_CHARS",
    "REASON_DOI_EXACT",
    "REASON_TITLE_SIMILARITY",
    "TITLE_SIMILARITY_THRESHOLD",
    "dedupe",
    "identifier_key",
    "identity_key",
    "merge_records",
    "title_similarity",
]

#: rapidfuzz ``token_sort_ratio`` at or above this (expressed 0..1, as the plan states it)
#: merges two records on title. 0.90 tolerates the differences that are pure formatting
#: (subtitle punctuation, a trailing period, "and" versus "&") while still separating two
#: genuinely different papers on the same topic.
TITLE_SIMILARITY_THRESHOLD = 0.90

#: A title fingerprint shorter than this never merges on similarity alone. Generic short
#: titles ("Editorial", "Introduction", "Errata") are identical across thousands of
#: unrelated records, and a 100% ratio between two of them means nothing.
MIN_TITLE_FINGERPRINT_CHARS = 12

#: The identifier fields unioned on merge. Losing one of these loses a source attribution
#: downstream, so the list lives in exactly one place.
IDENTIFIER_FIELDS: tuple[str, ...] = (
    "DOI",
    "openalex_id",
    "arxiv_id",
    "pmid",
    "pmcid",
    "s2_id",
)

#: Extension keys whose values are dicts and are UNIONED on merge rather than overwritten.
#: ``source_ranks`` feeds the rank.py relevance term, ``source_ids`` feeds verify.py.
_MERGED_DICT_KEYS: tuple[str, ...] = ("source_ranks", "source_ids")

#: Reasons a merge can happen. Recorded on every decision so provenance can tell an exact
#: identifier match from a fuzzy one.
REASON_DOI_EXACT = "doi_exact"
REASON_TITLE_SIMILARITY = "title_similarity"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def identity_key(record: CSLRecord) -> str:
    """The strongest stable key this record carries, as a namespaced string.

    Preference order mirrors how much a key can be trusted: DOI, then OpenAlex ID, arXiv
    ID, PMID, S2 ID, then the title fingerprint, then the record's own id. Used as the node
    key by ``graph.py`` and as the merge-group key here.
    """
    if record.DOI:
        return f"doi:{record.DOI}"
    if record.openalex_id:
        return f"openalex:{record.openalex_id.lower()}"
    if record.arxiv_id:
        return f"arxiv:{record.arxiv_id.lower()}"
    if record.pmid:
        return f"pmid:{record.pmid}"
    if record.s2_id:
        return f"s2:{record.s2_id.lower()}"
    if record.title_key:
        return f"title:{record.title_key}"
    return f"id:{record.id}"


def identifier_key(identifier: str) -> str:
    """The :func:`identity_key` an ID STRING would produce, without a record in hand.

    ``graph.py`` needs this to compare a seed identifier (a DOI on the command line) with
    the records the traversal discovers.
    """
    from .model import is_valid_doi, normalize_doi  # local: keeps the module import-light

    text = str(identifier or "").strip()
    if not text:
        return ""
    doi = normalize_doi(text)
    if is_valid_doi(doi):
        return f"doi:{doi}"
    bare = text.split("/")[-1] if "openalex.org" in text.lower() else text
    lowered = bare.lower()
    if lowered.startswith("w") and lowered[1:].isdigit():
        return f"openalex:{lowered}"
    if lowered.startswith("arxiv:"):
        return f"arxiv:{lowered[len('arxiv:'):]}"
    return f"id:{lowered}"


def title_similarity(left: CSLRecord | str, right: CSLRecord | str) -> float:
    """Normalized-title similarity in ``[0, 1]`` via rapidfuzz ``token_sort_ratio``.

    Token-sort, not plain ratio, because sources reorder title fragments ("Deep learning: a
    review" versus "A review of deep learning") far more often than they alter the words.
    """
    a = left.title_key if isinstance(left, CSLRecord) else title_fingerprint(left)
    b = right.title_key if isinstance(right, CSLRecord) else title_fingerprint(right)
    if not a or not b:
        return 0.0
    return float(fuzz.token_sort_ratio(a, b)) / 100.0


# ---------------------------------------------------------------------------
# Decisions and results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DedupDecision:
    """One merge, recorded so ``provenance.py`` can log it as a ``dedup_decision`` event."""

    kept_id: str
    kept_key: str
    duplicate_id: str
    duplicate_key: str
    reason: str
    similarity: float
    kept_sources: tuple[str, ...] = ()
    duplicate_sources: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "kept_id": self.kept_id,
            "kept_key": self.kept_key,
            "duplicate_id": self.duplicate_id,
            "duplicate_key": self.duplicate_key,
            "reason": self.reason,
            "similarity": round(self.similarity, 4),
            "kept_sources": list(self.kept_sources),
            "duplicate_sources": list(self.duplicate_sources),
        }


@dataclass
class DedupeResult:
    """The merged records, the decisions that produced them, and the key remapping."""

    records: list[CSLRecord] = field(default_factory=list)
    decisions: list[DedupDecision] = field(default_factory=list)
    #: Every input identity key mapped to the identity key of the record that survived it.
    #: A key that survived maps to itself, so this is total over the input.
    key_map: dict[str, str] = field(default_factory=dict)

    @property
    def duplicates_removed(self) -> int:
        return len(self.decisions)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_csl_json() for r in self.records],
            "decisions": [d.to_json_dict() for d in self.decisions],
            "duplicates_removed": self.duplicates_removed,
        }


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_records(primary: CSLRecord, other: CSLRecord) -> CSLRecord:
    """Merge ``other`` into ``primary``, keeping the union of what they know.

    ``primary`` wins every populated bibliographic field, because it came first in the
    fan-out order and reordering a field's provenance for no reason is how records drift.
    ``other`` fills the blanks. The parts that are NOT "primary wins":

    * identifiers are unioned (:data:`IDENTIFIER_FIELDS`);
    * ``custom.sources`` and ``custom.source_ids`` accumulate every contributing connector;
    * ``citation_count`` takes the maximum (indexes lag each other; the larger count is the
      more current one, and taking the max never invents a citation);
    * ``is_retracted`` is True when EITHER source says True. A retraction one index has not
      caught up with is still a retraction, and axis (b) must never lose it.
    """
    merged = CSLRecord.from_csl_json(primary.to_csl_json())
    merged.extra = dict(primary.extra)

    for name in IDENTIFIER_FIELDS:
        if not getattr(merged, name):
            value = getattr(other, name)
            if value:
                setattr(merged, name, value)

    for name in (
        "title",
        "container_title",
        "publisher",
        "volume",
        "issue",
        "page",
        "number",
        "version",
        "abstract",
        "URL",
        "ISBN",
        "language",
        "note",
        "oa_url",
    ):
        if not getattr(merged, name):
            value = getattr(other, name)
            if value:
                setattr(merged, name, value)

    if not merged.author:
        merged.author = list(other.author)
    if not merged.editor:
        merged.editor = list(other.editor)
    if merged.issued is None or merged.issued.year is None:
        if other.issued is not None and other.issued.year is not None:
            merged.issued = other.issued
    if not merged.ISSN:
        merged.ISSN = list(other.ISSN)
    if not merged.keyword:
        merged.keyword = list(other.keyword)

    merged.citation_count = _max_optional(primary.citation_count, other.citation_count)
    merged.reference_count = _max_optional(primary.reference_count, other.reference_count)
    merged.is_retracted = _or_optional(primary.is_retracted, other.is_retracted)
    merged.is_oa = _or_optional(primary.is_oa, other.is_oa)

    # Source attribution. Both halves may already carry accumulated lists (a merge of a
    # merge), so start from whatever each side knows and union, preserving merge order.
    primary_sources = _source_list(primary)
    sources = primary_sources + [s for s in _source_list(other) if s not in primary_sources]
    source_ids = dict(_source_id_map(primary))
    for name, native in _source_id_map(other).items():
        source_ids.setdefault(name, native)

    extra = dict(merged.extra)
    for key in _MERGED_DICT_KEYS:
        left = primary.extra.get(key)
        right = other.extra.get(key)
        if isinstance(left, dict) or isinstance(right, dict):
            combined: dict[str, Any] = {}
            if isinstance(right, dict):
                combined.update(right)
            if isinstance(left, dict):
                combined.update(left)  # primary's values win on a key clash
            extra[key] = combined
    for key, value in other.extra.items():
        if key not in extra and key not in _MERGED_DICT_KEYS:
            extra[key] = value

    if sources:
        extra["sources"] = sources
    if source_ids:
        extra["source_ids"] = source_ids
    merged.extra = extra
    # The merged record keeps the primary's id, so a decision that names it stays valid.
    merged.id = primary.id
    merged.source = primary.source or other.source
    merged.source_id = primary.source_id or other.source_id
    return merged


def _attributed(record: CSLRecord) -> CSLRecord:
    """A copy of ``record`` carrying ``custom.sources`` / ``custom.source_ids``.

    Applied to every record that survives dedup, merged or not, so consumers never have to
    special-case "this one came from a single source".
    """
    out = CSLRecord.from_csl_json(record.to_csl_json())
    out.extra = dict(record.extra)
    out.id = record.id
    sources = _source_list(record)
    source_ids = _source_id_map(record)
    if sources:
        out.extra["sources"] = sources
    if source_ids:
        out.extra["source_ids"] = source_ids
    return out


def _source_list(record: CSLRecord) -> list[str]:
    existing = record.extra.get("sources")
    if isinstance(existing, list) and existing:
        return [str(s) for s in existing]
    return [record.source] if record.source else []


def _source_id_map(record: CSLRecord) -> dict[str, str]:
    existing = record.extra.get("source_ids")
    out: dict[str, str] = {}
    if isinstance(existing, dict):
        out.update({str(k): str(v) for k, v in existing.items() if v})
    if record.source and record.source_id and record.source not in out:
        out[record.source] = record.source_id
    return out


def _max_optional(left: int | None, right: int | None) -> int | None:
    values = [v for v in (left, right) if v is not None]
    return max(values) if values else None


def _or_optional(left: bool | None, right: bool | None) -> bool | None:
    if left is True or right is True:
        return True
    if left is False or right is False:
        return False
    return None


# ---------------------------------------------------------------------------
# The two passes
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    """A set of records believed to be the same work, in first-seen order."""

    members: list[CSLRecord]
    reasons: list[str]
    scores: list[float]

    @property
    def head(self) -> CSLRecord:
        return self.members[0]

    @property
    def dois(self) -> set[str]:
        return {r.DOI for r in self.members if r.DOI}

    @property
    def fingerprint(self) -> str:
        for record in self.members:
            if record.title_key:
                return record.title_key
        return ""


def dedupe(records: Iterable[CSLRecord]) -> DedupeResult:
    """Collapse duplicate records. DOI exact first, then title similarity.

    Input order is preserved and is load-bearing: the first record of a group survives, so
    a caller that fans out in a fixed source order gets a byte-identical result every time
    (D15). Nothing here touches the network or the clock.
    """
    items = list(records)
    if not items:
        return DedupeResult()

    groups = _group_by_doi(items)
    groups = _merge_on_title(groups)

    out_records: list[CSLRecord] = []
    decisions: list[DedupDecision] = []
    key_map: dict[str, str] = {}

    for group in groups:
        merged = group.head
        for member in group.members[1:]:
            merged = merge_records(merged, member)
        if len(group.members) == 1:
            merged = _attributed(merged)

        kept_key = identity_key(merged)
        for member in group.members:
            key_map[identity_key(member)] = kept_key
        for index, member in enumerate(group.members[1:], start=1):
            decisions.append(
                DedupDecision(
                    kept_id=merged.id,
                    kept_key=kept_key,
                    duplicate_id=member.id,
                    duplicate_key=identity_key(member),
                    reason=group.reasons[index],
                    similarity=group.scores[index],
                    kept_sources=tuple(_source_list(merged)),
                    duplicate_sources=tuple(_source_list(member)),
                )
            )
        out_records.append(merged)

    return DedupeResult(records=out_records, decisions=decisions, key_map=key_map)


def _group_by_doi(items: Sequence[CSLRecord]) -> list[_Group]:
    """Pass 1. Records sharing a normalized DOI are one group; the rest are singletons."""
    groups: list[_Group] = []
    by_doi: dict[str, _Group] = {}
    for record in items:
        doi = record.DOI
        if doi and doi in by_doi:
            group = by_doi[doi]
            group.members.append(record)
            group.reasons.append(REASON_DOI_EXACT)
            group.scores.append(1.0)
            continue
        group = _Group(members=[record], reasons=[""], scores=[1.0])
        groups.append(group)
        if doi:
            by_doi[doi] = group
    return groups


def _merge_on_title(groups: Sequence[_Group]) -> list[_Group]:
    """Pass 2. Fold each group into an earlier one when the titles match closely enough.

    A group whose DOI set is non-empty and disjoint from the earlier group's DOI set is
    never folded in: two different DOIs mean two different registered works, and no title
    ratio outranks that.
    """
    kept: list[_Group] = []
    for group in groups:
        fingerprint = group.fingerprint
        target: _Group | None = None
        score = 0.0
        if len(fingerprint) >= MIN_TITLE_FINGERPRINT_CHARS:
            for candidate in kept:
                if _dois_conflict(candidate.dois, group.dois):
                    continue
                candidate_fingerprint = candidate.fingerprint
                if len(candidate_fingerprint) < MIN_TITLE_FINGERPRINT_CHARS:
                    continue
                similarity = title_similarity(candidate_fingerprint, fingerprint)
                if similarity >= TITLE_SIMILARITY_THRESHOLD and similarity > score:
                    target = candidate
                    score = similarity
        if target is None:
            kept.append(group)
            continue
        for member in group.members:
            target.members.append(member)
            target.reasons.append(REASON_TITLE_SIMILARITY)
            target.scores.append(score)
    return kept


def _dois_conflict(left: set[str], right: set[str]) -> bool:
    """True when both sides carry DOIs and share none of them."""
    return bool(left) and bool(right) and left.isdisjoint(right)
