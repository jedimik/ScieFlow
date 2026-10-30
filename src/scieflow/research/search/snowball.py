"""`scieflow research snowball`: the papers that cite (or are cited by) seed DOIs, one or two hops.

usage: scieflow research snowball --seed DOI [--seed DOI ...] [--direction forward|backward]
       [--depth 1|2] [--limit N] [--sources openalex,semantic-scholar,opencitations]
       [--workspace SLUG] [--network MODE]

M-C2 t6 (plan 5.6). The traversal re-implements the design of researcher's graph.py (MIT, credited in
CREDITS.md) on ScieFlow's Gateway: every request goes through the provider policy, the data check,
the cache, the rate limit and the ledger. Sources are asked in the canonical order (openalex,
semantic-scholar, opencitations); per level, per source, per frontier node. A node is marked visited
before it can enter the next frontier, and the seeds are visited from the start, so a cycle ends by
construction and each node is expanded at most once per source. Seeds are never reported as
discoveries. Each source is isolated: a failure, a switched-off or refused source ends that source
only. The output is sanitised at print; no request, record or message carries an address.

Exit codes follow the fan-out (search/fanout.py exit_code): 0 when a source answered; 6 when every
source is a snapshot miss; 3 when a source is refused or switched off and none answered; otherwise
1. A usage or bounds error (more than 10 seeds, a non-DOI seed, depth outside 1..2, limit outside
1..100, an unknown source) exits 1 before any request is sent.
"""

from __future__ import annotations

import argparse
import json
import operator
import re
import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from scieflow._vendor.researcher_core.dedupe import dedupe, identifier_key, identity_key
from scieflow._vendor.researcher_core.model import is_valid_doi, normalize_doi
from scieflow.core import network, sanitize
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.lib import http, kernel, records
from scieflow.research.lib.identity import _doi_key
from scieflow.research.search import fanout, registry

MAX_SEEDS = 10
MAX_DEPTH = 2
MAX_LIMIT = 100
DEFAULT_LIMIT = 25
DIRECTIONS = ("forward", "backward")
#: canonical source order (M-C2-D11)
ORDER = ("openalex", "semantic-scholar", "opencitations")
#: source name -> provider name (catalogue entries, M-C2-D3)
PROVIDER = {"openalex": "search-openalex", "semantic-scholar": "search-semantic-scholar",
            "opencitations": "opencitations"}
OPENALEX_API = "https://api.openalex.org"
S2_API = "https://api.semanticscholar.org/graph/v1"
OPENCITATIONS_API = "https://api.opencitations.net/index/v1"
ABSENT = (404, 410)          # OpenAlex and OpenCitations: "no such record", a clean negative
S2_ABSENT = (404,)           # Semantic Scholar: "no such paper", a clean negative
S2_FIELDS = ("paperId", "externalIds", "title", "year", "venue", "publicationVenue", "journal",
             "citationCount")
OA_BATCH = 50                # OpenAlex filter batch for the references of a work (researcher)
_W_ID = re.compile(r"^W[0-9]{1,15}$")


def _refuse(message: str) -> None:
    print(f"refused: {message}", file=sys.stderr)
    sys.exit(1)


def _expect_object(body) -> None:
    if not isinstance(body, Mapping):
        raise http.HTTPFailure("response has an unexpected shape", "MALFORMED")


def _expect_list(body) -> None:
    if not isinstance(body, list):
        raise http.HTTPFailure("response has an unexpected shape", "MALFORMED")


def _count_results(body) -> int:
    return len(body.get("results") or []) if isinstance(body, Mapping) else 0


def _count_list(body) -> int:
    return len(body) if isinstance(body, list) else 0


def _count_data(body) -> int:
    return len(body.get("data") or []) if isinstance(body, Mapping) else 0


def _work_id(value) -> str | None:
    text = str(value or "").rsplit("/", 1)[-1]
    return text if _W_ID.match(text) else None


# --- one source's neighbours of one node ---------------------------------------------------------


