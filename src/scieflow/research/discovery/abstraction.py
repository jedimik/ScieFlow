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
  `quota`, a policy refusal `policy` (a boundary, never retried around), any
  other failure `unavailable`. Only the first `top_k` (default
  `DEFAULT_TOP_K` = 8) ids are considered; a top item whose abstract fails
  `check` or has no key terms is marked `refused` and the rest go ahead.

Fetched content is data, not instructions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

from scieflow.core.review.claims import (LeakError, check_text, key_terms, leaks, safe_doi,
                                         sanitise)

MAX_SCITE_CALLS = 2
DEFAULT_TOP_K = 8                      # spec §7: tournament_top_k
SEARCH_LIMIT = 20
MAX_SEEDS = 10
GRAPH_MAX_EDGES = 100
MAX_QUERY_TERMS = 20
PRECHECK_TERMS = 6
PRECHECK_HITS = 5
MAX_COUNTER = 5
OPENALEX = "https://api.openalex.org/works"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

_NUMBER_WORDS = (r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                 r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
                 r"forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|"
                 r"billions?|dozens?|half(?!-life)|halves|thirds?|quarters?|fifths?|sixths?|"
                 r"sevenths?|eighths?|ninths?|tenths?|hundredths?|thousandths?")
_GROUP_NOUNS = (r"cohorts?|samples?|patients|participants|subjects|individuals|people|"
                r"population|registry|dataset|data|database|series|cases|controls|clinic|"
                r"centre|center|site|study\s+population|biobank")
_PEOPLE = (r"patients|participants|subjects|individuals|adults|people|cases|controls|children|"
           r"men|women|carriers|volunteers|veterans|residents")
_SITE_NOUNS = (r"cohorts?|samples?|registry|registries|population|biobank|dataset|database|"
               r"series|clinic|hospital|centre|center|site|study|trial|institute|university|"
               r"network|consortium|program|programme|initiative")

# Study-design adjectives are not names: "Prospective cohort studies" stays.
_DESIGN = (r"(?:Prospective|Retrospective|Longitudinal|Population|Population-based|Community|"
           r"Community-based|Birth|Clinical|Large|Larger|Small|Independent|Multicentre|"
           r"Multicenter|Multi-centre|Multi-center|National|International|General|Healthy|"
           r"Older|Elderly|Validation|Replication|Discovery|Prodromal|Incident|Prevalent|"
           r"Case-control|Nested|Representative|Different|Other|Further|Future|Such|A|An|The|"
           r"This|These|Each|Every|Any|Some|Most|Many|Few|Both|Randomi[sz]ed|Controlled|"
           r"Observational|Interventional|Pilot|Cross-sectional|Imaging|Genetic|Sleep|REM)")
# Eponyms and scale names that are science, not places or sites.
_EPONYMS = (r"(?:Parkinson|Parkinsonian|Alzheimer|Lewy|Huntington|Creutzfeldt|Jakob|Guillain|"
            r"Barr[eé]|Wilson|Cushing|Addison|Crohn|Hodgkin|Down|Tourette|Willis|Ekbom|"
            r"Meynert|Braak|Hoehn|Yahr|Epworth|Pittsburgh|Montreal|Unified|Movement|Mini|"
            r"Kaplan|Meier|Cox|Bayesian|Gaussian|Pearson|Spearman|Mendelian|Toll|Golgi|"
            r"Schwann|Purkinje|Ranvier|Broca|Wernicke|Korsakoff|Pick|Binswanger|Gerstmann|"
            r"Shy|Drager|Steele|Richardson|Olszewski|Lambert|Eaton|Charcot|Marie|Tooth|"
            r"Friedreich|Kufs|Niemann|Gaucher|Fabry|Tay|Sachs|Machado|Joseph|Bell)")
