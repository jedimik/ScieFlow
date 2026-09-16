"""Search Crossref. usage: scieflow research search crossref "query" [--limit N] [--from-year YYYY]"""

import argparse
import re

from scieflow.research.lib import http, papers

API = "https://api.crossref.org/works"


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


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search crossref")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    args = ap.parse_args(argv)

    params = {"query": args.query, "rows": args.limit}
    if args.from_year:
        params["filter"] = f"from-pub-date:{args.from_year}-01-01"
    data = http.get(API, params=params).json()
    papers.emit([norm(i) for i in data["message"].get("items", [])])


if __name__ == "__main__":
    main()
