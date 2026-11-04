"""Dual independent screening, blinded adjudication, kappa, and a ranked queue (M4.4, M4.5).

Screening is two streams keyed by ``screener_id``. Each stream is a sequence of
``screening_decision`` events (D19), one per record per stage, carrying the record id, the
stage (``title-abstract`` or ``full-text``), the decision (``include`` / ``exclude``), and
a reason drawn from the locked eligibility profile. Nothing here reads the clock: every
``ts`` is caller-supplied, so a replay of the same events reproduces the same ledger byte
for byte (D15).

Four properties are load-bearing and are enforced here rather than left to callers:

1. **Blinded adjudication.** :func:`screen_conflicts` surfaces the records the two streams
   disagree on, and the object it hands the adjudicator carries the record and the
   eligibility profile ONLY. It never carries either screener's verdict or reason. A
   :class:`Conflict` has no field in which a vote could hide, and
   :meth:`Conflict.adjudication_prompt` returns exactly ``{record_id, stage, record,
   eligibility_profile}``. The link back to the two original decisions travels as a pair of
   opaque content-addressed event ids, which reveal nothing about which way either screener
   voted.

2. **Adjudication is recorded, not inferred.** :func:`record_adjudication` emits an
   ``adjudication`` event carrying the resolved verdict, the rationale, and BOTH original
   ``screening_decision`` event ids, so the ledger proves which two decisions a resolution
   reconciled.

3. **Cohen's kappa is derived, never stored** (D10). :func:`cohens_kappa` aggregates the
   two streams from the ledger and computes ``(po - pe) / (1 - pe)`` over the records both
   screeners decided. Single-screener runs are reported as such (:attr:`KappaResult.
   single_screener`) rather than hidden, so the report can disclose the limitation.

4. **Prioritization changes ORDER only.** :func:`rank_queue` reorders the remaining queue by
   lexical similarity to the already-included records. Every record is still returned, no
   record is dropped, and no record is auto-excluded; disabling the flag restores insertion
   order exactly.

Decisions are strings, never booleans (D9): ``include`` and ``exclude`` are the two the
eligibility workflow uses, but the kappa and conflict machinery treats the category set as
open, so a stream that records a third category is handled rather than silently coerced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._fuzz import fuzz

from .model import CSLRecord, normalize_title
from ._ledger import ProvenanceEvent, ProvenanceLedger, RunContext

__all__ = [
    "SCREENING_STAGES",
    "INCLUDE",
    "EXCLUDE",
    "Conflict",
    "KappaResult",
    "ScreenError",
    "ScreeningDecision",
    "StreamsSummary",
    "cohens_kappa",
    "queue_similarity",
    "rank_queue",
    "record_adjudication",
    "record_screening_decision",
    "screen_conflicts",
    "screening_decisions",
    "screening_streams",
]

#: The two screening stages PRISMA 2020 distinguishes.
SCREENING_STAGES: tuple[str, ...] = ("title-abstract", "full-text")

#: The two decisions the eligibility workflow uses. The category set stays open elsewhere.
INCLUDE = "include"
EXCLUDE = "exclude"

#: Places the similarity score is rounded to before the queue is sorted. Rounding keeps ties
#: honest so the insertion-order tiebreak actually fires (mirrors ``rank.SCORE_PRECISION``).
_SCORE_PRECISION = 6


class ScreenError(RuntimeError):
    """A screening, adjudication, or kappa operation was rejected."""


def _normalize_decision(value: Any) -> str:
    """Coerce a decision label to its canonical lowercase form.

    ``included`` / ``excluded`` collapse to ``include`` / ``exclude`` so the two spellings
    the ledger accepts compare equal; any other non-empty label is lowercased and kept, so
    a third category is handled rather than coerced into a boolean (D9).
    """
    text = str(value or "").strip().lower()
    if text in {"include", "included"}:
        return INCLUDE
    if text in {"exclude", "excluded"}:
        return EXCLUDE
    return text


def _normalize_stage(value: Any) -> str:
    return str(value or "").strip().lower()


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreeningDecision:
    """One ``screening_decision`` event, parsed into the fields screening reasons about."""

    screener_id: str
    record_id: str
    stage: str
    decision: str
    reason: str
    event_id: str
    ts: str

    @classmethod
    def from_event(cls, event: ProvenanceEvent) -> ScreeningDecision:
        if event.type != "screening_decision":
            raise ScreenError(
                f"Event {event.event_id!r} is a {event.type!r} event, not a screening_decision."
            )
        payload = event.payload
        return cls(
            screener_id=str(payload.get("screener_id") or ""),
            record_id=str(payload.get("record_id") or ""),
            stage=_normalize_stage(payload.get("stage")),
            decision=_normalize_decision(payload.get("decision")),
            reason=str(payload.get("reason") or ""),
            event_id=event.event_id,
            ts=event.ts,
        )


def record_screening_decision(
    ledger: ProvenanceLedger,
    run: RunContext,
    *,
    screener_id: str,
    record_id: str,
    stage: str,
    decision: str,
    ts: str,
    reason: str = "",
    source_response_hashes: Iterable[str] = (),
    event_id: str = "",
) -> ProvenanceEvent:
    """Append one ``screening_decision`` event and return it as stored.

    ``ts`` is caller-supplied (D19); this function never reads the clock. The payload keys
    (``record_id``, ``decision``) match what :func:`researcher_core.provenance.derive_prisma`
    aggregates, so the decision counts toward the derived PRISMA flow without any second
    write of the same fact.
    """
    screener = str(screener_id).strip()
    if not screener:
        raise ScreenError("screener_id is required: it is what keeps the two streams apart.")
    record = str(record_id).strip()
    if not record:
        raise ScreenError("record_id is required: a screening decision is about one record.")
    stage_norm = _normalize_stage(stage)
    if stage_norm not in SCREENING_STAGES:
        raise ScreenError(
            f"Unknown screening stage {stage!r}. The stages are {', '.join(SCREENING_STAGES)}."
        )
    payload: dict[str, Any] = {
        "screener_id": screener,
        "record_id": record,
        "stage": stage_norm,
        "decision": _normalize_decision(decision),
    }
    if reason:
        payload["reason"] = str(reason)
    event = run.event(
        "screening_decision",
        payload,
        ts,
        source_response_hashes=source_response_hashes,
        event_id=event_id,
    )
    return ledger.append(event)


def screening_decisions(
    ledger: ProvenanceLedger, run_id: str, *, stage: str | None = None
) -> list[ScreeningDecision]:
    """Every screening decision for a run, in append order, optionally filtered by stage."""
    stage_norm = _normalize_stage(stage) if stage is not None else None
    out: list[ScreeningDecision] = []
    for event in ledger.iter_events(run_id=run_id, type="screening_decision"):
        decision = ScreeningDecision.from_event(event)
        if stage_norm is not None and decision.stage != stage_norm:
            continue
        out.append(decision)
    return out


def _latest_by_screener(
    decisions: Sequence[ScreeningDecision],
) -> dict[str, dict[str, ScreeningDecision]]:
    """Map ``record_id -> {screener_id -> latest decision}``.

    A screener who revises a record emits a new event; the ledger returns events in append
    order, so a later decision overwrites an earlier one here. That is the final position of
    each stream on each record, which is what conflicts and kappa are computed over.
    """
    by_record: dict[str, dict[str, ScreeningDecision]] = {}
    for decision in decisions:
        if not decision.record_id or not decision.screener_id:
            continue
        by_record.setdefault(decision.record_id, {})[decision.screener_id] = decision
    return by_record


# ---------------------------------------------------------------------------
# Streams summary (single-screener disclosure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamsSummary:
    """What screening streams exist for a stage, so the report can disclose the mode."""

    stage: str
    screener_ids: tuple[str, ...]
    per_screener_counts: dict[str, dict[str, int]]
    records_screened: int
    records_decided_by_all: int

    @property
    def single_screener(self) -> bool:
        """True when at most one stream exists. A stated limitation, never a hidden default."""
        return len(self.screener_ids) <= 1

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "screener_ids": list(self.screener_ids),
            "single_screener": self.single_screener,
            "per_screener_counts": {
                screener: dict(sorted(counts.items()))
                for screener, counts in sorted(self.per_screener_counts.items())
            },
            "records_screened": self.records_screened,
            "records_decided_by_all": self.records_decided_by_all,
        }


def screening_streams(ledger: ProvenanceLedger, run_id: str, stage: str) -> StreamsSummary:
    """Summarize the screening streams for one stage.

    ``single_screener`` is surfaced explicitly so the systematic-review report can state
    that only one stream exists rather than let it pass as if dual screening had happened.
    """
    stage_norm = _normalize_stage(stage)
    decisions = screening_decisions(ledger, run_id, stage=stage_norm)
    by_record = _latest_by_screener(decisions)

    screeners: set[str] = set()
    per_screener: dict[str, dict[str, int]] = {}
    decided_by_all = 0
    all_screeners_seen: set[str] = {d.screener_id for d in decisions if d.screener_id}

    for streams in by_record.values():
        screeners.update(streams)
        if all_screeners_seen and set(streams) == all_screeners_seen:
            decided_by_all += 1
        for screener, decision in streams.items():
            counts = per_screener.setdefault(screener, {})
            counts[decision.decision] = counts.get(decision.decision, 0) + 1

    return StreamsSummary(
        stage=stage_norm,
        screener_ids=tuple(sorted(screeners)),
        per_screener_counts=per_screener,
        records_screened=len(by_record),
        records_decided_by_all=decided_by_all,
    )


# ---------------------------------------------------------------------------
# Blinded conflicts
# ---------------------------------------------------------------------------


def _record_to_prompt(record: Any) -> Any:
    """Render a corpus entry into the blinded prompt form.

    A :class:`CSLRecord` becomes its CSL-JSON dict; a mapping or string passes through. No
    screening verdict is ever attached here, by construction.
    """
    if record is None:
        return None
    if isinstance(record, CSLRecord):
        return record.to_csl_json()
    if isinstance(record, Mapping):
        return dict(record)
    return record


@dataclass(frozen=True)
class Conflict:
    """A record the two screening streams disagree on, prepared for BLIND adjudication.

    There is deliberately no field on this object that holds either screener's verdict or
    reason. The adjudicator sees the record and the eligibility profile; the link back to the
    two decisions is a pair of opaque content-addressed event ids (:attr:`decision_event_ids`),
    which do not encode which way either screener voted. This is the blinding the acceptance
    test inspects.
    """

    record_id: str
    stage: str
    decision_event_ids: tuple[str, ...]
    record: Any = None
    eligibility_profile: Mapping[str, Any] | None = None

    def adjudication_prompt(self) -> dict[str, Any]:
        """Exactly what the adjudicator may see: the record and the profile, no votes."""
        return {
            "record_id": self.record_id,
            "stage": self.stage,
            "record": _record_to_prompt(self.record),
            "eligibility_profile": (
                dict(self.eligibility_profile)
                if self.eligibility_profile is not None
                else None
            ),
        }

    def to_json_dict(self) -> dict[str, Any]:
        """Serialized conflict. Carries the linkage ids, still no votes."""
        prompt = self.adjudication_prompt()
        prompt["decision_event_ids"] = list(self.decision_event_ids)
        return prompt


def screen_conflicts(
    ledger: ProvenanceLedger,
    run_id: str,
    stage: str,
    *,
    corpus: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> list[Conflict]:
    """Surface records the streams disagree on, blinded for adjudication.

    A record is a conflict when at least two screeners decided it and their final decisions
    are not unanimous. ``corpus`` supplies the record content the adjudicator reviews
    (keyed by record id); a record absent from the corpus yields ``record=None`` rather than
    being dropped. Conflicts are returned ordered by record id, for determinism.
    """
    stage_norm = _normalize_stage(stage)
    decisions = screening_decisions(ledger, run_id, stage=stage_norm)
    by_record = _latest_by_screener(decisions)

    conflicts: list[Conflict] = []
    for record_id in sorted(by_record):
        streams = by_record[record_id]
        if len(streams) < 2:
            continue
        verdicts = {decision.decision for decision in streams.values()}
        if len(verdicts) < 2:
            continue
        event_ids = tuple(
            sorted(decision.event_id for decision in streams.values())
        )
        conflicts.append(
            Conflict(
                record_id=record_id,
                stage=stage_norm,
                decision_event_ids=event_ids,
                record=corpus.get(record_id) if corpus is not None else None,
                eligibility_profile=profile,
            )
        )
    return conflicts


def record_adjudication(
    ledger: ProvenanceLedger,
    run: RunContext,
    *,
    decision: str,
    rationale: str,
    ts: str,
    conflict: Conflict | None = None,
    record_id: str | None = None,
    stage: str | None = None,
    decision_event_ids: Sequence[str] | None = None,
    source_response_hashes: Iterable[str] = (),
    event_id: str = "",
) -> ProvenanceEvent:
    """Emit an ``adjudication`` event resolving one conflict.

    Pass either the :class:`Conflict` (whose record id, stage, and both original decision
    event ids are read from it) or those three explicitly. The payload carries the resolved
    decision, the rationale, and BOTH original ``screening_decision`` event ids under
    ``resolves``, so the ledger proves which two decisions the resolution reconciled.
    """
    if conflict is not None:
        record = conflict.record_id
        stage_norm = conflict.stage
        resolves = tuple(conflict.decision_event_ids)
    else:
        record = str(record_id or "").strip()
        stage_norm = _normalize_stage(stage)
        resolves = tuple(str(e) for e in (decision_event_ids or ()))

    if not record:
        raise ScreenError("record_id is required to record an adjudication.")
    if stage_norm not in SCREENING_STAGES:
        raise ScreenError(
            f"Unknown screening stage {stage_norm!r}. The stages are "
            f"{', '.join(SCREENING_STAGES)}."
        )
    if len(resolves) < 2:
        raise ScreenError(
            "An adjudication reconciles two screening decisions; both original event ids "
            "are required under decision_event_ids (a Conflict supplies them)."
        )
    rationale_text = str(rationale or "").strip()
    if not rationale_text:
        raise ScreenError(
            "rationale is required: an adjudication must state why it resolved as it did."
        )

    payload: dict[str, Any] = {
        "record_id": record,
        "stage": stage_norm,
        "decision": _normalize_decision(decision),
        "rationale": rationale_text,
        "resolves": list(resolves),
    }
    event = run.event(
        "adjudication",
        payload,
        ts,
        source_response_hashes=source_response_hashes,
        event_id=event_id,
    )
    return ledger.append(event)


# ---------------------------------------------------------------------------
# Cohen's kappa, derived
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KappaResult:
    """Cohen's kappa between two screening streams, DERIVED from the ledger (D10)."""

    stage: str
    screener_ids: tuple[str, ...]
    n: int
    categories: tuple[str, ...]
    table: dict[str, dict[str, int]]
    observed_agreement: float
    expected_agreement: float
    kappa: float | None
    single_screener: bool
    note: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "screener_ids": list(self.screener_ids),
            "n": self.n,
            "categories": list(self.categories),
            "table": {
                a: dict(sorted(row.items())) for a, row in sorted(self.table.items())
            },
            "observed_agreement": self.observed_agreement,
            "expected_agreement": self.expected_agreement,
            "kappa": self.kappa,
            "single_screener": self.single_screener,
            "note": self.note,
        }


