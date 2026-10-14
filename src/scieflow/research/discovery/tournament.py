"""Gap discovery, M3 — the hypothesis tournament (spec §7 step 7).

Pairwise comparisons, judged by agents, rated with Elo:

- `schedule(hypotheses, judges, seed=…, max_comparisons=…)` — the match list.
  All unordered pairs are shuffled with `random.Random(seed)` and taken
  greedily so every hypothesis plays as evenly as possible, up to
  `max_comparisons` (each pair at most once). Which hypothesis is shown first
  is randomised by the same seed (position bias). Inputs are canonicalised
  (sorted by id / agent), so input order never changes the result.
- Judges: for each match, the least-loaded judge (ties by name) from the best
  tier available — `other-family` (a known model family different from both
  authors' families, and not an author), `unknown-family` (family not
  declared, not an author), `same-family` (not an author), and only as a
  last resort `author`. The tier is recorded on the match.
- `build(hypotheses, judges, verdicts, …)` — Elo over the schedule in match
  order (fixed `k`, `initial`); a verdict is a hypothesis id of the match or
  `"tie"`; a missing or invalid verdict changes nothing and is counted.
  Ranking: Elo (rounded to 2 decimals), then debate status
  (consensus > unchallenged = undebated > disputed > challenged), then id.
  `top` is the first `top_k` ids — the only ones that go on to the scite
  check.

Pure and deterministic; dispatching judges is the protocol's job.
"""

from __future__ import annotations

import itertools
import json
import random
import re
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = (Path(__file__).resolve().parents[1] / "schemas"
               / "hypothesis-tournament.schema.json")
DEFAULT_K = 32.0
DEFAULT_INITIAL = 1200.0
DEBATE_RANK = {"consensus": 0, "unchallenged": 1, "undebated": 1, "disputed": 2,
               "challenged": 3}
TIERS = ("other-family", "unknown-family", "same-family", "author")


class TournamentError(ValueError):
    """The tournament cannot be set up as asked."""


