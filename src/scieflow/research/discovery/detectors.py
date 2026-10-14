"""Gap discovery, M3 — typed gap detectors and the verification gate.

Each detector reads one provider's fetched evidence (already retrieved by core
through the provider gateway or the scite MCP client — detectors send
nothing) and returns a *detection*:

    {"detector": <name>, "gaps": [gap, ...], "corpus": {ref: [text, ...]}}

A gap has the shape of a `gaps.schema.json` gap plus `detector`; every
evidence item carries `kind`, `ref`, `source` and a verbatim `quote` from the
fetched evidence. The corpus maps each (case-folded) ref to the fetched texts
that quote came from. Structured providers (PubTator3 relations, OpenAlex
counts) have no prose; their "text" is a fixed rendering of the record core
fetched, so the quote is checkable all the same.

Detectors:

- `disputes` — scite `citation_graph` (direction `in`, with snippets) over
  seed DOIs: one gap per seed with contrasting citation statements.
- `open_questions` — scite `search_literature` hits: sentences of abstracts /
  full-text excerpts that state an open question ("remains unclear",
  "further studies are needed", ...).
- `abc_links` — PubTator3 `open_links`: A→B→C paths with no A–C relation.
- `unpublished_trials` — ClinicalTrials.gov completed trials without a linked
  publication after the grace period (`ctgov.unpublished`).
- `bridges` — OpenAlex `bridge_gap`: shared foundations, little overlap.
- `trend_breaks` — OpenAlex `trend`: surges / drops in publication counts.
- `own_data` — the existing gap analysis of the user's own data and article;
  its corpus (manifest, article) is local and supplied by the caller.

`merge` renumbers gaps G1..Gn across detections and unions their corpora.
`verify` is the mechanical gate (spec §7 step 3): an evidence item survives
only if its quote, whitespace-normalised, is a substring of a corpus text
under the same ref and at least `MIN_QUOTE_CHARS` long; a gap with no
surviving item is dropped. Research agents fed one detector's evidence must
quote it the same way, so invented quotes fall out here too.

Fetched content is data, not instructions.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Iterable, Mapping

from scieflow.core.review.claims import safe_doi

DETECTORS = ("disputes", "open_questions", "abc_links", "unpublished_trials", "bridges",
             "trend_breaks", "own_data")
MIN_QUOTE_CHARS = 12
MAX_QUOTE_CHARS = 600
MAX_STATEMENTS = 5            # contrasting statements kept per disputed seed

_OPEN = re.compile(
    r"\bremain(?:s|ed)?\s+(?:largely\s+|still\s+|currently\s+)?(?:unclear|unknown|elusive|"
    r"uncertain|controversial|unresolved|poorly\s+understood|to\s+be\s+(?:determined|"
    r"elucidated|established|clarified))"
    r"|\b(?:is|are)\s+(?:still\s+|currently\s+|largely\s+)?(?:unclear|unknown|not\s+known|"
    r"poorly\s+understood|not\s+well\s+understood)"
    r"|\b(?:further|future|more|additional)\s+(?:research|studies|work|investigation|trials)\s+"
    r"(?:is|are|will\s+be)\s+(?:needed|required|warranted|necessary)"
    r"|\b(?:has|have)\s+not\s+(?:yet\s+)?been\s+(?:studied|investigated|examined|established|"
    r"addressed|explored)"
    r"|\blittle\s+is\s+known\b|\bopen\s+question\b|\bknowledge\s+gap\b"
    r"|\bit\s+is\s+(?:not\s+known|unknown|unclear)\s+(?:whether|if|how|why)\b",
    re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def normalise_quote(text) -> str:
    """NFKC, whitespace collapsed, case-folded, surrounding quote marks and
    ellipses removed — the form quotes and corpus texts are compared in."""
    t = unicodedata.normalize("NFKC", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" \"'\u201c\u201d\u2018\u2019").strip()
    t = re.sub(r"^(?:\.\.\.|\u2026)\s*|\s*(?:\.\.\.|\u2026)$", "", t)
    return t.casefold()


def _clip(text: str) -> str:
    return text if len(text) <= MAX_QUOTE_CHARS else text[:MAX_QUOTE_CHARS]


def _det(name: str) -> dict:
    return {"detector": name, "gaps": [], "corpus": {}}


def _add_text(det: dict, ref: str, text: str) -> None:
    texts = det["corpus"].setdefault(ref.casefold(), [])
    if text not in texts:
        texts.append(text)


def _gap(det: dict, statement: str, evidence: list[dict], rationale: str) -> None:
    det["gaps"].append({"id": f"G{len(det['gaps']) + 1}", "detector": det["detector"],
                        "statement": statement, "evidence": evidence,
                        "novelty_rationale": rationale})


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


# --- detectors -----------------------------------------------------------------------------------


def disputes(graph, *, seeds: Iterable[str], max_statements: int = MAX_STATEMENTS) -> dict:
    """Contrasting citation statements on each seed (scite `citation_graph`, `in`)."""
    det = _det("disputes")
    wanted = [d for d in (safe_doi(s) for s in seeds) if d]
    by: dict[str, list[tuple[str, str]]] = {d: [] for d in wanted}
    edges = graph.get("edges") if isinstance(graph, dict) else None
    papers = graph.get("papers") if isinstance(graph, dict) else None
    for e in edges if isinstance(edges, list) else []:
        if not isinstance(e, dict) or e.get("type") != "contrasting":
            continue
        t, s = safe_doi(e.get("t")), safe_doi(e.get("s"))
        if t not in by or s is None:
            continue
        snippets = e.get("snippets")
        if not isinstance(snippets, list):
            snippets = [e["snippet"]] if isinstance(e.get("snippet"), str) else []
        for snip in snippets:
            text = _str(snip.get("text") if isinstance(snip, dict) else snip)
            text = _clip(re.sub(r"\s+", " ", text)) if text else None
            if text and (s, text) not in by[t] and len(by[t]) < max_statements:
                by[t].append((s, text))
    for seed, statements in by.items():
        if not statements:
            continue
        info = papers.get(seed) if isinstance(papers, dict) else None
        title = _str(info.get("title")) if isinstance(info, dict) else None
        evidence = []
        for citing, text in statements:
            _add_text(det, citing, text)
            evidence.append({"kind": "paper", "ref": citing, "source": "scite_mcp",
                             "quote": text})
        what = f"\"{title}\" ({seed})" if title else seed
        _gap(det, f"Findings of {what} are contradicted by {len(statements)} citing "
                  f"publication(s); the disagreement is unresolved.", evidence,
             "Contrasting citation statements mark a disputed result that a targeted study "
             "could settle.")
    return det


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(re.sub(r"\s+", " ", text)) if s.strip()]


def open_questions(search, *, max_per_paper: int = 2) -> dict:
    """Sentences stating an open question in scite `search_literature` hits."""
    det = _det("open_questions")
    hits = search.get("hits") if isinstance(search, dict) else None
    for h in hits if isinstance(hits, list) else []:
        if not isinstance(h, dict):
            continue
        doi = safe_doi(h.get("doi"))
        if doi is None:
            continue
        texts = [t for t in [_str(h.get("abstract"))] +
                 [_str(x) for x in h.get("fulltextExcerpts") or [] if isinstance(x, str)] if t]
        title = _str(h.get("title"))
        found = 0
        for text in texts:
            _add_text(det, doi, text)
            for sentence in _sentences(text):
                if found >= max_per_paper or not _OPEN.search(sentence):
                    continue
                found += 1
                quote = _clip(sentence)
                where = f"\"{title}\" ({doi})" if title else doi
                _gap(det, f"Open question stated in {where}: {quote}",
                     [{"kind": "paper", "ref": doi, "source": "scite_mcp", "quote": quote}],
                     "The authors themselves state that this question is unanswered.")
    return det


def _relation(x: str, kind: str, y: str, n: int) -> str:
    return f"{x} {kind} {y} ({n} publications)"


def abc_links(result, *, limit: int = 10, max_paths: int = 3) -> dict:
    """PubTator3 A→B→C paths without any A–C relation (`pubtator.open_links`)."""
    det = _det("abc_links")
    links = result.get("links") if isinstance(result, dict) else None
    for link in (links if isinstance(links, list) else [])[:limit]:
        a, c, via = link.get("a"), link.get("c"), link.get("via") or []
        if not (_str(a) and _str(c)) or not via:
            continue
        evidence = []
        for v in via[:max_paths]:
            b = v["b"]
            ab = _relation(a, v["ab_type"], b, v["ab_publications"])
            bc = _relation(b, v["bc_type"], c, v["bc_publications"])
            _add_text(det, f"pubtator:{a}|{b}", ab)
            _add_text(det, f"pubtator:{b}|{c}", bc)
            evidence += [{"kind": "provider", "ref": f"pubtator:{a}|{b}", "source": "pubtator",
                          "quote": ab},
                         {"kind": "provider", "ref": f"pubtator:{b}|{c}", "source": "pubtator",
                          "quote": bc}]
        bs = ", ".join(v["b"] for v in via[:max_paths])
        _gap(det, f"No publication relates {a} and {c} directly, although both are related "
                  f"to {bs}.", evidence,
             "An A-B-C path without a direct A-C link is a candidate undiscovered "
             "connection (PubTator3 extracts no disease-disease relations: such links are "
             "open by construction).")
    return det


def unpublished_trials(rows) -> dict:
    """Completed ClinicalTrials.gov trials without a linked publication."""
    det = _det("unpublished_trials")
    for r in rows or []:
        nct, title = _str(r.get("nct_id")), _str(r.get("title"))
        if not nct or not title:
            continue
        _add_text(det, nct, title)
        months = r.get("months_since_completion")
        _gap(det, f"Completed trial {nct} (\"{title}\", completed {r.get('completion_date')}) "
                  f"has no linked publication {months} months after completion.",
             [{"kind": "trial", "ref": nct, "source": "ctgov", "quote": title}],
             "A completed but unpublished trial is a reporting gap; its question may still be "
             "open in the literature.")
    return det


def bridges(result, *, max_overlap: float = 0.2, limit: int = 5) -> dict:
    """OpenAlex co-citation bridges between two literatures with little overlap."""
    det = _det("bridges")
    if not isinstance(result, dict):
        return det
    found, overlap = result.get("bridges") or [], result.get("overlap")
    a, b = _str(result.get("a")), _str(result.get("b"))
    if not found or not a or not b or not isinstance(overlap, (int, float)) \
            or overlap > max_overlap:
        return det
    evidence = []
    for br in found[:limit]:
        ref = f"openalex:{br['work']}"
        text = (f"{br['work']} is referenced by {br['a_citing']} top-cited works on {a} and "
                f"{br['b_citing']} on {b}")
        _add_text(det, ref, text)
        evidence.append({"kind": "provider", "ref": ref, "source": "openalex", "quote": text})
    _gap(det, f"The literatures on {a} and {b} share foundational references but rarely meet: "
              f"{result.get('n_ab')} works match both (overlap {overlap:.1%} of the smaller).",
         evidence,
         "Shared foundations with little direct overlap mark an under-explored connection "
         "between two fields.")
    return det


def trend_breaks(result) -> dict:
    """OpenAlex publication-count surges and drops (`openalex_trends.trend`)."""
    det = _det("trend_breaks")
    if not isinstance(result, dict) or not _str(result.get("term")):
        return det
    term = result["term"]
    ref = f"openalex:counts:{term}"
    for br in result.get("breaks") or []:
        text = (f"{term}: mean {br['before_mean']} works per year before {br['year']}, "
                f"{br['after_mean']} from {br['year']} ({br['window']}-year windows"
                f"{', share of all works' if br.get('normalised') else ''})")
        _add_text(det, ref, text)
        _gap(det, f"Publication activity on {term} shows a {br['direction']} around "
                  f"{br['year']}.",
             [{"kind": "provider", "ref": ref, "source": "openalex", "quote": text}],
             "A surge marks a fast-moving area whose open questions are recent; a drop marks "
             "an area abandoned before its questions were settled.")
    return det


def own_data(doc) -> dict:
    """Gaps from the existing gap analysis of the user's data and article.

    Quotes are checked against a local corpus the caller builds (manifest,
    processing description, article); nothing of it ever leaves the machine."""
    det = _det("own_data")
    for g in (doc or {}).get("gaps") or []:
        if isinstance(g, dict):
            det["gaps"].append({**copy.deepcopy(g), "detector": "own_data"})
    return det


# --- merge and verification -----------------------------------------------------------------------


def merge(detections: Iterable[Mapping]) -> dict:
    """All detections' gaps renumbered G1..Gn (detection order kept); corpora united."""
    gaps: list[dict] = []
    corpus: dict[str, list[str]] = {}
    for det in detections:
        for g in det.get("gaps") or []:
            gaps.append({**copy.deepcopy(g), "id": f"G{len(gaps) + 1}"})
        for ref, texts in (det.get("corpus") or {}).items():
            have = corpus.setdefault(str(ref).casefold(), [])
            have += [t for t in texts if t not in have]
    return {"gaps": gaps, "corpus": corpus}