def cohens_kappa(ledger: ProvenanceLedger, run_id: str, stage: str) -> KappaResult:
    """Derive Cohen's kappa between the two streams for one stage.

    The formula is the real one, ``kappa = (po - pe) / (1 - pe)``, over the records BOTH
    screeners decided. ``po`` is the observed agreement rate; ``pe`` is the agreement
    expected from each screener's marginal rates. A single-screener run reports
    ``single_screener=True`` and ``kappa=None`` rather than a fabricated number; more than
    two streams is out of scope for Cohen's kappa (Fleiss' kappa would be needed) and raises.
    """
    stage_norm = _normalize_stage(stage)
    decisions = screening_decisions(ledger, run_id, stage=stage_norm)
    by_record = _latest_by_screener(decisions)

    screeners = sorted({d.screener_id for d in decisions if d.screener_id})

    if len(screeners) < 2:
        return KappaResult(
            stage=stage_norm,
            screener_ids=tuple(screeners),
            n=0,
            categories=(),
            table={},
            observed_agreement=0.0,
            expected_agreement=0.0,
            kappa=None,
            single_screener=True,
            note="Only one screening stream exists; Cohen's kappa is undefined for one rater.",
        )
    if len(screeners) > 2:
        raise ScreenError(
            f"Cohen's kappa is defined for two raters; this stage has {len(screeners)} "
            f"screeners ({', '.join(screeners)}). Fleiss' kappa would be needed and is "
            "out of scope."
        )

    a_id, b_id = screeners[0], screeners[1]
    table: dict[str, dict[str, int]] = {}
    categories: set[str] = set()
    n = 0
    for streams in by_record.values():
        if a_id not in streams or b_id not in streams:
            continue
        a_cat = streams[a_id].decision
        b_cat = streams[b_id].decision
        categories.update((a_cat, b_cat))
        table.setdefault(a_cat, {})
        table[a_cat][b_cat] = table[a_cat].get(b_cat, 0) + 1
        n += 1

    if n == 0:
        return KappaResult(
            stage=stage_norm,
            screener_ids=(a_id, b_id),
            n=0,
            categories=(),
            table={},
            observed_agreement=0.0,
            expected_agreement=0.0,
            kappa=None,
            single_screener=False,
            note="No record was decided by both screeners; kappa is undefined.",
        )

    ordered_categories = tuple(sorted(categories))
    agreements = sum(table.get(cat, {}).get(cat, 0) for cat in ordered_categories)
    observed = agreements / n

    marginal_a = {
        cat: sum(table.get(cat, {}).values()) for cat in ordered_categories
    }
    marginal_b = {
        cat: sum(row.get(cat, 0) for row in table.values()) for cat in ordered_categories
    }
    expected = sum(
        (marginal_a[cat] / n) * (marginal_b[cat] / n) for cat in ordered_categories
    )

    if abs(1.0 - expected) < 1e-12:
        return KappaResult(
            stage=stage_norm,
            screener_ids=(a_id, b_id),
            n=n,
            categories=ordered_categories,
            table=table,
            observed_agreement=observed,
            expected_agreement=expected,
            kappa=None,
            single_screener=False,
            note=(
                "Expected agreement is 1 (both streams used a single category), so kappa is "
                "undefined; observed agreement is reported instead."
            ),
        )

    kappa = (observed - expected) / (1.0 - expected)
    return KappaResult(
        stage=stage_norm,
        screener_ids=(a_id, b_id),
        n=n,
        categories=ordered_categories,
        table=table,
        observed_agreement=observed,
        expected_agreement=expected,
        kappa=kappa,
        single_screener=False,
    )


