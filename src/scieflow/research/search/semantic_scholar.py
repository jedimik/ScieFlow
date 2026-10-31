"""Search Semantic Scholar (Graph API paper search). usage: scieflow research search semantic-scholar "query" [--limit N] [--from-year YYYY] [--workspace SLUG]

A keyless relevance search over /paper/search. The record mapping is a reduced port of researcher's
Semantic Scholar connector (MIT, credited in CREDITS.md). No API key is read or sent: the request
carries only the checked query, the field list, the limit and, with --from-year, the open-ended year
range composed here (`2020-`).
"""

from __future__ import annotations

import argparse

from scieflow.research.lib import http, papers
from scieflow.research.search import registry

API = "https://api.semanticscholar.org/graph/v1/paper/search"
PROVIDER = "search-semantic-scholar"
# The search field list of the upstream connector: its paper fields minus the abstract, which
# bloats a 25-hit page.
SEARCH_FIELDS = ("paperId", "externalIds", "url", "title", "venue", "publicationVenue", "year",
                 "publicationDate", "journal", "authors", "citationCount", "referenceCount",
                 "isOpenAccess", "openAccessPdf", "publicationTypes")
MAX_LIMIT = 100  # the search endpoint's page cap


def _validate(body) -> None:
    if not isinstance(body, dict):
        raise http.HTTPFailure("Semantic Scholar response has an unexpected shape", "MALFORMED")
    if "data" in body and not isinstance(body["data"], list):
        raise http.HTTPFailure("Semantic Scholar response has an unexpected shape", "MALFORMED")


def _count(body) -> int:
    data = body.get("data") if isinstance(body, dict) else None
    return len(data or [])


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def to_record(raw: dict) -> dict:
    external = _mapping(raw.get("externalIds"))
    journal = _mapping(raw.get("journal"))
    venue_obj = _mapping(raw.get("publicationVenue"))
    venue = journal.get("name") or venue_obj.get("name") or raw.get("venue") or ""
    paper_id = str(raw.get("paperId") or "")
    return papers.record(
        {"semantic_scholar": paper_id} if paper_id else None,
        doi=external.get("DOI"),
        title=str(raw.get("title") or ""),
        year=raw.get("year"),
        venue=str(venue),
        cited_by=raw.get("citationCount"),
        abstract=None,
        url=raw.get("url"),
        source="semantic-scholar",
    )


def search(gw, checked, *, limit: int = 25, from_year: int | None = None) -> list:
    """The Semantic Scholar records for a checked query (`registry.check_query`)."""
    fixed: dict = {"fields": ",".join(SEARCH_FIELDS),
                   "limit": max(1, min(int(limit), MAX_LIMIT))}
    if from_year is not None:
        if not isinstance(from_year, int) or isinstance(from_year, bool):
            raise ValueError("from_year must be an integer year")
        fixed["year"] = f"{from_year}-"
    body = gw.get_json(PROVIDER, "search", API, checked={"query": checked}, fixed=fixed,
                       count=_count, validate=_validate)
    return [to_record(item) for item in (body.get("data") or []) if isinstance(item, dict)]


def _search(gw, args) -> list:
    return search(gw, registry.check_query(args.query), limit=args.limit,  # refused first
                  from_year=args.from_year)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search semantic-scholar")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args), network_mode=args.network)


if __name__ == "__main__":
    main()
