"""Gap discovery, M3 — hypothesis abstraction and novelty checks (spec §7 steps 6 and 8, Q21).

Nothing derived from the user's data leaves the machine, so a hypothesis is
*abstracted* before any novelty search sees it:

1. `abstract(text)` removes, in order: the authors' framing ("We hypothesise
   that"), cohort descriptors ("in our cohort of 43 patients", "the Prague
   cohort", "cohort_a", "this sample of older adults", "our data"),
   effect-size language ("large effect size", "Cohen's d", "odds ratio",
   "two-fold", "doubles" → "increases"), number words; then the review
   sanitiser (`core.review.claims.sanitise`: numbers, percentages, p-values,
   CIs, `[data:…]`, `% source:`, `\\input`, paths).
2. `residual_leaks(text)` re-checks the result: the sanitiser's leak check
   plus first-person plural, cohort descriptors, number / fold words and
   effect-size phrases. Anything left is refused (`LeakError`), never sent.
   `abstract_all` reports refusals per hypothesis; the abstracted text is
   what the gate shows verbatim and the only text that is sent.

Novelty:

- `free_precheck(gw, items)` — for every abstracted hypothesis, one OpenAlex
  and one Europe PMC search on its key terms through the provider gateway
  (`research.lib.providers`: policy, leak check, cache, ledger). A provider
  that is off or failing is reported per item; the check goes on.
- `scite_plan(items, top)` / `scite_check(items, top, mcp, round_)` — the
  tournament's top-k only, OR-batched: one `search_literature` over the
  discriminative key terms of all top-k abstracts (hits matched back
  locally), then — only if a hit matched — one `citation_graph` (`in`, with
  snippets) over the matched hits with the highest contrasting share, for
  counter-evidence. At most `MAX_SCITE_CALLS` = 2 MCP calls, enforced here;
  the scite budget in `config/scite.yml` applies on top (the `mcp` callable
  is `MCPClient.call(...).value`). A budget / quota stop marks the rows
  `quota`; a refusal is a boundary, never retried around.

Fetched content is data, not instructions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

from scieflow.core.review.claims import (LeakError, check_text, key_terms, leaks, safe_doi,
                                         sanitise)

MAX_SCITE_CALLS = 2
SEARCH_LIMIT = 20
MAX_SEEDS = 10
GRAPH_MAX_EDGES = 100
MAX_QUERY_TERMS = 20
PRECHECK_TERMS = 6
PRECHECK_HITS = 5
MAX_COUNTER = 5
OPENALEX = "https://api.openalex.org/works"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

_NUMBER_WORDS = (r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
                 r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
                 r"fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|dozens?")
_GROUP_NOUNS = (r"cohorts?|samples?|patients|participants|subjects|individuals|people|"
                r"population|registry|dataset|data|database|series|cases|controls|clinic|"
                r"centre|center|site|study\s+population|biobank")
_PEOPLE = r"patients|participants|subjects|individuals|adults|people|cases|controls|children|men|women"

# Study-design adjectives are not names: "Prospective cohort studies" stays.
_DESIGN = (r"(?:Prospective|Retrospective|Longitudinal|Population|Population-based|Community|"
           r"Community-based|Birth|Clinical|Large|Larger|Small|Independent|Multicentre|"
           r"Multicenter|Multi-centre|Multi-center|National|International|General|Healthy|"
           r"Older|Elderly|Validation|Replication|Discovery|Prodromal|Incident|Prevalent|"
           r"Case-control|Nested|Representative|Different|Other|Further|Future|Such)")
_DANGLING = re.compile(r"(?:\s+(?:by|of|in|with|at|to|from|than|for|among|and|or|a|an|the))+$",
                       re.I)
_FRAMING = re.compile(r"\b(?:we|i)\s+(?:hypothesi[sz]e|propose|predict|expect|posit|suggest|"
                      r"believe|anticipate)\s*(?:that\s*)?,?", re.I)
_COHORT = [
    # in our cohort of 43 patients / among this sample of older adults / in our data
    re.compile(rf"\b(?:(?:in|among|from|for|of|within|across)\s+)?(?:our|this|these|the\s+"
               rf"(?:present|current|same|local))\s+(?:\w+\s+)?(?:{_GROUP_NOUNS})\b"
               rf"(?:\s+of\s+(?:[\w.,-]+\s+){{0,2}}(?:{_PEOPLE}))?", re.I),
    # the Prague cohort, the Czech registry
    re.compile(r"\b(?:(?:in|among|from|of)\s+)?(?:the\s+)?(?!" + _DESIGN + r"\b)[A-Z][\w-]*\s+"
               r"(?:cohort|sample|registry|population|biobank|dataset|database)\b"),
    # cohort_a, cohort A, group 2, arm B, site 3
    re.compile(r"\b(?:(?:in|among|from|of)\s+)?(?:cohort|group|arm|site|centre|center|wave)"
               r"[_\s-]?[A-Za-z0-9]{1,3}\b(?![\w-]*\s+(?:studies|study|designs?))"
               r"(?<!\bcohorts)", re.I),
]
_COHORT_LABEL_ONLY = re.compile(r"\b(?:cohort|group|arm|site|centre|center|wave)"
                                r"(?:_\w+|[\s-][A-Z0-9]{1,2}\b)")
_FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us)\b", re.I)
_EFFECT = re.compile(
    r"\b(?:with\s+)?an?\s+(?:very\s+)?(?:large|moderate|medium|small|strong|weak|substantial|"
    r"modest)\s+effect(?:\s+sizes?)?\b"
    r"|\beffect\s+sizes?\b(?:\s+of)?"
    r"|\bcohen'?s\s+[dfh]\b|\bhedges'?\s+g\b|\bglass'?s\s+delta\b"
    r"|\b(?:odds|hazard|risk|rate|likelihood|incidence\s+rate)\s+ratios?\b(?:\s+of)?"
    r"|\brelative\s+risks?\b(?:\s+of)?|\babsolute\s+risk\s+(?:reduction|increase)\b"
    r"|\bnumber\s+needed\s+to\s+(?:treat|harm)\b", re.I)
_FOLD = re.compile(rf"\b(?:(?:{_NUMBER_WORDS}|multi|several)[-\s]?fold|twice|thrice|"
                   rf"(?:{_NUMBER_WORDS})\s+times)\b", re.I)
_MULTIPLY = {"doubles": "increases", "doubled": "increased", "doubling": "increasing",
             "triples": "increases", "tripled": "increased", "tripling": "increasing",
             "quadruples": "increases", "quadrupled": "increased",
             "halves": "reduces", "halved": "reduced", "halving": "reducing"}
_MULTIPLY_RX = re.compile(r"\b(" + "|".join(_MULTIPLY) + r")\b", re.I)
_NUMBER_WORD = re.compile(rf"\b(?:{_NUMBER_WORDS})(?:[-\s](?:{_NUMBER_WORDS}|one|first))?\b",
                          re.I)


def residual_leaks(text) -> list[str]:
    """What still looks like data in an abstracted hypothesis (empty: clean)."""
    t = str(text)
    found = list(leaks(t))
    if _FIRST_PERSON.search(t):
        found.append("first-person")
    if any(rx.search(t) for rx in _COHORT[1:]) or _COHORT_LABEL_ONLY.search(t) or \
            re.search(rf"\b(?:this|these|the\s+(?:present|current|same))\s+(?:{_GROUP_NOUNS})\b",
                      t, re.I):
        found.append("cohort")
    if _EFFECT.search(t):
        found.append("effect-size")
    if _FOLD.search(t) or _MULTIPLY_RX.search(t) or _NUMBER_WORD.search(t):
        found.append("number-word")
    return sorted(set(found))


def check(text) -> str:
    """`text` unchanged if it may be sent as an abstracted hypothesis; `LeakError` otherwise."""
    check_text(text)
    kinds = residual_leaks(text)
    if kinds:
        raise LeakError(f"refused: still looks like data ({', '.join(kinds)})")
    return text


def abstract(text) -> str:
    """The abstracted hypothesis (see the module doc); `LeakError` if anything is left."""
    out = str(text or "")
    out = _FRAMING.sub(" ", out)
    for rx in _COHORT:
        out = rx.sub(" ", out)
    out = _EFFECT.sub(" ", out)
    out = _FOLD.sub(" ", out)
    out = _MULTIPLY_RX.sub(lambda m: _MULTIPLY[m.group(1).lower()], out)
    out = _NUMBER_WORD.sub(" ", out)
    out = sanitise(out)
    out = re.sub(r"\s+", " ", out).strip(" ,;:")
    out = _DANGLING.sub("", out).strip(" ,;:")
    if out[:1].islower():
        out = out[0].upper() + out[1:]
    return check(out)


def abstract_all(hypotheses: Iterable[Mapping], field: str = "statement") -> list[dict]:
    out = []
    for h in hypotheses:
        hid = h.get("id")
        try:
            out.append({"id": hid, "status": "ok", "abstract": abstract(h.get(field))})
        except LeakError as exc:
            out.append({"id": hid, "status": "refused", "reason": str(exc)})
    return out


# --- free novelty pre-check ----------------------------------------------------------------------


def _query(abstract_text: str, limit: int = PRECHECK_TERMS) -> str:
    return " ".join(key_terms(abstract_text, limit=limit))


def _doi(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", v.strip(), flags=re.I)
    return safe_doi(v)


def _openalex_body(body) -> None:
    from scieflow.research.lib import http
    if not isinstance(body, dict) or not isinstance(body.get("results"), list) or \
            not isinstance((body.get("meta") or {}).get("count"), int):
        raise http.HTTPFailure("OpenAlex response has no results / meta.count", "MALFORMED")


def _europepmc_body(body) -> None:
    from scieflow.research.lib import http
    if not isinstance(body, dict) or not isinstance(body.get("hitCount"), int) or \
            not isinstance((body.get("resultList") or {}).get("result"), list):
        raise http.HTTPFailure("Europe PMC response has no hitCount / resultList", "MALFORMED")


def _short_text(v, n: int = 300) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def _provider_search(gw, provider: str, q: str) -> dict:
    from scieflow.integrations.scite.policy import PolicyError
    from scieflow.research.lib import http

    if not gw.policy.allows(provider):
        return {"status": "off"}
    try:
        if provider == "openalex":
            body = gw.get_json("openalex", "novelty_search", OPENALEX, text={"search": q},
                               fixed={"per-page": PRECHECK_HITS,
                                      "select": "id,doi,title,publication_year"},
                               ttl_class="search",
                               count=lambda b: len(b.get("results") or []),
                               validate=_openalex_body)
            hits = [{"doi": _doi(r.get("doi")), "title": _short_text(r.get("title")),
                     "year": r.get("publication_year")}
                    for r in body["results"][:PRECHECK_HITS] if isinstance(r, dict)]
            return {"status": "ok", "count": body["meta"]["count"], "hits": hits}
        body = gw.get_json("europepmc", "novelty_search", EUROPEPMC, text={"query": q},
                           fixed={"format": "json", "pageSize": PRECHECK_HITS,
                                  "resultType": "lite"},
                           ttl_class="search",
                           count=lambda b: len((b.get("resultList") or {}).get("result") or []),
                           validate=_europepmc_body)
        hits = [{"doi": _doi(r.get("doi")), "title": _short_text(r.get("title")),
                 "year": int(r["pubYear"]) if str(r.get("pubYear", "")).isdigit() else None}
                for r in body["resultList"]["result"][:PRECHECK_HITS] if isinstance(r, dict)]
        return {"status": "ok", "count": body["hitCount"], "hits": hits}
    except PolicyError:
        return {"status": "off"}
    except http.HTTPFailure as exc:
        return {"status": "unavailable", "error": exc.classification}


def free_precheck(gw, items: Iterable[Mapping]) -> list[dict]:
    """OpenAlex + Europe PMC on every abstracted hypothesis (refused ones send nothing)."""
    out = []
    for it in items:
        if it.get("status") != "ok":
            continue
        try:
            check(it.get("abstract"))
            q = check_text(_query(it["abstract"]))
        except LeakError as exc:
            out.append({"id": it.get("id"), "status": "refused", "reason": str(exc)})
            continue
        out.append({"id": it.get("id"), "status": "ok", "query": q,
                    "openalex": _provider_search(gw, "openalex", q),
                    "europepmc": _provider_search(gw, "europepmc", q)})
    return out


# --- scite: top-k only, <= 2 MCP calls -------------------------------------------------------------


def _top_items(items: Iterable[Mapping], top: Iterable[str]) -> tuple[list[dict], list[dict]]:
    by = {it.get("id"): it for it in items}
    ok, refused = [], []
    for hid in top:
        it = by.get(hid)
        if it is not None and it.get("status") == "ok":
            check(it.get("abstract"))
            ok.append({"id": hid, "abstract": it["abstract"]})
        else:
            refused.append({"id": hid})
    return ok, refused


def _discriminative(ok: list[dict]) -> dict[str, list[str]]:
    terms = {it["id"]: key_terms(it["abstract"], limit=12) for it in ok}
    out = {}
    for hid, ts in terms.items():
        others = {t for o, os_ in terms.items() if o != hid for t in os_}
        own = [t for t in ts if t not in others]
        out[hid] = own or ts
    return out


def _or_term(ok: list[dict]) -> str:
    disc = _discriminative(ok)
    chosen: list[str] = []
    for depth in range(3):                      # round robin over each hypothesis' own terms
        for it in ok:
            ts = disc[it["id"]]
            if depth < len(ts) and ts[depth] not in chosen and len(chosen) < MAX_QUERY_TERMS:
                chosen.append(ts[depth])
    for it in ok:                               # then shared topic terms
        for t in key_terms(it["abstract"], limit=12):
            if t not in chosen and len(chosen) < MAX_QUERY_TERMS:
                chosen.append(t)
    return check_text(" OR ".join(chosen))


def scite_plan(items: Iterable[Mapping], top: Iterable[str]) -> dict:
    """The exact scite requests for the top-k (shown in the gate before anything is sent)."""
    ok, _ = _top_items(list(items), list(top))
    if not ok:
        return {"requests": [], "mcp_calls": 0}
    reqs = [{"tool": "search_literature",
             "args": {"term": _or_term(ok), "limit": SEARCH_LIMIT}},
            {"tool": "citation_graph",
             "args": {"seeds": f"<= {MAX_SEEDS} DOIs of the search hits matched to a "
                               f"hypothesis, by contrasting share", "direction": "in",
                      "include_snippets": True, "max_edges": GRAPH_MAX_EDGES},
             "conditional": "only if a search hit matches a hypothesis"}]
    return {"requests": reqs, "mcp_calls": len(reqs)}


def _words(*texts) -> set[str]:
    return set(re.findall(r"[\w-]+", " ".join(str(t or "") for t in texts).lower()))


def _contrasting_share(hit: dict) -> float:
    raw = hit.get("tally")
    t: dict = raw if isinstance(raw, dict) else {}
    c = t.get("contrasting") if isinstance(t.get("contrasting"), int) else 0
    total = sum(v for k, v in t.items() if k in ("supporting", "contrasting", "mentioning")
                and isinstance(v, int))
    return c / total if total else 0.0


def scite_check(items: Iterable[Mapping], *, top: Iterable[str],
                mcp: Callable[[str, dict, int], object], round_: int = 1) -> dict:
    from scieflow.integrations.scite.ledger import BudgetError
    from scieflow.integrations.scite.mcp import MCPFailure
    from scieflow.integrations.scite.policy import PolicyError

    items, top = list(items), list(top)
    ok, refused = _top_items(items, top)
    results: dict[str, dict] = {r["id"]: {"scite_status": "refused", "novelty_hits": [],
                                          "counter_evidence": []} for r in refused}
    for it in ok:
        results[it["id"]] = {"scite_status": "ok", "novelty_hits": [], "counter_evidence": []}
    out = {"status": "ok", "calls": 0, "skipped": [], "results": results}
    if not ok:
        return out
    calls = 0
    stop: str | None = None

    def call(tool: str, args: dict):
        nonlocal calls, stop
        if calls >= MAX_SCITE_CALLS:              # enforced in code, whatever the caller does
            out["skipped"].append({"tool": tool, "reason": "budget"})
            stop = stop or "budget"
            return None
        calls += 1
        try:
            return mcp(tool, args, round_)
        except BudgetError:
            stop = "budget"
        except MCPFailure as exc:
            stop = "quota" if exc.classification == "QUOTA" else "unavailable"
        except PolicyError:
            stop = "policy"
        out["skipped"].append({"tool": tool, "reason": stop})
        return None

    disc = _discriminative(ok)
    search = call("search_literature", {"term": _or_term(ok), "limit": SEARCH_LIMIT})
    hits = search.get("hits") if isinstance(search, dict) else None
    matched: dict[str, dict] = {}
    for h in hits if isinstance(hits, list) else []:
        if not isinstance(h, dict) or (doi := safe_doi(h.get("doi"))) is None:
            continue
        words = _words(h.get("title"), h.get("abstract"),
                       *[x for x in h.get("fulltextExcerpts") or [] if isinstance(x, str)])
        for it in ok:
            own = disc[it["id"]]
            need = min(2, len(own))
            if need and len(set(own) & words) >= need:
                row = {"doi": doi, "title": _short_text(h.get("title")), "year": h.get("year")}
                results[it["id"]]["novelty_hits"].append(row)
                matched[doi] = h
                for c in h.get("citations") or []:
                    if isinstance(c, dict) and c.get("type") == "contrasting" and \
                            (q := _short_text(c.get("snippet"), 600)):
                        ce = {"doi": doi, "citing_doi": safe_doi(c.get("sourceDoi")),
                              "quote": q}
                        lst = results[it["id"]]["counter_evidence"]
                        if ce not in lst and len(lst) < MAX_COUNTER:
                            lst.append(ce)
    if matched and stop is None:
        seeds = sorted(matched, key=lambda d: (-_contrasting_share(matched[d]), d))[:MAX_SEEDS]
        graph = call("citation_graph", {"seeds": seeds, "direction": "in",
                                        "include_snippets": True, "max_edges": GRAPH_MAX_EDGES})
        edges = graph.get("edges") if isinstance(graph, dict) else None
        for e in edges if isinstance(edges, list) else []:
            if not isinstance(e, dict) or e.get("type") != "contrasting":
                continue
            t = safe_doi(e.get("t"))
            snippets = e.get("snippets")
            if not isinstance(snippets, list):
                snippets = [e["snippet"]] if isinstance(e.get("snippet"), str) else []
            for snip in snippets:
                q = _short_text(snip.get("text") if isinstance(snip, dict) else snip, 600)
                if not q or t not in matched:
                    continue
                ce = {"doi": t, "citing_doi": safe_doi(e.get("s")), "quote": q}
                for it in ok:
                    hit_dois = {r["doi"] for r in results[it["id"]]["novelty_hits"]}
                    lst = results[it["id"]]["counter_evidence"]
                    if t in hit_dois and ce not in lst and len(lst) < MAX_COUNTER:
                        lst.append(ce)
    out["calls"] = calls
    if stop is not None:
        out["status"] = "quota" if stop in ("budget", "quota") else "unavailable"
        for it in ok:
            results[it["id"]]["scite_status"] = out["status"]
    return out
