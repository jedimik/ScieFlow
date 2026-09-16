"""Search Europe PMC. usage: scieflow research search europepmc "query" [--limit N] [--from-year YYYY]"""

import argparse

from scieflow.research.lib import http, papers

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def norm(res: dict) -> dict:
    pdf = None
    for u in (res.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if u.get("documentStyle") == "pdf":
            pdf = u.get("url")
    year = res.get("pubYear")
    return papers.record(
        doi=res.get("doi"),
        title=res.get("title"),
        year=int(year) if year and str(year).isdigit() else None,
        venue=res.get("journalTitle"),
        cited_by=res.get("citedByCount"),
        abstract=res.get("abstractText"),
        url=pdf,
        source="europepmc",
    )


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search europepmc")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    args = ap.parse_args(argv)

    query = args.query
    if args.from_year:
        query += f" AND PUB_YEAR:[{args.from_year} TO 3000]"
    params = {"query": query, "format": "json", "pageSize": args.limit, "resultType": "core"}
    data = http.get(API, params=params).json()
    results = (data.get("resultList") or {}).get("result", [])
    papers.emit([norm(r) for r in results])


if __name__ == "__main__":
    main()
