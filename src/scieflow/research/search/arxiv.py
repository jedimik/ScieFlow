"""Search arXiv. usage: scieflow research search arxiv "query" [--limit N] [--from-year YYYY] [--workspace SLUG]"""

import argparse
import re
import xml.etree.ElementTree as ET

from scieflow.research.lib import http, papers
from scieflow.research.search import registry

API = "https://export.arxiv.org/api/query"
PROVIDER = "search-arxiv"
NS = {"atom": "http://www.w3.org/2005/Atom"}


def _clean(text: str | None) -> str | None:
    return re.sub(r"\s+", " ", text).strip() if text else None


def norm(entry: ET.Element) -> dict:
    arxiv_id = (entry.findtext("atom:id", "", NS) or "").split("/abs/", 1)[-1]
    bare_id = re.sub(r"v\d+$", "", arxiv_id)
    published = entry.findtext("atom:published", "", NS)
    pdf_url = None
    for link in entry.findall("atom:link", NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")
    return papers.record(
        doi=f"10.48550/arXiv.{bare_id}" if bare_id else None,
        title=_clean(entry.findtext("atom:title", None, NS)),
        year=int(published[:4]) if published[:4].isdigit() else None,
        venue="arXiv",
        cited_by=None,
        abstract=_clean(entry.findtext("atom:summary", None, NS)),
        url=pdf_url or entry.findtext("atom:id", None, NS),
        source="arxiv",
    )


def _entries(text: str) -> list:
    return ET.fromstring(text).findall("atom:entry", NS)


def _validate(text: str) -> None:
    try:
        ET.fromstring(text)
    except ET.ParseError:
        raise http.HTTPFailure("arXiv response is not XML", "MALFORMED") from None


def search(gw, checked, *, limit: int = 25, from_year: int | None = None) -> list:
    """The arXiv records for a checked query (`registry.check_query`)."""
    text = gw.get_text(PROVIDER, "query", API,
                       composed={"search_query": registry.arxiv_query(checked)},
                       fixed={"max_results": limit, "sortBy": "relevance"},
                       count=lambda t: len(_entries(t)), validate=_validate)
    records = [norm(e) for e in _entries(text)]
    if from_year:
        records = [r for r in records if r["year"] and r["year"] >= from_year]
    return records


def _search(gw, args) -> list:
    return search(gw, registry.check_query(args.query), limit=args.limit,   # refused before any request
                  from_year=args.from_year)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search arxiv")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args), network_mode=args.network)


if __name__ == "__main__":
    main()
