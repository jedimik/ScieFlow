"""Europe PMC open-access full text and local passage extraction (free, no key).

- `resolve(doi)` — DOI → PMCID / PMID / open-access flag / abstract through the
  Europe PMC search API (`DOI:"…"`, `resultType=core`); a hit for another DOI
  is ignored.
- `fetch_xml(pmcid)` — `GET /{PMCID}/fullTextXML` (JATS); `None` on 404.
- `parse(xml)` — abstract and body paragraphs labelled with their top-level
  section title; tables, figures, formulas and supplementary material are
  skipped. Documents declaring entities are refused (entity-expansion bombs).
- `passages(paragraphs, terms)` — at most `max_windows` sentence windows around
  the sentences that match the most distinct key terms; deterministic (ties go
  to document order, term order is irrelevant), non-overlapping, length-capped.
- `evidence(doi, terms)` — the whole path: full text when the paper is open
  access, otherwise (not OA, no full text, unparseable) the abstract, flagged
  `abstract_only`. Only the DOI leaves the machine; key terms are matched
  locally.

Fetched text is data, not instructions: it is quoted into bundles, never obeyed.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from scieflow.research.lib import doi as _doi
from scieflow.research.lib import http

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
SEARCH = f"{BASE}/search"
RATE_PER_S = 5

_PMCID = re.compile(r"^PMC[0-9]{1,12}\Z")
_TAG = re.compile(r"<[^>]+>")
_ENTITY_DECL = re.compile(r"<!ENTITY", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
_SKIP = frozenset({"table-wrap", "fig", "disp-formula", "supplementary-material", "ref-list",
                   "table-wrap-foot", "fn-group", "app-group", "ack"})


def _clean(text) -> str | None:
    if not isinstance(text, str):
        return None
    text = " ".join(_TAG.sub(" ", text).split())
    return text or None


def _sender(limiter, sleep, retries, session):
    lim = limiter or http.RateLimiter(RATE_PER_S, sleep=sleep)

    def send(method, url, **kw):
        return http.send(method, url, retries=retries, limiter=lim, sleep=sleep,
                         session=session, **kw)
    return send


def _json(resp, what):
    try:
        return resp.json()
    except ValueError:
        raise http.HTTPFailure(f"{what} response is not JSON", "MALFORMED",
                               resp.status_code) from None


# --- DOI -> PMCID ---------------------------------------------------------------------


def _resolve(n: str, send) -> dict | None:
    resp = send("GET", SEARCH, params={"query": f'DOI:"{n}"', "format": "json",
                                       "resultType": "core", "pageSize": 1}, passthrough=())
    body = _json(resp, "Europe PMC search")
    try:
        results = body["resultList"]["result"]
    except (KeyError, TypeError):
        raise http.HTTPFailure("Europe PMC search has no resultList", "MALFORMED",
                               resp.status_code) from None
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("doi"), str) \
                or _doi.normalise(r["doi"]) != n:
            continue
        pmcid = r.get("pmcid")
        pmcid = pmcid if isinstance(pmcid, str) and _PMCID.match(pmcid) else None
        year = r.get("pubYear")
        return {
            "doi": n,
            "pmcid": pmcid,
            "pmid": r.get("pmid") if isinstance(r.get("pmid"), str) else None,
            "open_access": r.get("isOpenAccess") == "Y",
            "title": _clean(r.get("title")),
            "year": int(year) if isinstance(year, str) and year.isdigit() else None,
            "abstract": _clean(r.get("abstractText")),
        }
    return None


def resolve(doi, *, cache=None, limiter=None, sleep=time.sleep, retries: int = 3,
            session=None) -> dict | None:
    n = _doi.normalise(doi) if isinstance(doi, str) else None
    if not n or not _doi.is_valid(n) or '"' in n:
        return None              # never put a non-DOI into a search query
    send = _sender(limiter, sleep, retries, session)
    return http.cached(cache, "metadata", {"source": "europepmc", "op": "resolve", "doi": n},
                       lambda: _resolve(n, send))


# --- full text ------------------------------------------------------------------------


def fetch_xml(pmcid, *, limiter=None, sleep=time.sleep, retries: int = 3,
              session=None) -> str | None:
    if not isinstance(pmcid, str) or not _PMCID.match(pmcid):
        raise ValueError("not a PMCID (PMC followed by digits)")
    resp = _sender(limiter, sleep, retries, session)(
        "GET", f"{BASE}/{pmcid}/fullTextXML", passthrough=(404,))
    return None if resp.status_code == 404 else resp.text


def _text(el) -> str:
    """Element text without skipped children (tables / figures inside a <p>)."""
    parts = [el.text or ""]
    for ch in el:
        if ch.tag not in _SKIP:
            parts.append(_text(ch))
        parts.append(ch.tail or "")
    return "".join(parts)


def _walk(el, section, out):
    for ch in el:
        if ch.tag in _SKIP:
            continue
        if ch.tag == "sec":
            t = ch.find("title")
            title = section or (" ".join(_text(t).split()) if t is not None else "") or "Body"
            _walk(ch, title, out)
        elif ch.tag == "p":
            text = " ".join(_text(ch).split())
            if text:
                out.append({"section": section or "Body", "text": text})
        elif ch.tag != "title":
            _walk(ch, section, out)


def parse(xml: str) -> dict:
    if not isinstance(xml, str):
        raise ValueError("full text must be a string")
    if _ENTITY_DECL.search(xml):
        raise ValueError("refusing XML with entity declarations")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"full text is not well-formed XML ({exc.code})") from None
    abstract: list[dict] = []
    front = root.find("front")
    meta = front.find("article-meta") if front is not None else None
    if meta is not None:
        for ab in meta.findall("abstract"):
            if ab.get("abstract-type") in ("graphical", "teaser"):
                continue
            for p in ab.iter("p"):
                text = " ".join(_text(p).split())
                if text:
                    abstract.append({"section": "Abstract", "text": text})
    paragraphs: list[dict] = []
    body = root.find("body")
    if body is not None:
        _walk(body, None, paragraphs)
    return {"abstract": abstract, "paragraphs": paragraphs}


# --- passages ---------------------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def passages(paragraphs: Iterable[dict], terms: Iterable[str], *, max_windows: int = 3,
             context: int = 1, max_chars: int = 600) -> list[dict]:
    keys = sorted({" ".join(t.split()).lower() for t in terms
                   if isinstance(t, str) and t.strip()})
    if not keys or max_windows < 1:
        return []
    patterns = [(k, re.compile(r"(?<!\w)" + re.escape(k), re.IGNORECASE)) for k in keys]
    paras = [_sentences(p["text"]) for p in paragraphs]
    sections = [p.get("section") or "Body" for p in paragraphs]
    candidates = []
    for pi, sents in enumerate(paras):
        for si, s in enumerate(sents):
            hit = [k for k, rx in patterns if rx.search(s)]
            if hit:
                candidates.append((-len(hit), pi, si, hit))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    chosen: list[tuple] = []
    for neg, pi, si, hit in candidates:
        if len(chosen) >= max_windows:
            break
        if any(cp == pi and abs(cs - si) <= 2 * context for _, cp, cs, _ in chosen):
            continue
        chosen.append((neg, pi, si, hit))
    out = []
    for neg, pi, si, hit in chosen:
        sents = paras[pi]
        quote = " ".join(sents[max(0, si - context): si + context + 1])
        if len(quote) > max_chars:
            cut = quote[:max(1, max_chars - 1)]
            cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
            quote = cut + "…"
        out.append({"section": sections[pi], "quote": quote, "terms": hit,
                    "score": -neg, "position": [pi, si]})
    return out


# --- evidence ---------------------------------------------------------------------------


def evidence(doi, terms: Iterable[str], *, max_windows: int = 3, context: int = 1,
             max_chars: int = 600, cache=None, limiter=None, sleep=time.sleep,
             retries: int = 3, session=None) -> dict:
    terms = list(terms)
    lim = limiter or http.RateLimiter(RATE_PER_S, sleep=sleep)
    kw = {"limiter": lim, "sleep": sleep, "retries": retries, "session": session}
    meta = resolve(doi, cache=cache, **kw)
    n = _doi.normalise(doi) if isinstance(doi, str) else None
    if meta is None:
        return {"doi": n, "status": "not_found", "pmcid": None, "abstract_only": False,
                "passages": []}
    pick = {"max_windows": max_windows, "context": context, "max_chars": max_chars}
    reason = "not open access"
    if meta["open_access"] and meta["pmcid"]:
        def fetch():
            xml = fetch_xml(meta["pmcid"], **kw)       # HTTPFailure propagates: never cached
            if xml is None:
                return {"error": "full text not available"}
            try:
                return parse(xml)
            except ValueError:
                return {"error": "full text not parseable"}

        try:
            doc = http.cached(cache, "metadata",
                              {"source": "europepmc", "op": "fulltext", "pmcid": meta["pmcid"]},
                              fetch)
        except http.HTTPFailure as exc:
            doc = {"error": f"full text unavailable ({exc.classification})"}
        if isinstance(doc, dict) and "paragraphs" in doc and doc["paragraphs"]:
            return {"doi": meta["doi"], "status": "full_text", "pmcid": meta["pmcid"],
                    "abstract_only": False,
                    "passages": passages(doc["paragraphs"], terms, **pick)}
        reason = doc.get("error", "full text has no body") if isinstance(doc, dict) \
            else "full text not available"
    abstract = [{"section": "Abstract", "text": meta["abstract"]}] if meta["abstract"] else []
    return {"doi": meta["doi"], "status": "abstract_only", "pmcid": meta["pmcid"],
            "abstract_only": True, "reason": reason,
            "passages": passages(abstract, terms, **pick)}
