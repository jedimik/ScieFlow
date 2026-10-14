"""Reviewer assignment: who reviews which draft in a review round.

`plan()` is pure and deterministic: identical input gives byte-identical
`dumps()` output. Rules (spec §1, decisions Q2/Q12/Q13/Q22):

- A reviewer never reviews a draft of its own model family unless
  `allow_same_family_review` is on, and never its own draft unless
  `allow_self_review` is on (self pairs are then marked `self: true`). The
  self rule alone governs self pairs: self-review is same-family by nature.
- Pool members default to `covers: auto` when they wrote a draft and to
  `covers: all` when they did not. `covers: all` reviewers review every
  eligible draft *on top of* the balanced ones, so authors in the pool keep
  reviewing each other's drafts when an extra reviewer joins.
- `covers: auto` reviewers are spread over the drafts until each has
  `reviews_per_draft` of them (or every eligible one), keeping reviewer loads
  within one of each other where eligibility allows. Ties prefer a reviewer
  who is not the author, then pool order.
- `matrix_override` (reviewer -> [authors]) replaces the planner; every pair
  in it is still checked against the same rules.
- A draft below `reviews_per_draft` reviews in total (`covers: all`
  reviews count) is reported in `coverage` and `warnings`; a draft with no eligible reviewer at all is an AssignmentError
  naming the switch that would fix it.
- Every pair is a fresh dispatch (never the authoring session).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from scieflow.core import store
from scieflow.core.drafts import DraftError, check_name

VERSION = 1
SAME_FAMILY = "allow_same_family_review"
SELF = "allow_self_review"


class AssignmentError(ValueError):
    """The round cannot be staffed as configured; nothing was written."""


SCHEMA_PATH = (Path(__file__).resolve().parents[2] / "research" / "schemas"
               / "review-assignment.schema.json")


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def _check(name, what: str) -> str:
    """Agent names become file names (`<reviewer>-on-<author>…`): refuse path-shaped ones."""
    if not isinstance(name, str):
        raise AssignmentError(f"{what} {name!r} is not an agent name")
    try:
        check_name(name)
    except DraftError as exc:
        raise AssignmentError(f"{what} {name!r}: {exc}") from exc
    if "\\" in name or any(c.isspace() for c in name):
        # Also a path separator for Windows clients of the web view.
        raise AssignmentError(f"{what} {name!r}: no backslashes or spaces in agent names")
    return name


def _family(families: dict, name: str, what: str) -> str:
    _check(name, what)
    family = families.get(name)
    if not isinstance(family, str) or not family.strip():
        raise AssignmentError(
            f"{what} {name!r} declares no model_family (add model_family: to its entry "
            "in config/agents.yml)")
    return family.strip()


def _blocked_by(reviewer: str, author: str, families: dict, settings: dict) -> str | None:
    """The switch that forbids this pair, or None when it is eligible."""
    if reviewer == author:
        return None if settings.get(SELF) else SELF
    if families[reviewer] == families[author] and not settings.get(SAME_FAMILY):
        return SAME_FAMILY
    return None


def _pair(reviewer: str, author: str, covers: str, families: dict) -> dict:
    return {
        "reviewer": reviewer,
        "author": author,
        "reviewer_family": families[reviewer],
        "author_family": families[author],
        "covers": covers,
        "self": reviewer == author,
        "same_family": families[reviewer] == families[author],
        "fresh_dispatch": True,
    }


def _no_reviewer(author: str, candidates: list[str], families: dict, settings: dict) -> str:
    switches = {s for r in candidates if (s := _blocked_by(r, author, families, settings))}
    if SAME_FAMILY in switches:
        # Self-review alone would leave the draft to its own author: name the
        # switch that brings in another agent.
        switches = {SAME_FAMILY}
    if switches:
        fix = " or ".join(f"turn on {s}" for s in sorted(switches))
        return (f"draft {author!r} has no eligible reviewer: {fix} "
                "(scieflow review configure), or add a reviewer of another model family "
                "to the pool")
    return (f"draft {author!r} has no eligible reviewer: add a reviewer of another model "
            "family to the pool")


def _pool(authors: list[str], pool: list[dict], families: dict) -> list[dict]:
    members, seen = [], set()
    for entry in pool or []:
        name = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise AssignmentError(f"pool entry {entry!r} names no agent")
        if name in seen:
            raise AssignmentError(f"pool lists {name!r} more than once")
        seen.add(name)
        covers = entry.get("covers") or ("auto" if name in authors else "all")
        if covers not in ("auto", "all"):
            raise AssignmentError(f"pool entry {name!r}: covers {covers!r} is not all or auto")
        members.append({"agent": name, "model_family": _family(families, name, "reviewer"),
                        "covers": covers})
    return members


#: Cost of a reviewer's k-th review is (2k-1) * _LOAD: the sum is k², so a
#: minimum-cost assignment minimises the sum of squared loads — the loads are
#: within one of each other whenever eligibility allows it. A self pair costs
#: one unit more, so other reviewers are preferred at equal balance.
_LOAD = 1_000_000


def _min_cost_flow(n: int, edges: list[list], source: int, sink: int) -> None:
    """Successive shortest paths (Bellman-Ford); edges are [to, cap, cost, rev, frm].

    Deterministic: edges are scanned in insertion order and ties keep the
    first path found. Graphs here are tiny (drafts × reviewers).
    """
    graph: list[list[int]] = [[] for _ in range(n)]
    for i, e in enumerate(edges):
        graph[e[4]].append(i)
    while True:
        dist: list[int | None] = [None] * n
        via: list[int] = [-1] * n
        dist[source] = 0
        for _ in range(n):
            changed = False
            for node in range(n):
                here = dist[node]
                if here is None:
                    continue
                for i in graph[node]:
                    to, cap, cost = edges[i][0], edges[i][1], edges[i][2]
                    there = dist[to]
                    if cap > 0 and (there is None or here + cost < there):
                        dist[to], via[to], changed = here + cost, i, True
            if not changed:
                break
        if dist[sink] is None:
            return
        node = sink
        while node != source:
            i = via[node]
            edges[i][1] -= 1
            edges[edges[i][3]][1] += 1
            node = edges[i][4]


def _auto(authors, members, families, settings) -> list[dict]:
    target = settings["reviews_per_draft"]
    auto = [m["agent"] for m in members if m["covers"] == "auto"]
    eligible = {a: [r for r in auto if _blocked_by(r, a, families, settings) is None]
                for a in authors}
    # Nodes: 0 source, 1 sink, then drafts, then reviewers.
    d_node = {a: 2 + i for i, a in enumerate(authors)}
    r_node = {r: 2 + len(authors) + i for i, r in enumerate(auto)}
    edges: list[list] = []

    def add(frm: int, to: int, cost: int) -> int:
        edges.append([to, 1, cost, len(edges) + 1, frm])
        edges.append([frm, 0, -cost, len(edges) - 1, to])
        return len(edges) - 2

    for a in authors:
        for _ in range(min(target, len(eligible[a]))):
            add(0, d_node[a], 0)
    pair_edge = {(r, a): add(d_node[a], r_node[r], 1 if r == a else 0)
                 for a in authors for r in eligible[a]}
    for r in auto:
        for k in range(1, len(authors) + 1):
            add(r_node[r], 1, (2 * k - 1) * _LOAD)
    _min_cost_flow(2 + len(authors) + len(auto), edges, 0, 1)
    return [_pair(r, a, "auto", families) for a in authors for r in eligible[a]
            if edges[pair_edge[(r, a)]][1] == 0]


def _override(authors, override: dict, families, settings) -> tuple[list[dict], list[dict]]:
    reviewers, pairs = [], []
    for reviewer in sorted(override):
        family = _family(families, reviewer, "reviewer")
        reviewers.append({"agent": reviewer, "model_family": family, "covers": "override"})
        targets = override[reviewer] or []
        if len(set(targets)) != len(targets):
            raise AssignmentError(f"matrix_override: {reviewer!r} lists an author twice")
        if not isinstance(targets, list):
            raise AssignmentError(f"matrix_override: {reviewer!r} -> {targets!r} is not a list")
        for author in targets:
            _check(author, "matrix_override author")
            if author not in authors:
                raise AssignmentError(
                    f"matrix_override: {reviewer!r} -> {author!r}: {author!r} is not a draft "
                    f"author of this round ({', '.join(authors)})")
            switch = _blocked_by(reviewer, author, families, settings)
            if switch:
                raise AssignmentError(
                    f"matrix_override: {reviewer!r} may not review {author!r}'s draft "
                    f"({'own draft' if switch == SELF else 'same model family'}); "
                    f"turn on {switch} or change the override")
            pairs.append(_pair(reviewer, author, "override", families))
    return reviewers, pairs


def plan(authors: list[str], pool: list[dict], settings: dict, families: dict) -> dict:
    """Assign reviewers to drafts; see the module docstring for the rules.

    `authors`: draft authors in order; `pool`: [{agent, covers?}];
    `settings`: effective review settings; `families`: agent -> model family.
    """
    authors = list(authors or [])
    if not authors:
        raise AssignmentError("no draft authors: nothing to review")
    if len(set(authors)) != len(authors):
        raise AssignmentError(f"draft authors listed twice: {authors}")
    target = settings.get("reviews_per_draft")
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise AssignmentError(f"reviews_per_draft {target!r} is not an integer >= 1")
    fams = {a: _family(families, a, "draft author") for a in authors}
    override = settings.get("matrix_override") or {}

    if override:
        if not isinstance(override, dict):
            raise AssignmentError(f"matrix_override {override!r} is not a mapping")
        for reviewer in override:
            fams[reviewer] = _family(families, reviewer, "reviewer")
        reviewers, pairs = _override(authors, override, fams, settings)
        candidates = {a: list(override) for a in authors}
        source = "matrix_override"
    else:
        reviewers = _pool(authors, pool, families)
        for m in reviewers:
            fams[m["agent"]] = m["model_family"]
        pairs = _auto(authors, reviewers, fams, settings)
        for m in reviewers:
            if m["covers"] == "all":
                pairs += [_pair(m["agent"], a, "all", fams) for a in authors
                          if _blocked_by(m["agent"], a, fams, settings) is None]
        candidates = {a: [m["agent"] for m in reviewers] for a in authors}
        source = "auto"

    a_index = {a: i for i, a in enumerate(authors)}
    r_index = {r["agent"]: i for i, r in enumerate(reviewers)}
    pairs.sort(key=lambda p: (a_index[p["author"]], r_index[p["reviewer"]]))

    coverage, warnings = {}, []
    for author in authors:
        count = sum(1 for p in pairs if p["author"] == author)
        if count == 0:
            if source == "matrix_override":
                raise AssignmentError(
                    f"matrix_override leaves draft {author!r} without a reviewer")
            raise AssignmentError(_no_reviewer(author, candidates[author], fams, settings))
        shortfall = max(0, target - count)
        coverage[author] = {"reviewers": count, "target": target, "shortfall": shortfall}
        if shortfall:
            warnings.append(f"draft {author!r} gets {count} of {target} reviews "
                            "(not enough eligible reviewers)")
    for r in reviewers:
        r["reviews"] = sum(1 for p in pairs if p["reviewer"] == r["agent"])

    return {
        "version": VERSION,
        "source": source,
        "settings": {k: settings.get(k, False) if k != "reviews_per_draft" else target
                     for k in ("reviews_per_draft", SAME_FAMILY, SELF)},
        "authors": [{"agent": a, "model_family": fams[a]} for a in authors],
        "reviewers": reviewers,
        "pairs": pairs,
        "coverage": coverage,
        "warnings": warnings,
    }


def dumps(data: dict) -> str:
    """Canonical JSON text (sorted keys, fixed indent, trailing newline)."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def path(ws: Path, round_: int) -> Path:
    return Path(ws) / "review" / f"round-{round_}" / "assignment.json"