def _openalex_neighbours(gw, doi: str, direction: str, limit: int) -> list:
    """The works that cite `doi` (forward), or that `doi` cites (backward). The DOI is resolved to
    its OpenAlex work first (`works/doi:`, absent for an unknown DOI); the references of that work
    come from its `referenced_works`, hydrated in batches (researcher's request shapes)."""
    work = gw.get_json(PROVIDER["openalex"], "works-doi", f"{OPENALEX_API}/works/doi:{doi}",
                       absent=ABSENT, fixed={"select": records.OPENALEX_SELECT},
                       validate=_expect_object)
    if work is None:
        return []
    if direction == "forward":
        wid = _work_id(work.get("id"))
        if wid is None:
            raise http.HTTPFailure("OpenAlex work has no usable id", "MALFORMED")
        body = gw.get_json(PROVIDER["openalex"], "works-citing", f"{OPENALEX_API}/works",
                           ids={"filter": f"cites:{wid}"},
                           fixed={"per-page": limit, "select": records.OPENALEX_SELECT},
                           count=_count_results, validate=_expect_object)
        items = body.get("results") or []
        return [records.openalex_record(i) for i in items[:limit] if isinstance(i, Mapping)]
    referenced = work.get("referenced_works")
    wids = [w for w in (_work_id(r) for r in (referenced or [])[:limit]) if w]
    out: list = []
    for start in range(0, len(wids), OA_BATCH):
        batch = wids[start:start + OA_BATCH]
        body = gw.get_json(PROVIDER["openalex"], "works-batch", f"{OPENALEX_API}/works",
                           ids={"filter": "openalex:" + "|".join(batch)},
                           fixed={"per-page": len(batch), "select": records.OPENALEX_SELECT},
                           count=_count_results, validate=_expect_object)
        items = body.get("results") or []
        out += [records.openalex_record(i) for i in items if isinstance(i, Mapping)]
    return out


def _s2_neighbours(gw, doi: str, direction: str, limit: int) -> list:
    """Semantic Scholar's citing (forward) or cited (backward) papers of `doi`, keyless."""
    edge, item_key = ("citations", "citingPaper") if direction == "forward" \
        else ("references", "citedPaper")
    body = gw.get_json(PROVIDER["semantic-scholar"], f"paper-{edge}",
                       f"{S2_API}/paper/DOI:{doi}/{edge}", absent=S2_ABSENT,
                       fixed={"limit": limit, "fields": ",".join(S2_FIELDS)},
                       count=_count_data, validate=_expect_object)
    if body is None:
        return []
    out = []
    for item in body.get("data") or []:
        paper = item.get(item_key) if isinstance(item, Mapping) else None
        if not isinstance(paper, Mapping):
            continue                       # an edge whose far side S2 cannot resolve (M-C2 5.6)
        external = paper.get("externalIds")
        external = external if isinstance(external, Mapping) else {}
        if paper.get("paperId") or external.get("DOI"):
            out.append(records.semantic_scholar_record(paper))
            if len(out) >= limit:          # the bound holds whatever the answer carries (5.6)
                break
    return out


def _oc_neighbours(gw, doi: str, direction: str, limit: int) -> list:
    """OpenCitations' citing (forward) or cited (backward) DOIs of `doi`; DOI-only records."""
    op, field = ("citations", "citing") if direction == "forward" else ("references", "cited")
    body = gw.get_json(PROVIDER["opencitations"], op,
                       f"{OPENCITATIONS_API}/{op}/{quote(doi, safe='/')}", absent=ABSENT,
                       validate=_expect_list, count=_count_list)
    if body is None:
        return []
    seen: set = set()
    out: list = []
    for edge in body:
        if not isinstance(edge, Mapping):
            continue
        for other in str(edge.get(field) or "").split():
            other = normalize_doi(other)
            if not is_valid_doi(other) or other in seen:
                continue
            seen.add(other)
            out.append(records.opencitations_record(other))
            if len(out) >= limit:
                return out
    return out


