"""Search Crossref. usage: scieflow research search crossref "query" [--limit N] [--from-year YYYY] [--workspace SLUG]"""

import argparse
import re

from scieflow.research.lib import http, papers
from scieflow.research.search import registry

API = "https://api.crossref.org/works"
PROVIDER = "search-crossref"


def _strip_jats(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)  # Replace tags with space
    text = re.sub(r"\s+", " ", text).strip()  # Collapse whitespace
    text = re.sub(r"\s+([.,:;!?])", r"\1", text)  # Remove space before punctuation
    return text or None


def norm(item: dict) -> dict:
    parts = (item.get("published") or {}).get("date-parts", [[None]])
    year = None
    if parts and parts[0]:
        year = parts[0][0]
    return papers.record(
        doi=item.get("DOI"),
        title=(item.get("title") or [None])[0],
        year=year,
        venue=(item.get("container-title") or [None])[0],
        cited_by=item.get("is-referenced-by-count"),
        abstract=_strip_jats(item.get("abstract")),
        url=item.get("URL"),
        source="crossref",
    )


def _count(body) -> int:
    message = body.get("message") if isinstance(body, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    return len(items or [])


def _validate(body) -> None:
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("items", []), list):
        raise http.HTTPFailure("Crossref response has an unexpected shape", "MALFORMED")


def search(gw, checked, *, limit: int = 25, from_year: int | None = None) -> list:
    """The Crossref records for a checked query (`registry.check_query`)."""
    fixed: dict = {"rows": limit}
    if from_year:
        fixed["filter"] = f"from-pub-date:{from_year}-01-01"
    data = gw.get_json(PROVIDER, "search", API, checked={"query": checked}, fixed=fixed,
                       count=_count, validate=_validate)
    return [norm(i) for i in data["message"].get("items", [])]


def _search(gw, args) -> list:
    return search(gw, registry.check_query(args.query), limit=args.limit,   # refused before any request
                  from_year=args.from_year)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search crossref")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args), network_mode=args.network)


if __name__ == "__main__":
    main()
