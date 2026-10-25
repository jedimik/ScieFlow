"""Search Europe PMC. usage: scieflow research search europepmc "query" [--limit N] [--from-year YYYY] [--workspace SLUG]"""

import argparse

from scieflow.research.lib import papers
from scieflow.research.search import registry

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PROVIDER = "search-europepmc"


def norm(res: dict) -> dict:
    pdf = None
    for u in (res.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if u.get("documentStyle") == "pdf":
            pdf = u.get("url")
            break  # first listed PDF, as scripts/nblm/resolve.py does
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


def _count(body) -> int:
    results = (body.get("resultList") or {}).get("result") if isinstance(body, dict) else None
    return len(results or [])


def _search(gw, args) -> list:
    query = registry.check_query(args.query)            # refused here, before any request
    data = gw.get_json(PROVIDER, "search", API,
                       composed={"query": registry.europepmc_query(query, args.from_year)},
                       fixed={"format": "json", "pageSize": args.limit, "resultType": "core"},
                       count=_count, validate=registry.expect_object)
    results = (data.get("resultList") or {}).get("result", [])
    return [norm(r) for r in results]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search europepmc")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args))


if __name__ == "__main__":
    main()