FETCH = {"openalex": _openalex_neighbours, "semantic-scholar": _s2_neighbours,
         "opencitations": _oc_neighbours}


def _state(exc: BaseException) -> str:
    """The fan-out's status of one failed request (NetworkMiss first: it subclasses PolicyError)."""
    if isinstance(exc, network.NetworkMiss):
        return "offline-miss"
    if isinstance(exc, PolicyError):
        return "refused"
    return "failed"


# --- the traversal -----------------------------------------------------------------------------


@dataclass
class _Node:
    key: str
    doi: str


def _source_status(name: str, state: dict) -> dict:
    """The worst state any of the source's expansions reached: one answered expansion never hides
    a refused, failed or missed one (M-C2 t6 review F1; plan 5.9 on replay holes)."""
    if state["off"]:
        status = "off"
    elif "refused" in state["seen"]:
        status = "refused"
    elif "failed" in state["seen"]:
        status = "failed"
    elif "offline-miss" in state["seen"]:
        status = "offline-miss"
    elif state["ok"]:
        status = "ok"
    else:
        status = "offline-miss"
    return {"source": name, "status": status, "count": state["count"]}


def run_snowball(gw, seeds: list, *, direction: str = "forward", depth: int = 1,
                 limit: int = DEFAULT_LIMIT, sources=None) -> dict:
    """The snowball payload for normalised seed DOIs. `sources` lists any subset of ORDER; `None`
    asks all three (a switched-off source reports `off` and sends nothing)."""
    chosen = [name for name in ORDER if sources is None or name in sources]
    state = {name: {"ok": False, "off": False, "count": 0, "seen": set()} for name in chosen}
    seed_keys: dict = {}
    frontier: list = []
    for doi in seeds:
        key = identifier_key(doi)
        if key in seed_keys:
            continue
        seed_keys[key] = doi
        frontier.append(_Node(key, doi))
    visited = set(seed_keys)
    edges: dict = {}                       # (citing key, cited key) -> {"sources", "depth"}
    reports: list = []                     # (source rank, CSLRecord) for every non-seed neighbour
    retrieved = 0
    for level in range(1, depth + 1):
        if not frontier:
            break
        next_frontier: list = []
        # M-C2 t6 review F2: at most len(seeds) * limit nodes are expanded per source at each level,
        # so the frontier (shared by the sources) is capped at that many nodes.
        frontier_cap = len(seed_keys) * limit
        for name in chosen:
            rank = ORDER.index(name)
            if not gw.policy.allows(PROVIDER[name]):
                state[name]["off"] = True
                continue
            for node in frontier:
                try:
                    neighbours = FETCH[name](gw, node.doi, direction, limit)
                except Exception as exc:          # one request's fault ends that request only
                    state[name]["seen"].add(_state(exc))
                    continue
                state[name]["ok"] = True
                state[name]["count"] += len(neighbours)
                retrieved += len(neighbours)
                for rec in neighbours:
                    key = identity_key(rec)
                    if key == node.key:
                        continue               # a self-citation is a metadata defect (review F3)
                    citing, cited = (key, node.key) if direction == "forward" else (node.key, key)
                    edge = edges.setdefault((citing, cited), {"sources": set(), "depth": level})
                    edge["sources"].add(name)
                    if key in seed_keys:
                        continue               # a seed is never a discovery
                    rec.id = f"{name}:{len(reports)}"
                    reports.append((rank, rec))
                    if key in visited:
                        continue
                    doi = _doi_key(rec.DOI)
                    if doi is None:            # a node without a DOI is listed, not expanded
                        visited.add(key)
                        continue
                    if len(next_frontier) >= frontier_cap:
                        continue               # listed, but not expanded (the cap)
                    visited.add(key)           # before it can enter a frontier (cycles end)
                    next_frontier.append(_Node(key, doi))
        frontier = next_frontier

    reports.sort(key=operator.itemgetter(0))   # canonical source order, stable
    result = dedupe([rec for _rank, rec in reports])
    nodes = []
    for position, merged in enumerate(result.records):
        if identity_key(merged) in seed_keys:
            continue
        nodes.append(kernel.from_csl(merged, [], rank=position))

    key_map = result.key_map
    merged_edges: dict = {}
    for (citing, cited), info in edges.items():
        pair = (key_map.get(citing, citing), key_map.get(cited, cited))
        if pair[0] == pair[1]:
            continue                           # two keys merged into one node: still a self-edge
        current = merged_edges.setdefault(pair, {"sources": set(), "depth": info["depth"]})
        current["sources"] |= info["sources"]
        current["depth"] = min(current["depth"], info["depth"])
    edge_rows = [{"citing": c, "cited": d, "sources": [s for s in ORDER if s in info["sources"]],
                  "depth": info["depth"]} for (c, d), info in merged_edges.items()]
    statuses = [_source_status(name, state[name]) for name in chosen]
    return {
        "kernel": 1,
        "seeds": list(seed_keys.values()),
        "direction": direction,
        "depth": depth,
        "sources": statuses,
        "nodes": nodes,
        "edges": edge_rows,
        "dedup": [decision.to_json_dict() for decision in result.decisions],
        "counts": {"retrieved": retrieved, "nodes": len(nodes), "edges": len(edge_rows)},
    }