def _keep_frozen(ws: Path, round_: int, data: dict) -> None:
    """Carry the frozen pass-A hashes (`blind_sha256`) of pairs that stay assigned over
    from the existing assignment, so re-planning a round cannot reopen freezing."""
    try:
        old = json.loads(path(ws, round_).read_text(encoding="utf-8"))
        frozen = {(p["reviewer"], p["author"]): p["blind_sha256"]
                  for p in old.get("pairs", []) if isinstance(p, dict) and "blind_sha256" in p}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return
    pairs = []
    for p in data.get("pairs", []):
        key = (p.get("reviewer"), p.get("author")) if isinstance(p, dict) else None
        if key in frozen and "blind_sha256" not in p:
            p = {**p, "blind_sha256": frozen[key]}
        pairs.append(p)
    if "pairs" in data:
        data["pairs"] = pairs


def write(ws: Path, round_: int, plan_: dict) -> Path:
    """Persist a plan as review/round-N/assignment.json (atomic); returns the path."""
    if isinstance(round_, bool) or not isinstance(round_, int) or round_ < 1:
        raise AssignmentError(f"round {round_!r} is not an integer >= 1")
    data = {**plan_, "round": round_}
    _keep_frozen(ws, round_, data)
    errors = sorted(_validator().iter_errors(data), key=lambda e: e.json_path)
    if errors:
        raise AssignmentError(f"assignment does not match the review-assignment schema: "
                              f"{errors[0].json_path}: {errors[0].message}")
    target = path(ws, round_)
    target.parent.mkdir(parents=True, exist_ok=True)
    store._atomic_write(target, dumps(data))
    return target
