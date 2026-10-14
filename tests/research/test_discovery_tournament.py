"""M3 hypothesis tournament (T3.3): a capped, seeded, deterministic Elo
tournament; judges from model families other than both hypothesis authors
where possible; debate status as the tie-break."""

from __future__ import annotations

import itertools
import json

import pytest

from scieflow.research.discovery import tournament as T


def hyps(n=4, authors=None):
    authors = authors or ["claude-a", "codex-b"]
    fams = {"claude-a": "claude", "codex-b": "openai", "gemini-c": "gemini", "glm-d": "glm"}
    return [{"id": f"H{i + 1}", "author": authors[i % len(authors)],
             "family": fams.get(authors[i % len(authors)]),
             "debate_status": "unchallenged", "statement": f"hypothesis {i + 1}"}
            for i in range(n)]


JUDGES = [{"agent": "claude-a", "family": "claude"}, {"agent": "codex-b", "family": "openai"},
          {"agent": "gemini-c", "family": "gemini"}, {"agent": "glm-d", "family": "glm"}]


def first_wins(matches):
    return {m["match"]: m["a"] for m in matches}


def test_schedule_is_deterministic_for_a_seed():
    h = hyps(8)
    s1 = T.schedule(h, JUDGES, seed=7, max_comparisons=28)
    s2 = T.schedule(json.loads(json.dumps(h)), list(JUDGES), seed=7, max_comparisons=28)
    assert s1 == s2
    assert len(s1) == 28                       # all pairs of 8
    pairs = {frozenset((m["a"], m["b"])) for m in s1}
    assert len(pairs) == 28
    assert T.schedule(h, JUDGES, seed=8, max_comparisons=28) != s1


def test_schedule_input_order_does_not_matter():
    h = hyps(6)
    assert T.schedule(h, JUDGES, seed=3, max_comparisons=10) == \
        T.schedule(list(reversed(h)), list(reversed(JUDGES)), seed=3, max_comparisons=10)


def test_comparisons_are_capped_and_balanced():
    h = hyps(12)
    s = T.schedule(h, JUDGES, seed=1, max_comparisons=28)
    assert len(s) == 28
    per = {x["id"]: 0 for x in h}
    for m in s:
        per[m["a"]] += 1
        per[m["b"]] += 1
    assert max(per.values()) - min(per.values()) <= 1
    assert len(T.schedule(h, JUDGES, seed=1, max_comparisons=0)) == 0
    assert len(T.schedule(hyps(3), JUDGES, seed=1, max_comparisons=28)) == 3


def test_judges_come_from_other_families_where_possible():
    s = T.schedule(hyps(6), JUDGES, seed=2, max_comparisons=15)
    by_id = {x["id"]: x for x in hyps(6)}
    for m in s:
        fams = {by_id[m["a"]]["family"], by_id[m["b"]]["family"]}
        authors = {by_id[m["a"]]["author"], by_id[m["b"]]["author"]}
        assert m["judge"] not in authors
        assert m["judge_family"] not in fams
        assert m["judge_tier"] == "other-family"
    # load spread over the two eligible judges
    loads = {j: sum(1 for m in s if m["judge"] == j) for j in ("gemini-c", "glm-d")}
    assert abs(loads["gemini-c"] - loads["glm-d"]) <= 1


def test_judge_fallbacks_are_flagged():
    h = hyps(2)                                # claude-a vs codex-b
    only_same = [{"agent": "claude-x", "family": "claude"}]
    m = T.schedule(h, only_same, seed=1, max_comparisons=1)[0]
    assert (m["judge"], m["judge_tier"]) == ("claude-x", "same-family")
    unknown = [{"agent": "mystery", "family": None}, {"agent": "claude-x", "family": "claude"}]
    m = T.schedule(h, unknown, seed=1, max_comparisons=1)[0]
    assert (m["judge"], m["judge_tier"]) == ("mystery", "unknown-family")
    authors_only = [{"agent": "claude-a", "family": "claude"}]
    m = T.schedule(h, authors_only, seed=1, max_comparisons=1)[0]
    assert (m["judge"], m["judge_tier"]) == ("claude-a", "author")
    with pytest.raises(T.TournamentError):
        T.schedule(h, [], seed=1, max_comparisons=1)


def test_elo_is_deterministic_and_rewards_winners():
    h = hyps(4)
    s = T.schedule(h, JUDGES, seed=11, max_comparisons=6)
    always_h1 = {}
    for m in s:
        if "H1" in (m["a"], m["b"]):
            always_h1[m["match"]] = "H1"
        else:
            always_h1[m["match"]] = "tie"
    d1 = T.build(h, JUDGES, always_h1, seed=11, max_comparisons=6, top_k=2)
    d2 = T.build(h, JUDGES, always_h1, seed=11, max_comparisons=6, top_k=2)
    assert d1 == d2
    T.validate(d1)
    ranked = [x["id"] for x in sorted(d1["hypotheses"], key=lambda x: x["rank"])]
    assert ranked[0] == "H1"
    h1 = next(x for x in d1["hypotheses"] if x["id"] == "H1")
    assert (h1["wins"], h1["losses"], h1["comparisons"]) == (3, 0, 3)
    assert d1["top"] == ranked[:2]
    assert d1["k"] == T.DEFAULT_K and d1["initial"] == T.DEFAULT_INITIAL
    # zero-sum
    total = sum(x["elo"] for x in d1["hypotheses"])
    assert total == pytest.approx(4 * T.DEFAULT_INITIAL, abs=0.05)


