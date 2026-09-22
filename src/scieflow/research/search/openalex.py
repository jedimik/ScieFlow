"""Search OpenAlex. usage: scieflow research search openalex "query" [--limit N] [--from-year YYYY]"""

import argparse

from scieflow.research.lib import http, papers

API = "https://api.openalex.org/works"


def _abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    positions = [(i, word) for word, idxs in inv.items() for i in idxs]
    return " ".join(word for _, word in sorted(positions))


def _authors(work: dict) -> list[str]:
    names = []
    for entry in work.get("authorships") or []:
        name = (entry.get("author") or {}).get("display_name")
        if name:
            names.append(name)
    return names


def _signals(work: dict, oa: dict, loc: dict) -> dict:
    """What OpenAlex returns that the core fields have no room for."""
    fields = {
        "is_oa": oa.get("is_oa"),
        "oa_status": oa.get("oa_status"),
        "license": loc.get("license"),
        "type": work.get("type"),
        "authors": _authors(work) or None,
    }
    return {k: v for k, v in fields.items() if v is not None}


def norm(work: dict) -> dict:
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    oa = work.get("open_access") or {}
    doi = (work.get("doi") or "").replace("https://doi.org/", "") or None
    return papers.record(
        signals={"openalex": _signals(work, oa, loc)},
        doi=doi,
        title=work.get("display_name"),
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        cited_by=work.get("cited_by_count"),
        abstract=_abstract(work.get("abstract_inverted_index")),
        url=oa.get("oa_url") or work.get("id"),
        source="openalex",
    )


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search openalex")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    args = ap.parse_args(argv)

    params = {"search": args.query, "per-page": min(args.limit, 200)}
    if args.from_year:
        params["filter"] = f"from_publication_date:{args.from_year}-01-01"
    data = http.get(API, params=params).json()
    papers.emit([norm(w) for w in data.get("results", [])][: args.limit])


if __name__ == "__main__":
    main()
