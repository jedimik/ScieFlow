"""M-C2 t6 test support: a synthetic citation graph served through `responses` (no network).

The graph is tests/fixtures/kernel2/graph/synthetic-graph.json (invented DOIs and titles). OpenAlex
(works by DOI, cites:, openalex: filters) and OpenCitations (citations/, references/) are answered
from it. A request for a DOI or work the graph does not hold gets the clean "no such record" answer
(404 for OpenAlex's DOI lookup, an empty list for OpenCitations), exactly as the live services do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

REPO = Path(__file__).resolve().parents[3]
GRAPH_FILE = REPO / "tests" / "fixtures" / "kernel2" / "graph" / "synthetic-graph.json"
GRAPH = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
WORKS = GRAPH["works"]
EDGES = [tuple(e) for e in GRAPH["edges"]]
DOI_OF_W = {meta["w"]: doi for doi, meta in WORKS.items()}

OPENALEX_RE = re.compile(r"https://api\.openalex\.org/.*")
OPENCITATIONS_RE = re.compile(r"https://api\.opencitations\.net/index/v1/.*")
S2_RE = re.compile(r"https://api\.semanticscholar\.org/.*")


def openalex_work(doi: str) -> dict:
    meta = WORKS[doi]
    return {
        "id": f"https://openalex.org/{meta['w']}",
        "doi": f"https://doi.org/{doi}",
        "display_name": meta["title"],
        "title": meta["title"],
        "publication_year": meta["year"],
        "type": "article",
        "authorships": [],
        "referenced_works": [f"https://openalex.org/{WORKS[cited]['w']}"
                             for citing, cited in EDGES if citing == doi and cited in WORKS],
    }


def _json(status: int, body) -> tuple:
    return status, {"Content-Type": "application/json"}, json.dumps(body)


def openalex_answer(request) -> tuple:
    parts = urlsplit(request.url)
    query = parse_qs(parts.query)
    if parts.path.startswith("/works/doi:"):
        doi = unquote(parts.path[len("/works/doi:"):])
        if doi not in WORKS:
            return _json(404, {"error": "Not found"})
        return _json(200, openalex_work(doi))
    if parts.path == "/works":
        flt = query.get("filter", [""])[0]
        if flt.startswith("cites:"):
            cited = DOI_OF_W.get(flt[len("cites:"):])
            citing = [a for a, b in EDGES if b == cited]
            return _json(200, {"results": [openalex_work(d) for d in citing]})
        if flt.startswith("openalex:"):
            dois = [DOI_OF_W[w] for w in flt[len("openalex:"):].split("|") if w in DOI_OF_W]
            return _json(200, {"results": [openalex_work(d) for d in dois]})
    return _json(404, {"error": "unexpected request in the synthetic graph"})


def opencitations_answer(request) -> tuple:
    path = urlsplit(request.url).path
    prefix, _, doi = path[len("/index/v1/"):].partition("/")
    doi = unquote(doi)
    if prefix == "citations":
        rows = [{"citing": a, "cited": b} for a, b in EDGES if b == doi]
    elif prefix == "references":
        rows = [{"citing": a, "cited": b} for a, b in EDGES if a == doi]
    else:
        return _json(404, {"error": "unknown operation"})
    return _json(200, rows)
