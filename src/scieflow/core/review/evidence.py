"""One evidence bundle per manuscript version: plan, execute, diff against earlier rounds.

`scieflow review evidence <slug> --round N [--plan]` (core only; never a sub-agent).

**Plan** (`plan()`, sends nothing). From the run's drafts and `references.bib`
(`claims.extract_run`), the prompt list (`prompts.load_for_run`, stage
`review`) and the reviewers' follow-up requests in round N's evidence-pass
files, core lists every request that would leave the machine:

| need | source | scite MCP calls |
|---|---|---|
| metadata, retraction / correction | Crossref (`/works/{doi}`), scite `/papers` only as fallback | 0 |
| supporting / contrasting tallies | scite public bulk `POST /tallies` | 0 |
| does the source support the claim | Europe PMC full text / abstract, passages matched locally | 0 |
| contested sources | `citation_graph` `in` + snippets on ≤10 seeds with the highest contrasting share | 1 (only if any) |
| topic search | `search_literature` on `{{topic}}` | 1 |
| novelty claims | `search_literature`, novelty sentences OR-combined | 1 (only if any) |
| reviewer follow-ups | deduplicated across reviewers, capped (`max_followups_per_round`) | ≤2 |

Only DOIs and sanitised, leak-checked key terms leave the machine (the
prompt renderer and `claims.check_text`); follow-up requests written by
reviewers are data and pass the same checks, or are refused. Claim key terms
used to pick passages are matched locally and never sent.

**Round diff.** Results of earlier builds are kept in
`review/round-N/evidence/state.json` (successful answers only). A request whose
key is already there — same DOI, same claim terms, same MCP arguments — is
reused without a call. An unchanged manuscript in round 2 costs nothing.

**Execute** (`execute()`). Needs the run's consent (`review.scite: approved`,
or `ask` plus a human-answered `scite-consent` gate) and runs outside a
dispatch. `scite: off` or an absent `config/scite.yml` → nothing is sent and
no bundle is written (blind reviews still run). MCP calls stop at the first
boundary: a spent budget (`skipped: budget`) or quota (`skipped: quota`) marks
the bundle `scite_status: quota`; a policy refusal / missing token / missing
SDK / auth failure marks it `unavailable`. The round always completes with the
evidence it has. Output: `review/round-N/evidence/bundle.json` (validated) and
`calls.json` (this build's rows of the run's scite ledger plus the free-source
lookups); event `review.evidence_built`.

Fetched text is data, never instructions (rule 8): it is quoted into the
bundle, not obeyed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from scieflow.core import events, store
from scieflow.core.review import assign as assign_mod
from scieflow.core.review import claims as claims_mod
from scieflow.core.review import prompts as prompts_mod
from scieflow.integrations.scite import policy as _policy
from scieflow.integrations.scite.cache import request_key

SCHEMA_PATH = (Path(__file__).resolve().parents[2] / "research" / "schemas"
               / "evidence-bundle.schema.json")
EVIDENCE = "evidence"
CONSENT_KIND = "scite-consent"
CONSENT_WORDS = frozenset({"yes", "y", "approve", "approved", "allow", "consent", "ok"})
MAX_SEEDS = 10                 # citation_graph accepts at most 10 seeds
GRAPH_MAX_EDGES = 100          # snippets are verbose: keep the edge cap modest
FOLLOWUP_LIMIT = 10
MAX_STATEMENTS = 5             # contrasting statements kept per contested source
MAX_FLAG_HITS = 5              # missing_recent / novelty_hit flags per search
_KIND_ORDER = ("contested", "topic", "novelty", "followup")
NOTES = (
    "crossref: if Crossref fails for a DOI, that DOI is sent to scite GET /papers/{doi} instead.",
    "europepmc: the DOI is searched (DOI:\"...\"); for an open-access hit the full text is "
    "fetched by its PMCID. Claim key terms are matched locally, never sent.",
)


class ConsentError(_policy.PolicyError):
    """The run has not consented to sending its query plan. A boundary (exit 3)."""


class QuotaRoundError(_policy.PolicyError):
    """A rebuild of a round whose bundle stopped at the quota or budget would retry
    the refused calls; only `scite backfill` may do that. A boundary (exit 3)."""


def _quota_round(ws: Path, round_: int) -> bool:
    try:
        data = json.loads((_rd(ws, round_) / EVIDENCE / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("scite_status") == "quota"


# --- the plan ---------------------------------------------------------------------------------


@dataclass
class Request:
    id: str
    source: str                 # crossref | scite_public | europepmc | scite_mcp
    op: str                     # work | tallies | fulltext | citation_graph | search_literature
    sends: dict                 # exactly what leaves the machine
    prompts: tuple[str, ...] = ()
    kind: str | None = None     # MCP: contested | topic | novelty | followup
    conditional: str | None = None

    def to_json(self) -> dict:
        out = {"id": self.id, "source": self.source, "op": self.op, "sends": self.sends,
               "prompts": list(self.prompts), "mcp": self.source == "scite_mcp"}
        if self.kind:
            out["kind"] = self.kind
        if self.conditional:
            out["conditional"] = self.conditional
        return out


@dataclass
class Plan:
    slug: str
    round: int
    manuscript_hash: str
    requests: list[Request]
    reused: dict[str, int]                 # source -> lookups served from earlier builds
    refused: list[dict]                    # where + why, never the value
    dropped: list[dict]                    # planned requests left out (cap per kind / follow-ups)
    unsent: list[dict] = field(default_factory=list)   # MCP the policy will not allow now
    # local only (never printed in full, never sent)
    extraction: Any = None
    topic_present: bool = False
    tasks: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    ws: Path | None = None

    def mcp_calls(self) -> int:
        return sum(1 for r in self.requests if r.source == "scite_mcp")

    def to_json(self) -> dict:
        return {
            "version": 1,
            "slug": self.slug,
            "round": self.round,
            "manuscript_hash": self.manuscript_hash,
            "requests": [r.to_json() for r in self.requests],
            "counts": {
                "requests": len(self.requests),
                "mcp": self.mcp_calls(),
                "by_source": {s: sum(1 for r in self.requests if r.source == s)
                              for s in ("crossref", "scite_public", "europepmc", "scite_mcp")},
            },
            "reused": dict(self.reused),
            "refused": list(self.refused),
            "dropped": list(self.dropped),
            "unsent": list(self.unsent),
            "notes": list(NOTES),
        }


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _rd(ws: Path, round_: int) -> Path:
    return Path(ws) / "review" / f"round-{round_}"


def manuscript_hash(ws: Path) -> str:
    """sha256 over every manuscript file the claims are read from, and the bib."""
    h = hashlib.sha256()
    man = Path(ws) / claims_mod.MANUSCRIPT_DIR
    for path, _root in claims_mod.sources(ws):
        try:
            label = path.relative_to(man).as_posix()
        except ValueError:
            label = path.name
        h.update(label.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    bib = man / claims_mod.BIB_NAME
    if bib.is_file():
        h.update(b"references.bib\0" + bib.read_bytes())
    return h.hexdigest()


_TITLE = re.compile(r"\\title\s*(?:\[[^\]]*\]\s*)?\{([^{}]*)\}")
_KEYWORDS = re.compile(r"\\keywords\s*\{([^{}]*)\}|\\begin\s*\{keywords\}(.*?)\\end\s*\{keywords\}",
                       re.S)


def topic(ws: Path) -> str | None:
    """Title + keywords of the manuscript (first draft that has them), raw: the prompt
    renderer sanitises and leak-checks it before it may leave the machine."""
    nb = claims_mod.parser()
    title = keywords = None
    for path, _root in claims_mod.sources(ws):
        body = nb.strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        if title is None and (m := _TITLE.search(body)):
            title = " ".join(m.group(1).split()) or None
        if keywords is None and (m := _KEYWORDS.search(body)):
            keywords = " ".join((m.group(1) or m.group(2) or "").replace(",", " ").split()) or None
    parts = [p for p in (title, keywords) if p]
    return " ".join(parts) if parts else None


def _state_file(ws: Path, round_: int) -> Path:
    return _rd(ws, round_) / EVIDENCE / "state.json"


def load_state(ws: Path, round_: int) -> dict:
    """Results of the latest build at or before `round_` (successful answers only)."""
    for r in range(round_, 0, -1):
        path = _state_file(ws, r)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("results"), dict):
            return dict(data["results"])
    return {}


def _followups(ws: Path, round_: int, refused: list) -> list[dict]:
    """Reviewer follow-up requests of round N, sanitised and de-duplicated (capped by
    the planner, which knows which of them are reused at no cost)."""
    rd = _rd(ws, round_)
    seen: dict[tuple, dict] = {}
    for path in sorted(rd.glob("*.evidence.json")) if rd.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data.get("followup_requests") if isinstance(data, dict) else None
        for i, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            where = {"followup": f"{path.name}#{i + 1}"}
            args: dict = {}
            if isinstance(item.get("query"), str) and item["query"].strip():
                try:
                    term = claims_mod.check_text(claims_mod.sanitise(item["query"]))[:300].strip()
                except claims_mod.LeakError as exc:
                    refused.append({**where, "reason": str(exc)})
                    continue
                if term:
                    args["term"] = term
            dois = []
            for d in item.get("dois") if isinstance(item.get("dois"), list) else []:
                safe = claims_mod.safe_doi(d)
                if safe is None:
                    refused.append({**where, "reason": "not a safe DOI"})
                elif safe not in dois:
                    dois.append(safe)
            if dois:
                args["dois"] = dois[:MAX_SEEDS]
            if not args:
                continue
            args["limit"] = FOLLOWUP_LIMIT
            key = (args.get("term", "").lower(), tuple(sorted(args.get("dois", ()))))
            seen.setdefault(key, args)
    return list(seen.values())


def _sendable(policy: _policy.Policy, tool: str) -> str | None:
    """Why core may not call this MCP tool now (None = it may)."""
    try:
        _policy.check_tool(policy, tool)
    except _policy.PolicyError as exc:
        return str(exc)
    return None


def _mcp_kind(q: prompts_mod.Query) -> str:
    names = {n for n, _ in prompts_mod._wildcards(q.template, "query")}
    if q.tool == "citation_graph" or "seed_dois" in names:
        return "contested"
    if "novelty_claim" in names:
        return "novelty"
    return "topic"


def _seeds(tallies: Mapping[str, dict], dois: list[str], n: int) -> list[str]:
    """≤n cited DOIs with the highest contrasting share (ties: more contrasting, DOI)."""
    scored = []
    for d in dois:
        t = (tallies.get(d) or {}).get("tally") if isinstance(tallies.get(d), dict) else None
        if not isinstance(t, dict) or not t.get("contrasting"):
            continue
        total = t.get("total") or 0
        share = t["contrasting"] / total if total else 1.0
        scored.append((-share, -t["contrasting"], d))
    return [d for *_, d in sorted(scored)[:n]]


def plan(ws: Path, round_: int, policy: _policy.Policy, slug: str | None = None) -> Plan:
    """Every request a build of round N would send. Reads local files only."""
    if not isinstance(round_, int) or isinstance(round_, bool) or round_ < 1:
        raise ValueError(f"round must be an integer >= 1, got {round_!r}")
    ws = Path(ws)
    ex = claims_mod.extract_run(ws)
    ps = prompts_mod.load_for_run(ws)
    state = load_state(ws, round_)
    raw_topic = topic(ws)
    ctx = prompts_mod.Context.from_extraction(ex, run_dir=ws, topic=raw_topic,
                                              seed_dois=list(ex.dois))
    refused: list[dict] = []
    free: dict[str, dict[str, set]] = {"crossref": {}, "scite_public": {}, "europepmc": {}}
    mcp: dict[str, dict] = {}
    dropped: list[dict] = []
    for prompt in ps.select("review"):
        for q in prompt.queries:
            single = replace(prompt, queries=(q,))
            rendered, ref = prompts_mod.render_queries(single, ctx, ps)
            refused += ref
            for rq in rendered:
                if rq.source in free:
                    for doi in rq.query.split():
                        free[rq.source].setdefault(doi, set()).add(prompt.id)
                elif rq.source == "scite_mcp":
                    # one call per kind: the first prompt (in list order) defines it
                    kind = _mcp_kind(q)
                    entry = mcp.get(kind)
                    if entry is None:
                        mcp[kind] = {"kind": kind, "tool": rq.tool, "query": rq.query,
                                     "limit": rq.limit, "prompts": {prompt.id}}
                    elif kind == "contested" or (entry["tool"], entry["query"], entry["limit"]) \
                            == (rq.tool, rq.query, rq.limit):
                        entry["prompts"].add(prompt.id)
                    else:
                        dropped.append({"prompt": prompt.id, "source": "scite_mcp",
                                        "tool": rq.tool,
                                        "reason": f"one scite MCP call per kind ({kind}); "
                                                  f"already planned by "
                                                  f"{', '.join(sorted(entry['prompts']))}"})
                # other sources (pubtator, ctgov, openalex) belong to gap discovery (M3)
    # de-duplicate refusals (one per where+reason)
    refused = [dict(t) for t in dict.fromkeys(tuple(sorted(r.items())) for r in refused)]

    requests: list[Request] = []
    reused = {"crossref": 0, "scite_public": 0, "europepmc": 0, "scite_mcp": 0}
    tasks: dict = {"crossref": [], "tallies": [], "europepmc": [], "mcp": []}
    n = 0

    def add(source, op, sends, prompts, **kw) -> Request:
        nonlocal n
        n += 1
        req = Request(f"Q{n}", source, op, sends, tuple(sorted(prompts)), **kw)
        requests.append(req)
        return req

    for doi in sorted(free["crossref"]):
        if f"crossref:{doi}" in state or f"paper:{doi}" in state:
            reused["crossref"] += 1
            continue
        tasks["crossref"].append(doi)
        add("crossref", "work", {"doi": doi}, free["crossref"][doi])

    tally_new = []
    for doi in sorted(free["scite_public"]):
        if f"tally:{doi}" in state:
            reused["scite_public"] += 1
        else:
            tally_new.append(doi)
    size = policy.rest_max_dois_per_request
    for i in range(0, len(tally_new), size):
        chunk = tally_new[i:i + size]
        tasks["tallies"].append(chunk)
        prompts = set().union(*(free["scite_public"][d] for d in chunk))
        add("scite_public", "tallies", {"dois": chunk}, prompts)

    # passages: one lookup per (DOI, claim terms); one request per DOI is what leaves
    epmc: dict[str, list] = {}
    for c in ex.citing:
        terms = tuple(claims_mod.key_terms(c.text))
        for doi in c.dois:
            if doi not in free["europepmc"]:
                continue
            key = f"epmc:{doi}:{_sha(' '.join(terms))}"
            if key in state:
                reused["europepmc"] += 1
                continue
            epmc.setdefault(doi, []).append((c.id, terms, key))
    for doi in sorted(epmc):
        tasks["europepmc"].append((doi, epmc[doi]))
        add("europepmc", "fulltext", {"doi": doi}, free["europepmc"][doi])

    # MCP, in a fixed order: contested, topic, novelty, follow-ups
    entries = sorted(mcp.values(), key=lambda e: _KIND_ORDER.index(e["kind"]))
    n_seeds = min(policy.contested_seeds, MAX_SEEDS)
    unsent: list[dict] = []
    for e in entries:
        why = _sendable(policy, e["tool"])
        if why is not None:
            unsent.append({"kind": e["kind"], "tool": e["tool"], "reason": "policy",
                           "detail": why})
            continue
        if e["kind"] == "contested":
            candidates = [d for d in ex.dois if d in free["scite_public"]] or list(ex.dois)
            have = {d: v for d in candidates if isinstance(v := state.get(f"tally:{d}"), dict)}
            if candidates and len(have) == len(candidates):
                seeds = _seeds(have, candidates, n_seeds)
                if not seeds:
                    continue          # nothing contested: no call
                args = {"seeds": seeds, "direction": "in", "include_snippets": True,
                        "max_edges": GRAPH_MAX_EDGES}
                key = f"mcp:{request_key('mcp', 'citation_graph', args)}"
                if key in state:
                    reused["scite_mcp"] += 1
                    tasks["mcp"].append({"kind": "contested", "tool": "citation_graph",
                                         "args": args, "key": key, "reuse": True})
                    continue
                tasks["mcp"].append({"kind": "contested", "tool": "citation_graph",
                                     "args": args, "key": key})
                add("scite_mcp", "citation_graph", args, e["prompts"], kind="contested")
            else:
                sends = {"seeds": f"<= {n_seeds} of the DOIs sent to scite_public, chosen "
                                  f"by contrasting share after the tallies",
                         "seed_candidates": candidates, "direction": "in",
                         "include_snippets": True, "max_edges": GRAPH_MAX_EDGES}
                tasks["mcp"].append({"kind": "contested", "tool": "citation_graph",
                                     "args": None, "candidates": candidates, "n": n_seeds})
                add("scite_mcp", "citation_graph", sends, e["prompts"], kind="contested",
                    conditional="only if a cited DOI has contrasting citation statements")
            continue
        args = {"term": e["query"], "limit": e["limit"] or FOLLOWUP_LIMIT}
        key = f"mcp:{request_key('mcp', e['tool'], args)}"
        task = {"kind": e["kind"], "tool": e["tool"], "args": args, "key": key}
        if key in state:
            reused["scite_mcp"] += 1
            tasks["mcp"].append({**task, "reuse": True})
            continue
        tasks["mcp"].append(task)
        add("scite_mcp", e["tool"], args, e["prompts"], kind=e["kind"])

    new_followups = 0
    why = _sendable(policy, "search_literature")
    for args in _followups(ws, round_, refused):
        key = f"mcp:{request_key('mcp', 'search_literature', args)}"
        task = {"kind": "followup", "tool": "search_literature", "args": args, "key": key}
        if key in state:                       # reused: costs nothing, not capped
            reused["scite_mcp"] += 1
            tasks["mcp"].append({**task, "reuse": True})
            continue
        if new_followups >= policy.max_followups_per_round:
            dropped.append({"followup": args, "reason": "over max_followups_per_round"})
            continue
        new_followups += 1
        if why is not None:
            unsent.append({"kind": "followup", "tool": "search_literature", "reason": "policy",
                           "detail": why})
            continue
        tasks["mcp"].append(task)
        add("scite_mcp", "search_literature", args, ("followup",), kind="followup")

    return Plan(slug=slug or ws.name, round=round_, manuscript_hash=manuscript_hash(ws),
                requests=requests, reused=reused, refused=refused, dropped=dropped, unsent=unsent,
                extraction=ex, topic_present=bool(raw_topic), tasks=tasks, state=state, ws=ws)


# --- consent and gating -------------------------------------------------------------------------


def consent(ws: Path, setting: str) -> tuple[bool, str]:
    """(may send, why not). `approved` → yes; `ask` → only after a human answered a
    `scite-consent` gate affirmatively (gate file and its `gate.answered` event);
    `off` → never."""
    if setting == "off":
        return False, "review.scite is off for this run"
    if setting == "approved":
        return True, ""
    from scieflow.core import gates

    # A gate file alone is not proof: the human answer must also be in the run's
    # event log (`gate.answered` by a human, same gate, same answer), which
    # `gates.answer` writes — a hand-written gate file has no such event.
    answered = {(e["data"].get("gate"), str(e["data"].get("answer")))
                for e in events.read(ws, types=("gate.answered",))
                if e.get("actor") == "human" and isinstance(e.get("data"), dict)
                and e["data"].get("kind") == CONSENT_KIND}
    for g in gates.list_gates(ws, state="answered"):
        if g.get("kind") != CONSENT_KIND or g.get("answered_by") != "human":
            continue
        answer = str(g.get("answer") or "")
        if (g.get("id"), answer) in answered and answer.strip().lower() in CONSENT_WORDS:
            return True, ""
    return False, ("review.scite is 'ask' and no human-answered scite-consent gate approves "
                   "it: show the user the plan (--plan), open a scite-consent gate, or let the "
                   "user set review.scite=approved")


# --- sources ------------------------------------------------------------------------------------


@dataclass
class Sources:
    """Transports, injected so tests can count them. Each raises on a failure."""
    crossref: Callable[[str], dict]                       # retractions.check
    paper: Callable[[str, int], dict]                     # PublicClient.paper
    tallies: Callable[[list, int], dict]                  # PublicClient.tallies
    europepmc: Callable[[str, tuple], dict]               # fulltext.evidence
    mcp: Callable[[str, dict, int], Any]                  # MCPClient.call(...).value


def default_sources(policy: _policy.Policy, ledger, cache) -> Sources:
    from scieflow.integrations.scite.mcp import MCPClient
    from scieflow.integrations.scite.public import PublicClient
    from scieflow.research.lib import fulltext, http, retractions

    public = PublicClient(policy, cache, ledger)
    client = MCPClient(policy, cache, ledger)
    cr_lim = http.RateLimiter(retractions.RATE_PER_S)
    ep_lim = http.RateLimiter(fulltext.RATE_PER_S)
    return Sources(
        crossref=lambda doi: retractions.check(doi, cache=cache, limiter=cr_lim),
        paper=lambda doi, r: public.paper(doi, round=r),
        tallies=lambda dois, r: public.tallies(dois, round=r),
        europepmc=lambda doi, terms: fulltext.evidence(doi, terms, cache=cache, limiter=ep_lim),
        mcp=lambda tool, args, r: client.call(tool, args, round=r).value,
    )


# --- execution ----------------------------------------------------------------------------------


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate(bundle: dict) -> None:
    errors = sorted(_validator().iter_errors(bundle), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        raise ValueError(f"evidence bundle invalid at {'/'.join(map(str, e.path)) or '<root>'}: "
                         f"{e.message}")


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _year(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 1000 <= v <= 9999 else None


def _text(v, limit: int = 600) -> str | None:
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    return v[:limit] if v else None


def _valid_doi(v) -> str | None:
    from scieflow.research.lib import doi as _doi

    if not isinstance(v, str):
        return None
    n = _doi.normalise(v)
    return n if n and _doi.is_valid(n) and len(n) <= 300 and "/../" not in f"/{n}/" else None


def _hit_tally(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    counts = [_int(raw.get(k)) for k in ("supporting", "contrasting", "mentioning")]
    if any(v is None for v in counts):
        return None
    s, c, m = (int(v or 0) for v in counts)
    out = {"supporting": s, "contrasting": c, "mentioning": m}
    unclassified = _int(raw.get("unclassified"))
    if unclassified is not None:
        out["unclassified"] = unclassified
    total = _int(raw.get("total"))
    out["total"] = total if total is not None else s + c + m + (unclassified or 0)
    return out


def _hits(value, claim_terms: dict[str, set] | None) -> list[dict]:
    out = []
    hits = value.get("hits") if isinstance(value, dict) else None
    for h in hits if isinstance(hits, list) else []:
        if not isinstance(h, dict):
            continue
        doi = _valid_doi(h.get("doi"))
        if doi is None:
            continue
        r: dict = {"doi": doi}
        if (t := _text(h.get("title"), 300)):
            r["title"] = t
        if (y := _year(h.get("year"))):
            r["year"] = y
        if (tally := _hit_tally(h.get("tally"))):
            r["tally"] = tally
        excerpts = h.get("fulltextExcerpts")
        quote = _text(excerpts[0]) if isinstance(excerpts, list) and excerpts else None
        quote = quote or _text(h.get("abstract"))
        if quote:
            r["quote"] = quote
        if claim_terms:
            hay = " ".join(str(h.get(k) or "") for k in ("title", "abstract")).lower()
            hay += " " + " ".join(str(x) for x in excerpts or [] if isinstance(x, str)).lower()
            words = set(re.findall(r"[\w-]+", hay))
            matched = [cid for cid, terms in claim_terms.items()
                       if terms and len(terms & words) >= min(2, len(terms))]
            if matched:
                r["claims"] = matched
        out.append(r)
    return out


def _contested(value, seeds: list[str]) -> list[dict]:
    edges = value.get("edges") if isinstance(value, dict) else None
    by: dict[str, list] = {d: [] for d in seeds}
    for e in edges if isinstance(edges, list) else []:
        if not isinstance(e, dict) or e.get("type") != "contrasting":
            continue
        t, s = _valid_doi(e.get("t")), _valid_doi(e.get("s"))
        if t not in by or s is None:
            continue
        snippets = e.get("snippets") or ([e["snippet"]] if isinstance(e.get("snippet"), str) else [])
        for snip in snippets if isinstance(snippets, list) else []:
            quote = _text(snip.get("text") if isinstance(snip, dict) else snip)
            st = {"citing_doi": s, "quote": quote, "stance": "contrasting"} if quote else None
            if st and st not in by[t] and len(by[t]) < MAX_STATEMENTS:
                by[t].append(st)
    return [{"doi": d, "statements": st} for d, st in by.items() if st]


class _Build:
    def __init__(self, p: Plan, policy, sources: Sources, ledger):
        self.p, self.policy, self.src, self.ledger = p, policy, sources, ledger
        self.results: dict = dict(p.state)
        self.skipped: list[dict] = []
        self.free = {"crossref": 0, "europepmc": 0}
        self.status = "ok"
        self.stop: str | None = None

    def _skip(self, query: str, reason: str) -> None:
        self.skipped.append({"query": query, "reason": reason})

    def run_free(self) -> None:
        for doi in self.p.tasks["crossref"]:
            self.free["crossref"] += 1
            try:
                res = self.src.crossref(doi)
            except Exception:                                   # noqa: BLE001
                res = {"status": "unavailable"}
            if isinstance(res, dict) and res.get("status") in ("ok", "corrected", "retracted",
                                                               "unresolvable"):
                self.results[f"crossref:{doi}"] = {k: res.get(k) for k in
                                                   ("status", "title", "year", "reason")}
                continue
            # Crossref unavailable: scite /papers as the fallback for this DOI
            try:
                pp = self.src.paper(doi, self.p.round)
            except Exception:                                   # noqa: BLE001
                pp = None
            if isinstance(pp, dict) and pp.get("status") == "ok":
                self.results[f"paper:{doi}"] = {
                    "status": "retracted" if pp.get("retracted") is True else "ok",
                    "title": pp.get("title"), "year": pp.get("year")}
            else:
                self._skip(f"crossref {doi}", "unavailable")
        for chunk in self.p.tasks["tallies"]:
            try:
                got = self.src.tallies(chunk, self.p.round)
            except _policy.PolicyError:
                got = {}
                for d in chunk:
                    self._skip(f"scite_public tallies {d}", "policy")
                continue
            except Exception:                                   # noqa: BLE001
                got = {}
            for d in chunk:
                entry = got.get(d) if isinstance(got, dict) else None
                if isinstance(entry, dict) and entry.get("status") in ("ok", "unknown"):
                    self.results[f"tally:{d}"] = {"status": entry["status"],
                                                  "tally": entry.get("tally")}
                else:
                    self._skip(f"scite_public tallies {d}", "unavailable")
        for doi, wanted in self.p.tasks["europepmc"]:
            for _cid, terms, key in wanted:
                self.free["europepmc"] += 1
                try:
                    ev = self.src.europepmc(doi, terms)
                except Exception:                               # noqa: BLE001
                    ev = None
                if not isinstance(ev, dict) or ev.get("status") not in ("full_text",
                                                                         "abstract_only",
                                                                         "not_found"):
                    self._skip(f"europepmc {doi}", "unavailable")
                    continue
                self.results[key] = {
                    "status": ev["status"], "abstract_only": bool(ev.get("abstract_only")),
                    "passages": [p["quote"] for p in ev.get("passages") or []
                                 if isinstance(p, dict) and _text(p.get("quote"))]}

    def _mcp(self, task: dict) -> None:
        from scieflow.integrations.scite.ledger import BudgetError
        from scieflow.integrations.scite.mcp import MCPFailure

        label = f"{task['tool']}: " + (task["args"].get("term") or
                                       " ".join(task["args"].get("seeds") or
                                                task["args"].get("dois") or []))
        if self.stop is not None:
            self._skip(label, self.stop)
            return
        try:
            value = self.src.mcp(task["tool"], task["args"], self.p.round)
        except BudgetError:
            self.stop = "budget"
        except _policy.PolicyError:
            self._skip(label, "policy")         # this tool only; others may still be allowed
            return
        except MCPFailure as exc:
            if exc.classification == "QUOTA":
                self.stop = "quota"
            elif exc.classification in ("AUTH", "NOT_INSTALLED"):
                self.stop = "unavailable"
            else:
                self._skip(label, "unavailable")
                return
        else:
            self.results[task["key"]] = value
            return
        self._skip(label, self.stop)

    def run_mcp(self) -> None:
        for u in self.p.unsent:
            self._skip(f"{u['tool']}: ({u['kind']})", "policy")
        for task in self.p.tasks["mcp"]:
            if task.get("reuse"):
                continue
            if task["kind"] == "contested" and task["args"] is None:
                tallies = {d: v for d in task["candidates"]
                           if isinstance(v := self.results.get(f"tally:{d}"), dict)}
                seeds = _seeds(tallies, task["candidates"], task["n"])
                if not seeds:
                    continue
                args = {"seeds": seeds, "direction": "in", "include_snippets": True,
                        "max_edges": GRAPH_MAX_EDGES}
                task = {**task, "args": args,
                        "key": f"mcp:{request_key('mcp', 'citation_graph', args)}"}
                if task["key"] in self.results:
                    continue
                self.p.tasks.setdefault("resolved_contested", []).append(task)
            self._mcp(task)
        reasons = {s["reason"] for s in self.skipped
                   if s["query"].split(":")[0] in ("citation_graph", "search_literature")}
        if reasons & {"budget", "quota"}:
            self.status = "quota"
        elif reasons:
            self.status = "unavailable"


def _assemble(b: _Build, generated: str) -> dict:
    p, R = b.p, b.results
    ex = p.extraction
    refs, flags = [], []

    def flag(kind, detail, *, doi=None, key=None, claim=None):
        f = {"id": "", "kind": kind, "blocker": kind in ("retracted", "unresolvable"),
             "detail": detail}
        if doi:
            f["doi"] = doi
        if key:
            f["key"] = key
        if claim:
            f["claim"] = claim
        flags.append(f)

    # bib keys per DOI (first key) for readable flags
    nb = claims_mod.parser()
    bib = p.ws / claims_mod.MANUSCRIPT_DIR / claims_mod.BIB_NAME if p.ws is not None else None
    keys: dict[str, str] = {}
    if bib is not None and bib.is_file():
        for k, raw in nb.parse_bib(bib.read_text(encoding="utf-8", errors="replace")).items():
            d = claims_mod.safe_doi(raw)
            if d and d not in keys and re.fullmatch(r"[A-Za-z0-9_:.+-]{1,128}", k):
                keys[d] = k

    years = []
    for doi in ex.dois:
        cr = R.get(f"crossref:{doi}") or R.get(f"paper:{doi}")
        ta = R.get(f"tally:{doi}")
        ref: dict = {"doi": doi}
        if doi in keys:
            ref["key"] = keys[doi]
        sources = []
        status = "ok"
        if isinstance(cr, dict):
            status = cr.get("status") or "ok"
            sources.append("crossref" if f"crossref:{doi}" in R else "scite_public")
            if (t := _text(cr.get("title"), 300)):
                ref["title"] = t
            if (y := _year(cr.get("year"))):
                ref["year"] = y
                years.append(y)
        ref["status"] = status
        if isinstance(ta, dict) and isinstance(ta.get("tally"), dict):
            ref["tally"] = ta["tally"]
            if "scite_public" not in sources:
                sources.append("scite_public")
        epmc = [v for k, v in R.items() if k.startswith(f"epmc:{doi}:") and isinstance(v, dict)
                and v.get("status") in ("full_text", "abstract_only")]
        if epmc:
            ref["oa"] = any(v["status"] == "full_text" for v in epmc)
            sources.append("europepmc")
        ref["sources"] = sources
        refs.append(ref)
        if status == "retracted":
            flag("retracted", "retraction notice (Crossref update / scite)", doi=doi,
                 key=keys.get(doi))
        elif status == "unresolvable":
            flag("unresolvable", (cr or {}).get("reason") or "DOI does not resolve", doi=doi,
                 key=keys.get(doi))
        elif status == "corrected":
            flag("corrected", "correction or editorial notice (Crossref update)", doi=doi)
    for bad in ex.invalid_dois:
        if re.fullmatch(r"[A-Za-z0-9_:.+-]{1,128}", bad["key"]):
            refs.append({"key": bad["key"], "status": "unresolvable", "sources": []})
            flag("unresolvable", "malformed DOI in references.bib", key=bad["key"])

    for ref in refs:
        t = ref.get("tally")
        if t and t.get("contrasting"):
            flag("contested", f"{t['contrasting']} contrasting of {t['total']} citation statements",
                 doi=ref["doi"])

    claims_out = []
    for c in ex.claims:
        passages = []
        terms = tuple(claims_mod.key_terms(c.text))
        for doi in c.dois:
            v = R.get(f"epmc:{doi}:{_sha(' '.join(terms))}")
            if not isinstance(v, dict):
                continue
            for q in v.get("passages") or []:
                passages.append({"doi": doi, "quote": q, "source": "europepmc",
                                 "abstract_only": bool(v.get("abstract_only"))})
            if v.get("status") == "abstract_only":
                flag("abstract_only", "only the abstract was available (not open access or no "
                     "full text)", doi=doi, claim=c.id)
        claims_out.append({"id": c.id, "section": c.section, "text": c.text,
                           "dois": list(c.dois), "passages": passages})

    contested, searches = [], []
    novelty_terms = {c.id: set(claims_mod.key_terms(c.text)) for c in ex.novelty}
    cited = set(ex.dois)
    latest = max(years) if years else None
    tasks = [t for t in p.tasks["mcp"] if t.get("args")] + p.tasks.get("resolved_contested", [])
    for task in sorted(tasks, key=lambda t: _KIND_ORDER.index(t["kind"])):
        value = R.get(task["key"])
        if value is None:
            continue
        if task["kind"] == "contested":
            for entry in _contested(value, task["args"]["seeds"]):
                if entry not in contested:
                    contested.append(entry)
            continue
        query = task["args"].get("term") or " ".join(task["args"].get("dois") or [])
        results = _hits(value, novelty_terms if task["kind"] == "novelty" else None)
        sid = f"S{len(searches) + 1}"
        if any(s["kind"] == task["kind"] and s["query"] == query for s in searches):
            continue
        searches.append({"id": sid, "kind": task["kind"], "source": "scite_mcp", "query": query,
                         "results": results})
        if task["kind"] == "topic" and latest is not None:
            new = [r for r in results if r["doi"] not in cited and r.get("year", 0) > latest]
            for r in new[:MAX_FLAG_HITS]:
                flag("missing_recent", f"not cited; published {r['year']}, after the newest "
                     f"cited reference ({latest}) — topic search {sid}", doi=r["doi"])
        if task["kind"] == "novelty":
            n = 0
            for r in results:
                for cid in r.get("claims", []):
                    if n < MAX_FLAG_HITS:
                        flag("novelty_hit", f"earlier work may anticipate this novelty claim — "
                             f"novelty search {sid}", doi=r["doi"], claim=cid)
                        n += 1
    order = ("retracted", "unresolvable", "corrected", "contested", "abstract_only",
             "missing_recent", "novelty_hit")
    flags.sort(key=lambda f: order.index(f["kind"]))
    for i, f in enumerate(flags, 1):
        f["id"] = f"F{i}"

    bundle = {"version": 1, "round": p.round, "manuscript_hash": p.manuscript_hash,
              "generated": generated, "scite_status": b.status, "references": refs,
              "claims": claims_out, "contested": contested, "searches": searches,
              "flags": flags}
    return bundle


@dataclass
class Outcome:
    status: str                  # built | off | refused
    scite_status: str
    reason: str | None = None
    bundle: dict | None = None
    calls: dict | None = None
    plan: Plan | None = None
    frozen: dict | None = None   # passes.freeze() report, taken before the bundle is built


def execute(p: Plan, ws: Path, policy: _policy.Policy, *, ledger=None, cache=None,
            sources: Sources | None = None, sources_factory=None,
            now: Callable[[], datetime] | None = None, backfill: bool = False) -> Outcome:
    """Run the plan, write bundle.json + calls.json + state.json, emit review.evidence_built.

    A round that ends `scite_status: quota` tells the user once per run
    (`scite.unavailable`; the MCP client already emits it for a QUOTA error, so
    this covers a spent budget)."""
    from scieflow.integrations.scite.cache import Cache
    from scieflow.integrations.scite.ledger import Ledger, default_dir

    ws = Path(ws)
    ledger = ledger or Ledger(ws, policy)
    cache = cache or Cache(default_dir() / "cache", policy.cache_ttl_days)
    if sources is None:
        sources = (sources_factory or default_sources)(policy, ledger, cache)
    before = len(ledger.rows("run"))
    b = _Build(p, policy, sources, ledger)
    b.run_free()
    b.run_mcp()
    generated = (now or (lambda: datetime.now(timezone.utc)))().isoformat(timespec="seconds")
    bundle = _assemble(b, generated)
    # this build's scite rows (discovery providers share the run ledger)
    rows = [r for r in ledger.rows("run")[before:] if r.get("source") in ("mcp", "rest")]
    summary = {
        "mcp": sum(1 for r in rows if r.get("source") == "mcp" and not r.get("cached")),
        "rest": sum(1 for r in rows if r.get("source") == "rest" and not r.get("cached")),
        "cached": sum(1 for r in rows if r.get("cached")),
        "skipped": b.skipped,
    }
    bundle["calls"] = summary
    validate(bundle)
    out = _rd(ws, p.round) / EVIDENCE
    calls = {"version": 1, "round": p.round, "manuscript_hash": p.manuscript_hash,
             "generated": generated, "summary": summary,
             "free": dict(b.free), "reused": dict(p.reused), "ledger": rows}
    keep = {k: v for k, v in b.results.items() if k in _wanted_keys(p, b.results)}
    store.write_text(out / "state.json", json.dumps(
        {"version": 1, "round": p.round, "manuscript_hash": p.manuscript_hash, "results": keep},
        indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    store.write_text(out / "calls.json", json.dumps(calls, indent=2, ensure_ascii=False) + "\n")
    store.write_text(out / "bundle.json", json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    events.emit(ws, "review.evidence_built", round=p.round, scite_status=b.status,
                mcp=summary["mcp"], rest=summary["rest"], cached=summary["cached"],
                skipped=len(summary["skipped"]), manuscript_hash=p.manuscript_hash,
                **({"backfill": True} if backfill else {}))
    if b.status == "quota" and not events.read(ws, types=("scite.unavailable",)):
        events.emit(ws, "scite.unavailable", reason=b.stop or "budget", round=p.round)
    return Outcome("built", b.status, bundle=bundle, calls=calls, plan=p)


def _wanted_keys(p: Plan, results: dict) -> set:
    """Keys this manuscript version uses (older entries are not carried forward)."""
    ex = p.extraction
    want = set()
    for d in ex.dois:
        want |= {f"crossref:{d}", f"paper:{d}", f"tally:{d}"}
    for c in ex.citing:
        h = _sha(" ".join(claims_mod.key_terms(c.text)))
        want |= {f"epmc:{d}:{h}" for d in c.dois}
    for t in p.tasks["mcp"] + p.tasks.get("resolved_contested", []):
        if t.get("key"):
            want.add(t["key"])
    return want


def build(project, slug: str, round_: int, *, plan_only: bool = False, environ=None,
          policy: _policy.Policy | None = None, **kw) -> Outcome:
    """The `scieflow review evidence` entry point. Refuses inside a dispatch; with
    `plan_only` sends nothing and writes nothing."""
    from scieflow.core.review import settings as settings_mod
    from scieflow.integrations.scite import mcp as smcp

    smcp.refuse_in_dispatch(environ)
    ws = project.run_dir(slug)
    if not ws.is_dir():
        raise ValueError(f"no workspace {slug!r}")
    setting = settings_mod.load(project, slug)["scite"]
    policy = policy or _policy.load(project.root, environ)
    if not policy.enabled:
        return Outcome("off", "off", reason=policy.reason)
    if setting == "off":
        return Outcome("off", "off", reason="review.scite is off for this run")
    p = plan(ws, round_, policy, slug=slug)
    if plan_only:
        return Outcome("planned", "ok", plan=p)
    if not kw.get("backfill") and _quota_round(ws, round_):
        raise QuotaRoundError(
            f"round {round_}'s evidence stopped at the scite quota or budget: rebuilding it "
            "would retry the refused calls. Continue the round with this bundle, or later run "
            f"`scieflow research scite backfill {slug} --round {round_}`")
    ok, why = consent(ws, setting)
    if not ok:
        raise ConsentError(why)
    frozen = None
    if assign_mod.path(ws, round_).is_file():
        # Pass A is frozen before the bundle exists (spec §5, Review Focus #6).
        from scieflow.core.review import passes as passes_mod

        frozen = passes_mod.freeze(ws, round_)
    outcome = execute(p, ws, policy, **kw)
    outcome.frozen = frozen
    return outcome


class BackfillError(ValueError):
    """Nothing to backfill, or the round cannot be backfilled; nothing was sent."""


def backfill(project, slug: str, round_: int, *, environ=None, force: bool = False,
             **kw) -> Outcome:
    """`scieflow research scite backfill <slug> --round N` (spec Q23).

    Re-runs round N's evidence build after a quota or outage: every answer the
    earlier build got is reused from state.json, so only the skipped parts are
    requested, under the same budgets (a spent budget is still a boundary).
    Refused inside a dispatch, for a round without a bundle, for a bundle with
    nothing missing, and when the manuscript changed since (that is a new round).
    A round that already has a matrix (or decisions) is refused unless `force`:
    the new bundle changes the bundle hash, the flags and the scite status the
    matrix and the pass-B reviews were checked against, so the matrix must be
    rebuilt and its decisions made again.
    """
    from scieflow.integrations.scite import mcp as smcp

    smcp.refuse_in_dispatch(environ)
    if isinstance(round_, bool) or not isinstance(round_, int) or round_ < 1:
        raise BackfillError(f"round {round_!r} is not an integer >= 1")
    ws = project.run_dir(slug)
    if not ws.is_dir():
        raise BackfillError(f"no workspace {slug!r}")
    path = _rd(ws, round_) / EVIDENCE / "bundle.json"
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BackfillError(f"round {round_} has no evidence bundle: run `scieflow review "
                            f"evidence {slug} --round {round_}` first") from None
    except (OSError, ValueError) as exc:
        raise BackfillError(f"round {round_}: bundle.json is unreadable: {exc}") from exc
    if not isinstance(bundle, dict):
        raise BackfillError(f"round {round_}: bundle.json is not a JSON object")
    skipped = (bundle.get("calls") or {}).get("skipped") or []
    if bundle.get("scite_status") == "ok" and not skipped:
        raise BackfillError(f"nothing to backfill: round {round_}'s bundle has scite_status ok "
                            "and no skipped request")
    if bundle.get("manuscript_hash") != manuscript_hash(ws):
        raise BackfillError(f"the manuscript changed since round {round_}'s bundle was built: "
                            "build the evidence of a new round instead")
    rd = _rd(ws, round_)
    if not force and ((rd / "matrix.json").exists() or (rd / "decisions.json").exists()):
        raise BackfillError(f"round {round_} already has a matrix: a backfilled bundle "
                            "invalidates it (and any decisions on it); re-run pass B and the "
                            "matrix afterwards, or pass --force to backfill anyway")
    return build(project, slug, round_, environ=environ, backfill=True, **kw)
