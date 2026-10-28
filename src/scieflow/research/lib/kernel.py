"""Record adapter between the search modules' paper records and the vendored CSL model (M-C1 5.2).

`to_csl` maps one source's paper record to a CSLRecord whose id is `source:rank`, so the vendored
dedupe and rank see every identifier and the per-source rank (`extra.source_ranks`).
`from_csl` maps a merged, ranked record back to a paper record: the core fields come from the merged
record, the namespaced signals are the union of the originals' (the first source in canonical order
wins a namespace), and `signals.kernel` names the sources, their identifiers, the score and the rank.
"""

from __future__ import annotations

from scieflow._vendor.researcher_core.model import CSLDate, CSLRecord
from scieflow.research.lib import papers


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def to_csl(record: dict, source: str, rank: int) -> CSLRecord:
    """The CSL record of one paper record, id `source:rank` (rank is the 0-based position in
    that source's answer)."""
    url = record.get("url") or ""
    doi = record.get("doi") or ""
    year = _int_or_none(record.get("year"))
    return CSLRecord(
        id=f"{source}:{rank}",
        DOI=doi,
        title=record.get("title") or "",
        issued=CSLDate(year=year) if year is not None else None,
        container_title=record.get("venue") or "",
        abstract=record.get("abstract") or "",
        URL=url,
        source=source,
        source_id=url or doi,
        citation_count=_int_or_none(record.get("cited_by")),
        extra={"source_ranks": {source: rank}},
    )


def from_csl(merged: CSLRecord, originals: list, *, rank: int) -> dict:
    """The paper record of a merged, ranked CSL record. `originals` are the paper records merged
    into it, in canonical source order; `rank` is the record's 0-based position in the output."""
    signals: dict = {}
    for original in originals:
        for namespace, values in (original.get("signals") or {}).items():
            signals.setdefault(namespace, values)
    sources = list(merged.extra.get("sources") or ([merged.source] if merged.source else []))
    signals["kernel"] = {
        "sources": sources,
        "ids": dict(merged.extra.get("source_ids") or {}),
        "score": merged.extra.get("score"),
        "rank": rank,
    }
    return papers.record(
        signals=signals,
        doi=merged.DOI or None,
        title=merged.title or None,
        year=merged.year,
        venue=merged.container_title or None,
        cited_by=merged.citation_count,
        abstract=merged.abstract or None,
        url=merged.URL or None,
        source=merged.source or None,
    )
