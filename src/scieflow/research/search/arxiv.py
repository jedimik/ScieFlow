"""Search arXiv. usage: scieflow research search arxiv "query" [--limit N] [--from-year YYYY]"""

import argparse
import re
import xml.etree.ElementTree as ET

from scieflow.research.lib import http, papers

API = "https://export.arxiv.org/api/query"
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


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search arxiv")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    args = ap.parse_args(argv)

    params = {
        "search_query": f"all:{args.query}",
        "max_results": args.limit,
        "sortBy": "relevance",
    }
    root = ET.fromstring(http.get(API, params=params).text)
    records = [norm(e) for e in root.findall("atom:entry", NS)]
    if args.from_year:
        records = [r for r in records if r["year"] and r["year"] >= args.from_year]
    papers.emit(records)


if __name__ == "__main__":
    main()
