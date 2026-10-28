"""Composite ranking over relevance, recency, and citation count.

Three signals, three named weights, one score. The weights are constants rather than
tunables scattered through the code, so that a change to how results are ordered is a
visible, reviewable diff rather than an emergent property of six call sites.

Why these weights (:data:`DEFAULT_WEIGHTS`):

* **Relevance 0.50.** The dominant term, and deliberately so. The indexes have far more
  information about topical match than the kernel does (full text, citation context,
  learned rankers), and the one thing a fan-out adds is CROSS-SOURCE AGREEMENT: a paper
  that three indexes independently rank highly for a query is a better answer than one that
  a single index ranks first. The relevance term is therefore reciprocal-rank fusion (RRF)
  over the per-source ranks, which rewards exactly that agreement.
* **Citations 0.30.** A real quality signal and a real bias: it lags by years, so it buries
  new work, and it varies by orders of magnitude across fields. It is log-scaled (the
  difference between 10 and 100 citations means much more than between 1000 and 1090) and
  normalized within the result set, and it is kept well below relevance so that a highly
  cited off-topic paper cannot outrank an on-topic one.
* **Recency 0.20.** The counterweight to the citation term's lag. Small on its own, because
  a literature search that ranked purely on freshness would bury the field's foundations.

Determinism (D15): the score depends only on the records in hand, never on the wall clock.
The recency reference year defaults to the newest publication year IN THE RESULT SET, not
to today, so replaying a snapshot next year produces byte-identical output. Ties break on
the record id, never on dict or set iteration order.

Scores are comparable only WITHIN one result set (the citation term is normalized by the
set maximum), which is exactly what ``custom.score`` in ``record.schema.json`` promises.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .model import CSLRecord

__all__ = [
    "CITATION_WEIGHT",
    "DEFAULT_WEIGHTS",
    "RECENCY_UNKNOWN_SCORE",
    "RECENCY_WEIGHT",
    "RECENCY_WINDOW_YEARS",
    "RELEVANCE_WEIGHT",
    "RRF_K",
    "RankScore",
    "RankWeights",
    "citation_score",
    "rank_records",
    "recency_score",
    "relevance_score",
    "score_records",
]

#: Weight of the cross-source relevance term. See the module docstring for the reasoning.
RELEVANCE_WEIGHT = 0.50
#: Weight of the log-scaled, set-normalized citation term.
CITATION_WEIGHT = 0.30
#: Weight of the recency ramp.
RECENCY_WEIGHT = 0.20

#: Reciprocal-rank-fusion constant. 60 is the value from Cormack, Clarke, and Buettcher
#: (SIGIR 2009), where it was found to be insensitive across collections. It damps the
#: enormous gap between rank 1 and rank 2 that a raw 1/rank would create, so that agreement
#: across sources can actually outweigh a single source's top hit.
RRF_K = 60

#: Recency ramps linearly over this many years back from the reference year. Ten years is
#: roughly the half-life at which a methods paper stops being "current" in most fields
#: without being irrelevant.
RECENCY_WINDOW_YEARS = 10

#: A record with no publication year scores the neutral midpoint: a missing metadata field
#: is not evidence about the work's age, so it must neither promote nor bury the record.
RECENCY_UNKNOWN_SCORE = 0.5

#: Places the composite score is rounded to. Float arithmetic is deterministic for a fixed
#: input, but rounding before the sort keeps ties honest ties (so the record-id tiebreak
#: actually fires) instead of ordering on the last bit of an IEEE double.
SCORE_PRECISION = 6


@dataclass(frozen=True)
class RankWeights:
    """The three weights. Not required to sum to 1, but the defaults do, for readability."""

    relevance: float = RELEVANCE_WEIGHT
    citations: float = CITATION_WEIGHT
    recency: float = RECENCY_WEIGHT

    def to_json_dict(self) -> dict[str, float]:
        return {
            "relevance": self.relevance,
            "citations": self.citations,
            "recency": self.recency,
        }


DEFAULT_WEIGHTS = RankWeights()


@dataclass(frozen=True)
class RankScore:
    """The composite score of one record, with its three components kept visible."""

    record_id: str
    score: float
    relevance: float
    citations: float
    recency: float

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "score": self.score,
            "components": {
                "relevance": round(self.relevance, SCORE_PRECISION),
                "citations": round(self.citations, SCORE_PRECISION),
                "recency": round(self.recency, SCORE_PRECISION),
            },
        }


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def relevance_score(record: CSLRecord, fallback_rank: int) -> float:
    """Reciprocal-rank fusion over the per-source ranks stashed by ``search.py``.

    ``search.py`` writes ``custom.source_ranks = {source: zero_based_rank}`` as it fans out,
    and ``dedupe.py`` unions those dicts on merge, so a record confirmed at rank 0 by three
    sources arrives here with three entries and scores above a record ranked 0 by one.

    The raw RRF sum is unbounded above (more sources means more terms), so it is expressed
    relative to the best possible single-source score before normalization: a record found
    at rank 0 by one source scores exactly 1.0 on the single-source scale, and every extra
    confirming source adds to that. :func:`score_records` then normalizes the set to [0, 1].

    ``fallback_rank`` is the record's position in the input list, used when no source ranks
    were recorded (a hand-assembled list, or graph output being ranked).
    """
    ranks = record.extra.get("source_ranks")
    positions: list[int] = []
    if isinstance(ranks, Mapping):
        for source in sorted(ranks, key=str):  # sorted: never depend on dict order
            value = ranks[source]
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if value >= 0:
                positions.append(value)
    if not positions:
        positions = [max(0, int(fallback_rank))]
    best_possible = 1.0 / (RRF_K + 1)
    return sum(1.0 / (RRF_K + 1 + p) for p in positions) / best_possible


def citation_score(record: CSLRecord, max_citations: int) -> float:
    """Log-scaled citation count, normalized by the largest count in the result set.

    An unknown citation count scores 0.0 rather than an imagined average: the kernel never
    invents a number it was not given.
    """
    count = record.citation_count
    if count is None or count <= 0 or max_citations <= 0:
        return 0.0
    return math.log1p(min(count, max_citations)) / math.log1p(max_citations)


def recency_score(record: CSLRecord, reference_year: int | None) -> float:
    """Linear ramp over :data:`RECENCY_WINDOW_YEARS` back from ``reference_year``."""
    year = record.year
    if year is None or reference_year is None:
        return RECENCY_UNKNOWN_SCORE
    age = reference_year - year
    if age <= 0:
        return 1.0
    if age >= RECENCY_WINDOW_YEARS:
        return 0.0
    return 1.0 - (age / RECENCY_WINDOW_YEARS)


# ---------------------------------------------------------------------------
# Scoring and ranking
# ---------------------------------------------------------------------------


def score_records(
    records: Sequence[CSLRecord],
    *,
    reference_year: int | None = None,
    weights: RankWeights = DEFAULT_WEIGHTS,
) -> list[RankScore]:
    """Score every record, in input order.

    ``reference_year`` defaults to the newest year present in ``records``. That default is
    what keeps the output replayable: it is a function of the snapshot, not of today's
    date (D15).
    """
    items = list(records)
    if not items:
        return []

    years = [r.year for r in items if r.year is not None]
    if reference_year is None and years:
        reference_year = max(years)

    counts = [r.citation_count for r in items if r.citation_count is not None]
    max_citations = max([c for c in counts if c > 0], default=0)

    raw_relevance = [relevance_score(r, index) for index, r in enumerate(items)]
    max_relevance = max(raw_relevance, default=0.0)

    out: list[RankScore] = []
    for index, record in enumerate(items):
        relevance = raw_relevance[index] / max_relevance if max_relevance > 0 else 0.0
        citations = citation_score(record, max_citations)
        recency = recency_score(record, reference_year)
        composite = (
            weights.relevance * relevance
            + weights.citations * citations
            + weights.recency * recency
        )
        out.append(
            RankScore(
                record_id=record.id,
                score=round(composite, SCORE_PRECISION),
                relevance=relevance,
                citations=citations,
                recency=recency,
            )
        )
    return out


def rank_records(
    records: Iterable[CSLRecord],
    *,
    reference_year: int | None = None,
    weights: RankWeights = DEFAULT_WEIGHTS,
    limit: int | None = None,
) -> list[CSLRecord]:
    """Return the records ordered best first, each carrying ``custom.score``.

    Deterministic: equal scores break on the record id, so the order never depends on the
    order a set or dict happened to iterate in (D15). Records are copied, not mutated in
    place, so ranking the same list twice is idempotent.
    """
    items = list(records)
    scores = score_records(items, reference_year=reference_year, weights=weights)

    ranked: list[tuple[float, str, CSLRecord]] = []
    for record, score in zip(items, scores, strict=True):
        copy = CSLRecord.from_csl_json(record.to_csl_json())
        copy.id = record.id
        copy.extra = dict(record.extra)
        copy.extra["score"] = score.score
        ranked.append((score.score, record.id, copy))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    out = [record for _, _, record in ranked]
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out