def test_elo_update_matches_the_formula():
    ra, rb = T.update(1200.0, 1200.0, 1.0, k=32)
    assert (ra, rb) == (pytest.approx(1216.0), pytest.approx(1184.0))
    ra, rb = T.update(1400.0, 1200.0, 0.5, k=32)
    exp = 1 / (1 + 10 ** (-200 / 400))
    assert ra == pytest.approx(1400 + 32 * (0.5 - exp))


def test_debate_status_breaks_elo_ties():
    h = hyps(4)
    h[0]["debate_status"] = "challenged"
    h[1]["debate_status"] = "consensus"
    h[2]["debate_status"] = "disputed"
    h[3]["debate_status"] = "unchallenged"
    s = T.schedule(h, JUDGES, seed=5, max_comparisons=6)
    ties = {m["match"]: "tie" for m in s}
    d = T.build(h, JUDGES, ties, seed=5, max_comparisons=6, top_k=4)
    order = [x["id"] for x in sorted(d["hypotheses"], key=lambda x: x["rank"])]
    assert order == ["H2", "H4", "H3", "H1"]


def test_missing_or_invalid_verdicts_change_nothing_and_are_counted():
    h = hyps(3)
    s = T.schedule(h, JUDGES, seed=5, max_comparisons=3)
    verdicts = {s[0]["match"]: "H99", s[1]["match"]: None}
    d = T.build(h, JUDGES, verdicts, seed=5, max_comparisons=3, top_k=3)
    assert all(x["elo"] == T.DEFAULT_INITIAL for x in d["hypotheses"])
    assert [m["result"] for m in d["matches"]] == ["invalid", "no-verdict", "no-verdict"]
    assert d["no_verdict"] == 3


def test_withdrawn_and_duplicate_hypotheses_are_refused():
    h = hyps(3)
    h[2]["debate_status"] = "withdrawn"
    with pytest.raises(T.TournamentError, match="withdrawn"):
        T.schedule(h, JUDGES, seed=1, max_comparisons=3)
    h = hyps(3)
    h[2]["id"] = "H1"
    with pytest.raises(T.TournamentError, match="duplicate"):
        T.schedule(h, JUDGES, seed=1, max_comparisons=3)
    h = hyps(3)
    h[0]["debate_status"] = "weird"
    with pytest.raises(T.TournamentError, match="debate status"):
        T.schedule(h, JUDGES, seed=1, max_comparisons=3)


def test_positions_are_randomised_by_seed_not_by_id():
    s = T.schedule(hyps(8), JUDGES, seed=4, max_comparisons=28)
    firsts = sum(1 for m in s if int(m["a"][1:]) < int(m["b"][1:]))
    assert 0 < firsts < 28


def test_every_pair_at_most_once_and_no_self_match():
    s = T.schedule(hyps(5), JUDGES, seed=9, max_comparisons=100)
    assert len(s) == 10
    for m in s:
        assert m["a"] != m["b"]
    assert [m["match"] for m in s] == list(range(1, 11))
    assert {frozenset((m["a"], m["b"])) for m in s} == \
        {frozenset(p) for p in itertools.combinations([f"H{i}" for i in range(1, 6)], 2)}


# --- review findings (ticket 15) --------------------------------------------------------------


def test_families_are_normalised_before_tiering():
    h = [{"id": "H1", "author": "a1", "family": "", "debate_status": "unchallenged"},
         {"id": "H2", "author": "a2", "family": "OpenAI", "debate_status": "unchallenged"}]
    m = T.schedule(h, [{"agent": "g", "family": "google"}], seed=1, max_comparisons=1)[0]
    assert m["judge_tier"] == "unknown-family"
    h[0]["family"] = "Google "
    m = T.schedule(h, [{"agent": "g", "family": "google"},
                       {"agent": "z", "family": "zhipu"}], seed=1, max_comparisons=1)[0]
    assert (m["judge"], m["judge_tier"]) == ("z", "other-family")
    m = T.schedule(h, [{"agent": "o", "family": "openai"}], seed=1, max_comparisons=1)[0]
    assert m["judge_tier"] == "same-family"


def test_ambiguous_ids_and_duplicate_judges_are_refused():
    h = hyps(3)
    h[1]["id"] = "H01"
    with pytest.raises(T.TournamentError, match="duplicate"):
        T.schedule(h, JUDGES, seed=1, max_comparisons=3)
    with pytest.raises(T.TournamentError, match="judge"):
        T.schedule(hyps(3), JUDGES + [{"agent": "glm-d", "family": "other"}], seed=1,
                   max_comparisons=3)


def test_verdicts_keyed_by_strings_count():
    h = hyps(2)
    s = T.schedule(h, JUDGES, seed=1, max_comparisons=1)
    d = T.build(h, JUDGES, {str(s[0]["match"]): s[0]["a"]}, seed=1, max_comparisons=1, top_k=1)
    assert d["no_verdict"] == 0 and d["top"] == [s[0]["a"]]