_SCI_NEXT = (r"(?:disease|diseases|disorder|syndrome|body|bodies|type|scale|scales|score|"
             r"scores|assessment|test|criteria|index|questionnaire|inventory|stage|stages|"
             r"rating|cognitive|sleepiness|sleep|cell|cells|receptor|receptors|nucleus|"
             r"apparatus|area|model|models|regression|correlation|randomi[sz]ation|"
             r"dementia|palsy|atrophy|ataxia|neuropathy|'s)")
_NATIONALITY = (r"(?:Czech|Czechs|Slovak|German|Austrian|Polish|Hungarian|French|Italian|"
                r"Spanish|Portuguese|British|English|Irish|Scottish|Welsh|Dutch|Belgian|"
                r"Swiss|Swedish|Norwegian|Danish|Finnish|Icelandic|Greek|American|Canadian|"
                r"Mexican|Brazilian|Argentinian|Chilean|Chinese|Japanese|Korean|Taiwanese|"
                r"Indian|Pakistani|Australian|Israeli|Turkish|Russian|Ukrainian|Romanian|"
                r"Bulgarian|Serbian|Croatian|Slovenian|European|Asian|African|Hispanic|"
                r"Latino|Latina|Latinx|Caucasian|Indigenous|Aboriginal|Jewish|Ashkenazi|"
                r"Arab|Nordic|Scandinavian|Mediterranean)")
_ROMAN = r"(?=[MDCLXVI]{2,}\b)M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"

_DANGLING = re.compile(r"(?:\s+(?:by|of|in|with|at|to|from|than|for|among|and|or|a|an|the|"
                       r"around|about|approximately|nearly|almost|over|under|above|below))+$",
                       re.I)
_FRAMING = re.compile(r"\b(?:we|i)\s+(?:hypothesi[sz]e|propose|predict|expect|posit|suggest|"
                      r"believe|anticipate)\s*(?:that\s*)?,?", re.I)
# Removed by `abstract`, and anything matching after it is a residual leak.
_COHORT = [
    # in our cohort of 43 patients / among this sample of older adults / in our data
    re.compile(rf"\b(?:(?:in|among|from|for|of|within|across)\s+)?(?:our|my|this|these|the\s+"
               rf"(?:present|current|same|local))\s+(?:\w+\s+)?(?:{_GROUP_NOUNS})\b"
               rf"(?:\s+of\s+(?:[\w.,-]+\s+){{0,2}}(?:{_PEOPLE}))?", re.I),
    # named sites and studies: the Prague cohort, the Mayo Clinic series, the Brno sleep
    # clinic, the ICEBERG study, the VA Palo Alto site
    re.compile(rf"\b(?:(?i:in|among|from|of|at|within)\s+)?(?:(?i:the)\s+)?"
               rf"(?!{_DESIGN}\b|{_EPONYMS}\b)[A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){{0,3}}"
               rf"(?:\s+[a-z][\w-]*){{0,2}}?\s+(?i:{_SITE_NOUNS})\b"),
    # cohort_a, cohort A, group 2, arm B, site 3
    re.compile(r"\b(?:(?:in|among|from|of)\s+)?(?:cohort|group|arm|site|centre|center|wave)"
               r"[_\s-]?[A-Za-z0-9]{1,3}\b(?![\w-]*\s+(?:studies|study|designs?))"
               r"(?<!\bcohorts)", re.I),
    # places: in Prague, living in Texas, residents of Brno (eponyms and scale names stay)
    re.compile(rf"\b(?i:in|at|from|among|across|near|around|living\s+in|resident\s+in|"
               rf"residents\s+of|born\s+in)\s+(?:(?i:the)\s+)?(?!{_EPONYMS}\b|{_DESIGN}\b)"
               rf"[A-Z][a-z\u00c0-\u017f]+(?:\s+[A-Z][a-z\u00c0-\u017f]+)*\b"
               rf"(?!\s+(?i:{_SCI_NEXT})\b)(?!-)"),
    # nationality, ancestry, ethnicity
    re.compile(rf"\b{_NATIONALITY}\b"),
    re.compile(rf"\b(?:White|Black|white|black)\s+(?=(?:{_PEOPLE})\b)"),
    # counts and codes before a people noun: XLIII patients, PD12 participants
    re.compile(rf"\b(?:{_ROMAN}|[\w-]*\d[\w-]*)\s+(?=(?i:{_PEOPLE})\b)"),
]
_COHORT_LABEL_ONLY = re.compile(r"\b(?:cohort|group|arm|site|centre|center|wave)"
                                r"(?:_\w+|[\s-][A-Z0-9]{1,2}\b)")
_FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us|my|mine|me)\b", re.I)
_EFFECT = re.compile(
    r"\b(?:with\s+)?an?\s+(?:very\s+)?(?:large|moderate|medium|small|strong|weak|substantial|"
    r"modest)\s+(?:effect(?:\s+sizes?)?|standardi[sz]ed\s+mean\s+differences?)\b"
    r"|\beffect\s+sizes?\b(?:\s+of)?|\beffect\s+[dg]\b"
    r"|\bcohen(?:'?s)?(?:\s+[dfhw]\b)?|\bhedges(?:'?s?)?(?:\s+g\b)?|\bglass(?:'?s)?\s+delta\b"
    r"|\b(?:standardi[sz]ed\s+)?mean\s+differences?\b|\bSMDs?\b"
    r"|\b(?:odds|hazard|risk|rate|likelihood|incidence[\s-]+rate)[\s-]+ratios?\b(?:\s+of)?"
    r"|\brelative\s+risks?\b(?:\s+of)?|\babsolute\s+risk\s+(?:reduction|increase)\b"
    r"|\bnumber\s+needed\s+to\s+(?:treat|harm)\b"
    r"|(?:\b(?:partial\s+)?(?:eta|omega)|[\u03b7\u03c9])[\s-]*(?:p\s*)?(?:2|squared)\b"
    r"|\b(?:by\s+)?a\s+factor(?:\s+of)?\b"
    r"|\b(?:HR|OR|RR|IRR|ARR|RRR|NNT|NNH|SMD|WMD)s?\b", re.I)
# the abbreviations above are matched case-insensitively only where unambiguous
_EFFECT_ABBR_CI = re.compile(r"\b(?:or|hr|rr)\b")
_FOLD = re.compile(rf"\b(?:(?:{_NUMBER_WORDS}|multi|several)[-\s]?fold|twice|thrice|"
                   rf"(?:{_NUMBER_WORDS})\s+times)\b", re.I)
_MULTIPLY = {"doubles": "increases", "doubled": "increased", "doubling": "increasing",
             "triples": "increases", "tripled": "increased", "tripling": "increasing",
             "quadruples": "increases", "quadrupled": "increased",
             "halves": "reduces", "halved": "reduced", "halving": "reducing"}
_MULTIPLY_RX = re.compile(r"\b(" + "|".join(_MULTIPLY) + r")\b", re.I)
_NUMBER_WORD = re.compile(rf"\b(?:point\s+)?(?:{_NUMBER_WORDS})"
                          rf"(?:(?:[\s-]+(?:and|point|a|of))*[\s-]+(?:{_NUMBER_WORDS}))*\b",
                          re.I)
_LETTERS = re.compile(r"[^\W\d_]+")


def _script(ch: str) -> str:
    import unicodedata
    name = unicodedata.name(ch, "")
    return name.split(" ")[0] if name else "UNKNOWN"


def _scripts(text: str) -> list[str]:
    """`homoglyph` for a word mixing scripts (f\u0456fty), `script` for a word in
    neither Latin nor Greek — both refused: they can smuggle numbers and names
    past the filters."""
    found = []
    for word in _LETTERS.findall(text):
        scripts = {_script(ch) for ch in word}
        if len(scripts) > 1:
            found.append("homoglyph")
        elif not scripts <= {"LATIN", "GREEK"}:
            found.append("script")
    return found


def _effect(text: str) -> bool:
    for m in _EFFECT.finditer(text):
        token = m.group(0)
        # lower-case "or" is a conjunction, not an odds ratio
        if _EFFECT_ABBR_CI.fullmatch(token):
            continue
        return True
    return False


