"""Search PubMed (NCBI E-utilities). usage: scieflow research search pubmed "query" [--limit N] [--from-year YYYY] [--workspace SLUG]

Two requests, as the E-utilities require: esearch returns the PMIDs of the term (JSON), then efetch
returns the XML of those PMIDs only. The parser is a reduced port of researcher's PubMed connector
(MIT, credited in CREDITS.md): only the fields a paper record keeps. A clean negative (no hits) is
an empty list with no efetch; an esearch ERROR, a non-list id list or an efetch ERROR exits 1.

The query goes through `registry.check_pubmed_query` (the closed PubMed tag list, M-C2-D6) and the
year clause is composed in `registry.pubmed_term`, so no unchecked text reaches the wire.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET

from scieflow.research.lib import http, papers
from scieflow.research.search import registry

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PROVIDER = "search-pubmed"
MAX_PAGE = 100   # one efetch GET carries every id; a page is capped to keep the URL small


def _validate_esearch(body) -> None:
    result = body.get("esearchresult") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        raise http.HTTPFailure("PubMed esearch response has an unexpected shape", "MALFORMED")
    if result.get("ERROR"):
        raise http.HTTPFailure("PubMed esearch reported an error", "MALFORMED")
    if not isinstance(result.get("idlist", []), list):
        raise http.HTTPFailure("PubMed esearch id list is not a list", "MALFORMED")


def _idlist(body) -> list[str]:
    ids = body["esearchresult"].get("idlist") or []
    return [str(pmid) for pmid in ids if str(pmid).isdigit()]


def _esearch_count(body) -> int:
    return len(_idlist(body))


def _text(element) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year(article) -> int | None:
    """The issue year, else the leading year of a MedlineDate, else the electronic ArticleDate."""
    if article is None:
        return None
    issue_date = article.find("Journal/JournalIssue/PubDate")
    if issue_date is not None:
        year = _as_int(_text(issue_date.find("Year")))
        if year is not None:
            return year
        for token in _text(issue_date.find("MedlineDate")).replace("-", " ").split():
            if len(token) == 4 and token.isdigit():
                return int(token)
    return _as_int(_text(article.find("ArticleDate/Year")))


def _article(element) -> dict | None:
    citation = element.find("MedlineCitation")
    if citation is None:
        return None
    article = citation.find("Article")
    ids: dict[str, str] = {}
    for node in element.findall(".//ArticleIdList/ArticleId"):
        kind = (node.get("IdType") or "").strip().lower()
        value = _text(node)
        if kind and value and kind not in ids:
            ids[kind] = value
    pmid = _text(citation.find("PMID")) or ids.get("pubmed", "")
    doi = ids.get("doi", "")
    if not doi and article is not None:
        for node in article.findall("ELocationID"):
            if (node.get("EIdType") or "").strip().lower() == "doi":
                doi = _text(node)
    abstract = []
    if article is not None:
        for node in article.findall("Abstract/AbstractText"):
            body = _text(node)
            if body:
                label = (node.get("Label") or "").strip()
                abstract.append(f"{label}: {body}" if label else body)
    title = _text(article.find("ArticleTitle")) if article is not None else ""
    if title.endswith("."):           # MEDLINE ends a title with a period and brackets a translation
        title = title[:-1].rstrip()
    if title.startswith("[") and title.endswith("]"):
        title = title[1:-1].strip()
    journal = _text(article.find("Journal/Title")) if article is not None else ""
    return {"pmid": pmid, "doi": doi, "title": title, "abstract": " ".join(abstract),
            "journal": journal, "year": _year(article)}


def parse_articles(text: str) -> list[dict]:
    """The articles of an efetch answer. Unparseable XML, or an <ERROR> with no article, is a
    malformed answer (never a negative); an empty article set is a clean negative."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise http.HTTPFailure("PubMed efetch response is not valid XML", "MALFORMED") from exc
    articles = [a for a in (_article(e) for e in root.findall("PubmedArticle")) if a]
    if not articles and root.find("ERROR") is not None:
        raise http.HTTPFailure("PubMed efetch reported an error", "MALFORMED")
    return articles


def _count_text(text) -> int:
    return len(parse_articles(text))


def to_record(article: dict) -> dict:
    pmid = article["pmid"]
    return papers.record(
        {"pubmed": pmid} if pmid else None,
        doi=article["doi"],
        title=article["title"],
        year=article["year"],
        venue=article["journal"],
        cited_by=None,
        abstract=article["abstract"],
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        source="pubmed",
    )


def search(gw, checked, *, limit: int = 25, from_year: int | None = None) -> list:
    """The PubMed records for a checked query (`registry.check_pubmed_query`)."""
    term = registry.pubmed_term(checked, from_year)
    page = max(1, min(int(limit), MAX_PAGE))
    body = gw.get_json(PROVIDER, "esearch", ESEARCH, composed={"term": term},
                       fixed={"db": "pubmed", "retmode": "json", "sort": "relevance",
                              "retmax": page},
                       count=_esearch_count, validate=_validate_esearch)
    ids = _idlist(body)[:page]
    if not ids:
        return []                                    # a clean negative: no efetch
    gw.remember(PROVIDER, ids)
    if not all(gw.was_received(PROVIDER, pmid) for pmid in ids):
        raise http.HTTPFailure("PubMed efetch asked for an id esearch did not return", "MALFORMED")
    text = gw.get_text(PROVIDER, "efetch", EFETCH, count=_count_text,
                       ids={"id": ",".join(ids)},
                       fixed={"db": "pubmed", "retmode": "xml"}, validate=parse_articles)
    order = {pmid: index for index, pmid in enumerate(ids)}
    articles = sorted(parse_articles(text or ""), key=lambda a: order.get(a["pmid"], len(order)))
    return [to_record(article) for article in articles]


def _search(gw, args) -> list:
    return search(gw, registry.check_pubmed_query(args.query), limit=args.limit,  # refused first
                  from_year=args.from_year)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research search pubmed")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-year", type=int)
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    registry.run(args.workspace, lambda gw: _search(gw, args), network_mode=args.network)


if __name__ == "__main__":
    main()