def _named_sources(text) -> tuple | None:
    if text is None:
        return None
    names = {part.strip() for part in text.split(",") if part.strip()}
    unknown = sorted(names - set(ORDER))
    if unknown or not names:
        _refuse(f"unknown source(s): {', '.join(unknown) or '(none given)'} "
                f"(one of {', '.join(ORDER)})")
    return tuple(name for name in ORDER if name in names)


def _checked_seeds(values) -> list:
    if not values:
        _refuse("give at least one --seed DOI")
    if len(values) > MAX_SEEDS:
        _refuse(f"at most {MAX_SEEDS} seeds; got {len(values)}")
    seeds: list = []
    for value in values:
        doi = _doi_key(value)
        if doi is None:
            _refuse("a seed is not a plain DOI")   # nothing sent
        if doi not in seeds:
            seeds.append(doi)
    return seeds


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow research snowball")
    ap.add_argument("--seed", action="append", default=[], metavar="DOI",
                    help="A seed DOI (one to ten, repeat the option).")
    ap.add_argument("--direction", choices=DIRECTIONS, default="forward",
                    help="forward: papers that cite the seeds; backward: papers they cite.")
    ap.add_argument("--depth", type=int, default=1, help="Hops: 1 (default) or 2.")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="Neighbours asked of each source per expanded node (1-100, default 25). "
                         "Per source, at most seeds x LIMIT nodes are expanded at depth 2.")
    ap.add_argument("--sources", default=None, metavar="LIST",
                    help="Comma-separated sources, any order (openalex, semantic-scholar, "
                         "opencitations). Default: all three; semantic-scholar reports off "
                         "unless config/integrations.yml switches it on.")
    registry.add_workspace_option(ap)
    registry.add_network_option(ap)
    args = ap.parse_args(argv)
    seeds = _checked_seeds(args.seed)
    if not 1 <= args.depth <= MAX_DEPTH:
        _refuse(f"--depth must be 1 or {MAX_DEPTH}; got {args.depth}")
    if not 1 <= args.limit <= MAX_LIMIT:
        _refuse(f"--limit must be between 1 and {MAX_LIMIT}; got {args.limit}")
    chosen = _named_sources(args.sources)
    payload = registry.call(
        args.workspace,
        lambda gw: run_snowball(gw, seeds, direction=args.direction, depth=args.depth,
                                limit=args.limit, sources=chosen),
        network_mode=args.network,
    )
    sys.stdout.write(json.dumps(sanitize.sanitize_json_strings(payload), indent=2,
                                ensure_ascii=False) + "\n")
    code = fanout.exit_code(payload["sources"])
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