def _strip_effect(text: str) -> str:
    return _EFFECT.sub(lambda m: m.group(0) if _EFFECT_ABBR_CI.fullmatch(m.group(0)) else " ",
                       text)


def _prepare(text) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKC", str(text or ""))
    return t.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")


def residual_leaks(text) -> list[str]:
    """What still looks like data in an abstracted hypothesis (empty: clean)."""
    t = _prepare(text)
    found = list(leaks(t)) + _scripts(t)
    if _FIRST_PERSON.search(t):
        found.append("first-person")
    if any(rx.search(t) for rx in _COHORT[1:]) or _COHORT_LABEL_ONLY.search(t) or \
            re.search(rf"\b(?:this|these|the\s+(?:present|current|same))\s+(?:{_GROUP_NOUNS})\b",
                      t, re.I):
        found.append("cohort")
    if _effect(t):
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
    out = _prepare(text)
    bad = _scripts(out)
    if bad:
        raise LeakError(f"refused: still looks like data ({', '.join(sorted(set(bad)))})")
    out = _FRAMING.sub(" ", out)
    for rx in _COHORT:
        out = rx.sub(" ", out)
    out = _strip_effect(out)
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


def _top_items(items: Iterable[Mapping], top: Iterable[str],
               top_k: int = DEFAULT_TOP_K) -> tuple[list[dict], list[dict]]:
    """The first `top_k` distinct ids of `top`, split into sendable and refused.
    An item is refused (never sent) unless it is an `ok` abstract that passes
    `check` and has key terms; one refusal does not stop the others."""
    by = {it.get("id"): it for it in items}
    ok, refused, seen = [], [], []
    for hid in top:
        if hid not in seen:
            seen.append(hid)
    for hid in seen[:max(0, int(top_k))]:
        it = by.get(hid)
        reason = "not an abstracted hypothesis"
        if it is not None and it.get("status") == "ok":
            try:
                check(it.get("abstract"))
                if key_terms(it["abstract"], limit=12):
                    ok.append({"id": hid, "abstract": it["abstract"]})
                    continue
                reason = "no key terms"
            except LeakError as exc:
                reason = str(exc)
        refused.append({"id": hid, "reason": reason})
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


def scite_plan(items: Iterable[Mapping], top: Iterable[str],
               top_k: int = DEFAULT_TOP_K) -> dict:
    """The exact scite requests for the top-k (shown in the gate before anything is sent)."""
    ok, refused = _top_items(list(items), list(top), top_k)
    if not ok:
        return {"requests": [], "mcp_calls": 0, "refused": [r["id"] for r in refused]}
    reqs = [{"tool": "search_literature",
             "args": {"term": _or_term(ok), "limit": SEARCH_LIMIT}},
            {"tool": "citation_graph",
             "args": {"seeds": f"<= {MAX_SEEDS} DOIs of the search hits matched to a "
                               f"hypothesis, by contrasting share", "direction": "in",
                      "include_snippets": True, "max_edges": GRAPH_MAX_EDGES},
             "conditional": "only if a search hit matches a hypothesis"}]
    return {"requests": reqs, "mcp_calls": len(reqs), "refused": [r["id"] for r in refused]}


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
                mcp: Callable[[str, dict, int], object], round_: int = 1,
                top_k: int = DEFAULT_TOP_K) -> dict:
    from scieflow.integrations.scite.ledger import BudgetError
    from scieflow.integrations.scite.mcp import MCPFailure
    from scieflow.integrations.scite.policy import PolicyError

    items, top = list(items), list(top)
    ok, refused = _top_items(items, top, top_k)
    results: dict[str, dict] = {r["id"]: {"scite_status": "refused", "reason": r["reason"],
                                          "novelty_hits": [], "counter_evidence": []}
                                for r in refused}
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
        out["status"] = ("quota" if stop in ("budget", "quota") else
                         "policy" if stop == "policy" else "unavailable")
        for it in ok:
            results[it["id"]]["scite_status"] = out["status"]
    return out