# ---------------------------------------------------------------------------
# Ranked screening queue (M4.5): order only, no exclusion
# ---------------------------------------------------------------------------


def _corpus_text(entry: Any) -> str:
    """Extract comparable text (title plus abstract) from a corpus entry.

    Accepts a :class:`CSLRecord`, a mapping with ``title`` / ``abstract`` keys, or a raw
    string. Returns lowercased, whitespace-collapsed text; an unknown entry yields "".
    """
    if entry is None:
        return ""
    if isinstance(entry, CSLRecord):
        text = f"{entry.title} {entry.abstract}"
    elif isinstance(entry, Mapping):
        parts = [str(entry.get(key) or "") for key in ("title", "abstract")]
        text = " ".join(p for p in parts if p) or str(entry.get("text") or "")
    else:
        text = str(entry)
    return normalize_title(text).lower()


def queue_similarity(
    record_id: str,
    included_ids: Sequence[str],
    corpus: Mapping[str, Any],
) -> float:
    """Lexical similarity of one record to the nearest already-included record, in ``[0, 1]``.

    ``rapidfuzz.fuzz.token_sort_ratio`` over the title-plus-abstract text (the same lexical
    signal ``dedupe.title_similarity`` uses), taking the MAXIMUM over the included set: a
    record close to any one included paper is worth surfacing. Records or included ids
    missing from the corpus contribute no similarity rather than an invented one.
    """
    target = _corpus_text(corpus.get(record_id))
    if not target:
        return 0.0
    best = 0.0
    for included_id in included_ids:
        reference = _corpus_text(corpus.get(included_id))
        if not reference:
            continue
        score = float(fuzz.token_sort_ratio(target, reference)) / 100.0
        if score > best:
            best = score
    return best


def rank_queue(
    remaining_ids: Sequence[str],
    included_ids: Sequence[str],
    corpus: Mapping[str, Any],
    *,
    enabled: bool = True,
) -> list[str]:
    """Reorder the remaining screening queue by similarity to the included records.

    ORDER only. Every id in ``remaining_ids`` is returned exactly once, no id is dropped, and
    nothing is auto-excluded: this is prioritization, not filtering. Ranking is stable, so
    equal-similarity records keep their insertion order, and ``enabled=False`` returns the
    queue untouched (the same list, in the same order). Deterministic: the score depends only
    on the corpus text, never on the wall clock (D15).
    """
    remaining = list(remaining_ids)
    if not enabled or not included_ids or len(remaining) < 2:
        return remaining

    scored: list[tuple[float, int, str]] = []
    for index, record_id in enumerate(remaining):
        score = round(queue_similarity(record_id, included_ids, corpus), _SCORE_PRECISION)
        scored.append((score, index, record_id))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [record_id for _, _, record_id in scored]