def _natural(hid: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", hid)]


def _check(hypotheses: Iterable[Mapping]) -> list[dict]:
    out, seen = [], set()
    for h in hypotheses:
        hid = h.get("id")
        if not isinstance(hid, str) or not hid:
            raise TournamentError("every hypothesis needs an id")
        if hid in seen:
            raise TournamentError(f"duplicate hypothesis id {hid}")
        seen.add(hid)
        status = h.get("debate_status", "undebated")
        if status == "withdrawn":
            raise TournamentError(f"{hid} was withdrawn in the debate; leave it out")
        if status not in DEBATE_RANK:
            raise TournamentError(f"{hid}: unknown debate status {status!r} "
                                  f"({', '.join(DEBATE_RANK)})")
        out.append({"id": hid, "author": h.get("author"), "family": h.get("family"),
                    "debate_status": status})
    return sorted(out, key=lambda h: _natural(h["id"]))


def _judges(judges: Iterable[Mapping]) -> list[dict]:
    out = [{"agent": j["agent"], "family": j.get("family") or None} for j in judges
           if isinstance(j, Mapping) and isinstance(j.get("agent"), str) and j["agent"]]
    if not out:
        raise TournamentError("no judges: assign at least one agent to judge the tournament")
    return sorted(out, key=lambda j: j["agent"])


def _tier(judge: dict, a: dict, b: dict) -> int:
    if judge["agent"] in (a["author"], b["author"]):
        return 3
    if judge["family"] is None:
        return 1
    fams = {a["family"], b["family"]}
    if None not in fams and judge["family"] not in fams:
        return 0
    if None in fams and judge["family"] not in fams:
        return 1                      # an author's family is unknown: cannot confirm
    return 2


def schedule(hypotheses: Iterable[Mapping], judges: Iterable[Mapping], *, seed: int,
             max_comparisons: int) -> list[dict]:
    hs = _check(hypotheses)
    js = _judges(judges)
    by_id = {h["id"]: h for h in hs}
    rng = random.Random(seed)
    pairs = [(a["id"], b["id"]) for a, b in itertools.combinations(hs, 2)]
    rng.shuffle(pairs)
    order = {p: i for i, p in enumerate(pairs)}
    played = {h["id"]: 0 for h in hs}
    load = {j["agent"]: 0 for j in js}
    remaining = list(pairs)
    matches: list[dict] = []
    while remaining and len(matches) < max(0, int(max_comparisons)):
        pair = min(remaining, key=lambda p: (max(played[p[0]], played[p[1]]),
                                             played[p[0]] + played[p[1]], order[p]))
        remaining.remove(pair)
        x, y = pair
        if rng.random() < 0.5:
            x, y = y, x
        a, b = by_id[x], by_id[y]
        best = min(js, key=lambda j: (_tier(j, a, b), load[j["agent"]], j["agent"]))
        load[best["agent"]] += 1
        played[x] += 1
        played[y] += 1
        matches.append({"match": len(matches) + 1, "a": x, "b": y, "judge": best["agent"],
                        "judge_family": best["family"], "judge_tier": TIERS[_tier(best, a, b)]})
    return matches


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def update(ra: float, rb: float, score_a: float, *, k: float = DEFAULT_K) -> tuple[float, float]:
    """New ratings after one game; `score_a` is 1 (a wins), 0.5 (tie) or 0."""
    ea = expected(ra, rb)
    delta = k * (score_a - ea)
    return ra + delta, rb - delta


def build(hypotheses: Iterable[Mapping], judges: Iterable[Mapping],
          verdicts: Mapping[int, str | None], *, seed: int, max_comparisons: int, top_k: int,
          k: float = DEFAULT_K, initial: float = DEFAULT_INITIAL) -> dict:
    hypotheses = list(hypotheses)
    hs = _check(hypotheses)
    matches = schedule(hypotheses, judges, seed=seed, max_comparisons=max_comparisons)
    rating = {h["id"]: float(initial) for h in hs}
    stats = {h["id"]: {"wins": 0, "losses": 0, "ties": 0, "comparisons": 0} for h in hs}
    out_matches, no_verdict = [], 0
    for m in matches:
        v = verdicts.get(m["match"]) if isinstance(verdicts, Mapping) else None
        a, b = m["a"], m["b"]
        if v is None:
            result = "no-verdict"
        elif v not in (a, b, "tie"):
            result = "invalid"
        else:
            result = "tie" if v == "tie" else ("a" if v == a else "b")
        if result in ("no-verdict", "invalid"):
            no_verdict += 1
            out_matches.append({**m, "result": result})
            continue
        score = {"a": 1.0, "b": 0.0, "tie": 0.5}[result]
        rating[a], rating[b] = update(rating[a], rating[b], score, k=k)
        for hid in (a, b):
            stats[hid]["comparisons"] += 1
        if result == "tie":
            stats[a]["ties"] += 1
            stats[b]["ties"] += 1
        else:
            win, lose = (a, b) if result == "a" else (b, a)
            stats[win]["wins"] += 1
            stats[lose]["losses"] += 1
        out_matches.append({**m, "result": result, "winner": v})
    ranked = sorted(hs, key=lambda h: (-round(rating[h["id"]], 2),
                                       DEBATE_RANK[h["debate_status"]], _natural(h["id"])))
    rank = {h["id"]: i + 1 for i, h in enumerate(ranked)}
    rows = [{**h, "elo": round(rating[h["id"]], 2), "rank": rank[h["id"]], **stats[h["id"]]}
            for h in hs]
    return {"version": 1, "seed": seed, "k": k, "initial": initial,
            "max_comparisons": max_comparisons, "top_k": top_k,
            "hypotheses": sorted(rows, key=lambda r: r["rank"]), "matches": out_matches,
            "no_verdict": no_verdict, "top": [h["id"] for h in ranked[:max(0, int(top_k))]]}


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate(doc: dict) -> None:
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        raise TournamentError("; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5]))