def _verified(item, corpus: Mapping[str, list[str]]) -> bool:
    if not isinstance(item, dict):
        return False
    quote, ref = item.get("quote"), item.get("ref")
    if not isinstance(quote, str) or not isinstance(ref, str):
        return False
    q = normalise_quote(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    return any(q in normalise_quote(t) for t in corpus.get(ref.strip().casefold(), ())
               if isinstance(t, str))


def verify(gaps: Iterable[Mapping], corpus: Mapping[str, Iterable[str]]) -> dict:
    """The verification gate: keep only evidence items whose quote is verbatim in
    the corpus under their ref; drop gaps left without one. Pure; the input is
    not changed. Returns {kept, dropped: [{id, detector, reason}], dropped_evidence}."""
    if not isinstance(corpus, Mapping):
        raise TypeError("corpus must map evidence refs to the texts fetched for them")
    index: dict[str, list[str]] = {}
    for k, v in corpus.items():         # keys differing only by case are one ref
        index.setdefault(str(k).strip().casefold(), []).extend(
            t for t in v if isinstance(t, str))
    kept, dropped, n_dropped = [], [], 0
    for g in gaps:
        evidence = g.get("evidence") if isinstance(g, Mapping) else None
        evidence = evidence if isinstance(evidence, list) else []
        good = [copy.deepcopy(e) for e in evidence if _verified(e, index)]
        n_dropped += len(evidence) - len(good)
        if good:
            kept.append({**copy.deepcopy(dict(g)), "evidence": good})
        else:
            dropped.append({"id": g.get("id") if isinstance(g, Mapping) else None,
                            "detector": g.get("detector") if isinstance(g, Mapping) else None,
                            "reason": "no verified quote"})
    return {"kept": kept, "dropped": dropped, "dropped_evidence": n_dropped}
