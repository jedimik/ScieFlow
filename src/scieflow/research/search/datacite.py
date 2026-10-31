"""Search DataCite (datasets, software, preprints; the DOI registry for research outputs).
usage: scieflow research search datacite "query" [--limit N] [--from-year YYYY] [--workspace SLUG]

A keyless search of DataCite's /dois index, a JSON:API answer. Named by the user only: DataCite is
not in the default `search all` set (M-C2-D7). The record mapping is a reduced port of researcher's
DataCite connector (MIT, credited in CREDITS.md). The query is composed by
`registry.datacite_query`: escaped by the vendored Lucene escape, then wrapped in code.
"""

from __future__ import annotations

import argparse

from scieflow.research.lib import http, papers
from scieflow.research.search import registry

API = "https://api.datacite.org/dois"
PROVIDER = "search-datacite"
MAX_SIZE = 100   # DataCite caps page[size] at 1000; a caller here never needs more than 100


def _validate(body) -> None:
    if not isinstance(body, dict):
        raise http.HTTPFailure("DataCite response has an unexpected shape", "MALFORMED")
    if "data" in body and not isinstance(body["data"], list):
        raise http.HTTPFailure("DataCite response has an unexpected shape", "MALFORMED")


def _count(body) -> int:
    data = body.get("data") if isinstance(body, dict) else None
    return len(data or [])


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first_text(items, key: str, *, prefer: str | None = None) -> str:
    rows = [row for row in (items or []) if isinstance(row, dict) and row.get(key)]
    if prefer is not None:
        rows.sort(key=lambda row: 0 if str(row.get("descriptionType", "")).lower() == prefer
                  else 1)
    return str(rows[0][key]) if rows else ""


def to_record(item: dict) -> dict:
    attrs = _mapping(item.get("attributes"))
    container = _mapping(attrs.get("container"))
    doi = str(attrs.get("doi") or item.get("id") or "")
    url = attrs.get("url") or (f"https://doi.org/{doi}" if doi else "")
    return papers.record(
        None,
        doi=doi,
        title=_first_text(attrs.get("titles"), "title"),
        year=attrs.get("publicationYear"),
        venue=str(container.get("title") or ""),
        cited_by=attrs.get("citationCount"),
        abstract=_first_text(attrs.get("descriptions"), "description", prefer="abstract"),
        url=str(url),
        source="datacite",
    )


def search(gw, checked, *, limit: int = 25, from_year: int | None = None) -> list:
    """The DataCite records for a checked query (`registry.check_query`)."""
    size = max(1, min(int(limit), MAX_SIZE))
    composed = registry.datacite_query(checked, from_year)
    body = gw.get_json(PROVIDER, "dois", API, composed={"query": composed},
                       fixed={"page[size]": size}, count=_count, validate=_validate)
    return [to_record(item) for item in (body.get("data") or []) if isinstance(item, dict)][:size]


def _search(gw, args) -> list:
    return search(gw, registry.check_query(args.query), limit=args.limit,  # refused first
                  from_year=args.from_year)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search datacite")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args), network_mode=args.network)


if __name__ == "__main__":
    main()
