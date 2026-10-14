"""OpenAlex counts by year, trend breaks and co-citation bridges (free).

- `counts_by_year(gw, term)` — `GET /works?search=…&group_by=publication_year`
  → {year: works}. `baseline_counts(gw)` is the same for all of OpenAlex.
- `trend_breaks(counts, until_year=…)` — pure and deterministic. Years from
  the first year with data to `until_year` (pass the last *complete* year;
  missing years count 0). For each year y with a full window on both sides,
  ratio = mean(y … y+w-1) / mean(y-w … y-1); ≥ `ratio` is a surge, ≤ 1/`ratio`
  a drop, provided either mean reaches `min_count` works. A run of
  consecutive flagged years of one direction is reported once, at its most
  extreme year (ties: the earliest). With `baseline` (the whole literature
  per year) the series is the topic's share, so the field's general growth
  is not mistaken for a break.
- `bridges(refs_a, refs_b)` — pure: works referenced by at least `min_each`
  of the top papers of both topics (co-citation bridges), scored by the
  smaller of the two citing counts; ties by total, then id. A paper that is
  top-cited in *both* topics is direct overlap (`shared_top`), so its
  references count on neither side.
- `bridge_gap(gw, a, b)` — the whole path: the top-cited works per topic with
  their reference lists, plus the number of works matching both topics
  (`"a" AND "b"`). Shared foundations with little direct overlap mark a gap
  between two literatures.

Only leak-checked topic terms leave the machine. Fetched content is data.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from scieflow.research.lib import http

API = "https://api.openalex.org/works"
PROVIDER = "openalex"
_ID_PREFIX = "https://openalex.org/"


def _groups(value) -> list:
    groups = value.get("group_by") if isinstance(value, dict) else None
    if not isinstance(groups, list):
        raise http.HTTPFailure("OpenAlex response has no group_by", "MALFORMED")
    return groups


def _years(value) -> dict[int, int]:
    out: dict[int, int] = {}
    for g in _groups(value):
        if not isinstance(g, dict):
            continue
        key, n = str(g.get("key", "")), g.get("count")
        if key.isdigit() and isinstance(n, int) and not isinstance(n, bool) and n >= 0:
            out[int(key)] = n
    return dict(sorted(out.items()))


def _n_groups(value) -> int:
    groups = value.get("group_by") if isinstance(value, dict) else None
    return len(groups) if isinstance(groups, list) else 0


def _n_results(value) -> int:
    rows = value.get("results") if isinstance(value, dict) else None
    return len(rows) if isinstance(rows, list) else 0


def counts_by_year(gw, term: str) -> dict[int, int]:
    return _years(gw.get_json(PROVIDER, "counts_by_year", API, text={"search": term},
                              fixed={"group_by": "publication_year"}, ttl_class="search",
                              count=_n_groups, validate=_groups))


def baseline_counts(gw) -> dict[int, int]:
    return _years(gw.get_json(PROVIDER, "counts_by_year_all", API,
                              fixed={"group_by": "publication_year"}, ttl_class="metadata",
                              count=_n_groups, validate=_groups))


def trend_breaks(counts: Mapping[int, int], *, until_year: int, window: int = 3,
                 ratio: float = 2.0, min_count: int = 5,
                 baseline: Mapping[int, int] | None = None) -> list[dict]:
    if window < 1 or ratio <= 1:
        raise ValueError("window must be >= 1 and ratio > 1")
    years = [y for y in counts if y <= until_year]
    if not years:
        return []
    span = range(min(years), until_year + 1)
    raw = {y: int(counts.get(y, 0)) for y in span}
    if baseline is not None:
        if any(not baseline.get(y) for y in span):
            span = range(max([y for y in span if not baseline.get(y)], default=min(years)) + 1,
                         until_year + 1)
        series = {y: raw[y] / baseline[y] for y in span}
    else:
        series = {y: float(raw[y]) for y in span}
    flagged = []
    for y in span:
        if y - window < span.start or y + window - 1 > until_year:
            continue
        before = [series[x] for x in range(y - window, y)]
        after = [series[x] for x in range(y, y + window)]
        rb = sum(raw[x] for x in range(y - window, y)) / window
        ra = sum(raw[x] for x in range(y, y + window)) / window
        if max(rb, ra) < min_count:
            continue
        mb, ma = sum(before) / window, sum(after) / window
        if mb == 0:
            if ma > 0:
                flagged.append((y, "surge", None, rb, ra))
            continue
        r = ma / mb
        if r >= ratio:
            flagged.append((y, "surge", r, rb, ra))
        elif r <= 1 / ratio:
            flagged.append((y, "drop", r, rb, ra))
    out: list[dict] = []
    run: list[tuple] = []

    def flush():
        if not run:
            return
        if run[0][1] == "surge":
            best = min(run, key=lambda f: (-(f[2] if f[2] is not None else float("inf")), f[0]))
        else:
            best = min(run, key=lambda f: (f[2], f[0]))
        y, direction, r, rb, ra = best
        out.append({"year": y, "direction": direction,
                    "ratio": round(r, 6) if r is not None else None,
                    "before_mean": round(rb, 6), "after_mean": round(ra, 6),
                    "window": window, "normalised": baseline is not None})
        run.clear()

    for f in flagged:
        if run and (f[0] != run[-1][0] + 1 or f[1] != run[-1][1]):
            flush()
        run.append(f)
    flush()
    return out


def trend(gw, term: str, *, until_year: int, normalise: bool = True, **kw) -> dict:
    counts = {y: n for y, n in counts_by_year(gw, term).items() if y <= until_year}
    base = baseline_counts(gw) if normalise else None
    return {"term": term, "until_year": until_year, "normalised": normalise,
            "counts": counts,
            "breaks": trend_breaks(counts, until_year=until_year, baseline=base, **kw)}


def _short(work_id) -> str | None:
    if not isinstance(work_id, str) or not work_id.strip():
        return None
    w = work_id.strip()
    return w[len(_ID_PREFIX):] if w.startswith(_ID_PREFIX) else w


def references(gw, term: str, *, top: int = 25) -> tuple[int, list[dict]]:
    body = gw.get_json(PROVIDER, "top_cited", API, text={"search": term},
                       fixed={"per-page": int(top), "sort": "cited_by_count:desc",
                              "select": "id,doi,publication_year,referenced_works"},
                       ttl_class="search", count=_n_results, validate=_is_works)
    rows = body["results"]
    works = []
    for r in rows:
        if not isinstance(r, dict) or _short(r.get("id")) is None:
            continue
        works.append({"id": _short(r["id"]), "doi": r.get("doi"),
                      "year": r.get("publication_year"),
                      "referenced_works": [s for s in map(_short, r.get("referenced_works") or [])
                                           if s]})
    return _meta_count(body), works


def _is_works(body) -> None:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise http.HTTPFailure("OpenAlex response has no results", "MALFORMED")
    _meta_count(body)


def _meta_count(body) -> int:
    n = (body.get("meta") or {}).get("count") if isinstance(body, dict) else None
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise http.HTTPFailure("OpenAlex response has no meta.count", "MALFORMED")
    return n


def shared_top(refs_a: Iterable[Mapping], refs_b: Iterable[Mapping]) -> list[str]:
    """Works among the top papers of both topics (direct overlap, sorted)."""
    ids = [{_short(w.get("id")) for w in refs} - {None} for refs in (refs_a, refs_b)]
    return sorted(ids[0] & ids[1])


def bridges(refs_a: Iterable[Mapping], refs_b: Iterable[Mapping], *, min_each: int = 2,
            limit: int = 20) -> list[dict]:
    refs_a, refs_b = list(refs_a), list(refs_b)
    # a paper top-cited in both topics is direct overlap: its references would be
    # counted on both sides and pass for a bridge, so it counts on neither
    both = set(shared_top(refs_a, refs_b))

    def citing(works):
        c: Counter = Counter()
        for w in works:
            if _short(w.get("id")) in both:
                continue
            c.update({s for s in map(_short, w.get("referenced_works") or []) if s})
        return c
    ca, cb = citing(refs_a), citing(refs_b)
    out = [{"work": w, "a_citing": ca[w], "b_citing": cb[w], "score": min(ca[w], cb[w])}
           for w in set(ca) & set(cb) if ca[w] >= min_each and cb[w] >= min_each]
    out.sort(key=lambda x: (-x["score"], -(x["a_citing"] + x["b_citing"]), x["work"]))
    return out[:limit]


def bridge_gap(gw, a: str, b: str, *, top: int = 25, min_each: int = 2, limit: int = 20) -> dict:
    from scieflow.core.review.claims import check_text

    for term in (a, b):
        check_text(term)
    n_a, works_a = references(gw, a, top=top)
    n_b, works_b = references(gw, b, top=top)
    both = '"{}" AND "{}"'.format(a.replace('"', " ").strip(), b.replace('"', " ").strip())
    n_ab = _meta_count(gw.get_json(PROVIDER, "co_occurrence", API, text={"search": both},
                                   fixed={"per-page": 1, "select": "id"}, ttl_class="search",
                                   count=lambda _v: 1, validate=_meta_count))
    low = min(n_a, n_b)
    return {"a": a, "b": b, "n_a": n_a, "n_b": n_b, "n_ab": n_ab,
            "overlap": round(n_ab / low, 6) if low else None,
            "shared_top": shared_top(works_a, works_b),
            "bridges": bridges(works_a, works_b, min_each=min_each, limit=limit)}
