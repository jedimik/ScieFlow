"""Gap-discovery pipeline steps (ticket 16, plan T3.5, spec §7).

The coordinator-side mechanical steps that tie the M3 pieces together: settings,
the evidence map (scite disputes + open questions, free providers), the
verification gate over detector and agent gaps, the hypothesis pool, the
tournament rounds with judge prompts, the top-k scite check under the gap-run
cap, one evolution round, the report check and the user's selection gate.
Nothing reaches the network: MCP is a counting fake, providers are off or
replaced at module level.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite import policy as spolicy
from scieflow.research.discovery import pipeline as pl
from scieflow.research.discovery import tournament
from scieflow.research.lib import http as http_lib

REPO = Path(__file__).resolve().parents[2]
SLUG = "2026-10-gap-unit"
TOKEN = "sk-unit...fake"
HOST_ENV = {"SCITE_TOKEN": TOKEN, "UV_CACHE_DIR": "/home/u/.cache/uv"}
FAMILIES = {"stub_a": "claude", "stub_b": "openai", "stub_c": "gemini"}

GRAPH = {"seeds": ["10.1000/seed.1"], "papers": {"10.1000/seed.1": {"title": "Seed study"}},
         "edges": [{"s": "10.1000/citing.1", "t": "10.1000/seed.1", "type": "contrasting",
                    "snippets": [{"text": "In contrast to the seed study, we found no "
                                          "association with olfactory loss."}]}]}
SEARCH = {"hits": [{"doi": "10.1000/open.1", "title": "Open problems in prodromal synucleinopathy",
                    "abstract": "Conversion rates are well described. Whether olfactory loss "
                                "precedes motor signs remains unclear in prospective work."}]}


TALLY = {"tally": {"supporting": 3, "contrasting": 2, "mentioning": 9, "unclassified": 0,
                   "total": 14}, "citing_publications": 6}
REST_CALLS: list = []


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network attempted")
    monkeypatch.setattr(smcp, "sdk_rpc", boom)
    monkeypatch.setattr(http_lib, "send", boom)
    REST_CALLS.clear()

    def rest_factory(policy, ledger, cache):
        def tallies(dois, r):
            REST_CALLS.append(list(dois))
            return {d: TALLY for d in dois}
        return tallies
    monkeypatch.setattr(pl, "_default_rest_factory", rest_factory)


class FakeMCP:
    def __init__(self, errors=None):
        self.calls: list[tuple] = []
        self.errors = list(errors or [])

    def rpc_factory(self, url, token):
        assert token == TOKEN

        def rpc(tool, args):
            self.calls.append((tool, dict(args)))
            if self.errors:
                err = self.errors.pop(0)
                if err is not None:
                    raise err
            payload = GRAPH if tool == "citation_graph" else SEARCH
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        return rpc

    def factory(self, policy, ledger, cache):
        client = smcp.MCPClient(policy, cache, ledger, rpc_factory=self.rpc_factory,
                                environ=HOST_ENV, sleep=lambda s: None, jitter=lambda: 0.0)
        return lambda tool, args, r: client.call(tool, args, round=r).value


def agents_yml() -> str:
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    return "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1, tier: primary, '
        f"model_family: {fam}}}\n" for a, fam in FAMILIES.items())


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "defaults.yml", tmp_path / "config" / "defaults.yml")
    (tmp_path / "config" / "agents.yml").write_text(agents_yml())
    (tmp_path / "config" / "scite.yml").write_text(yaml.safe_dump(
        {"max_mcp_calls_per_round": 5, "max_mcp_calls_per_run": 20}))
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    for key, value in HOST_ENV.items():
        monkeypatch.setenv(key, value)
    return Project(tmp_path)


def make_run(project, *, scite="approved", config=None, query=None) -> Path:
    ws = project.run_dir(SLUG)
    for d in ("inputs/results", "gaps", "hypotheses", "discovery", "report", "findings"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    cfg = {"assignments": {"research.gap-analysis": list(FAMILIES),
                           "research.debate": list(FAMILIES)},
           "review": {"scite": scite}, **(config or {})}
    (ws / "config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    (ws / "status.yml").write_text(yaml.safe_dump(
        {"workflow": "gap-discovery", "slug": SLUG, "phase": "gap-analysis", "phases": {}}))
    (ws / "inputs" / "results" / "metrics.csv").write_text("marker,auc\nolfaction,0.81\n")
    (ws / "inputs" / "manifest.yml").write_text(yaml.safe_dump({
        "package": SLUG, "delivered": "2026-10-14", "processing": "processing.md",
        "artifacts": [{"id": "tbl-metrics", "file": "results/metrics.csv", "kind": "table",
                       "description": "per-marker discrimination table"}]}))
    q = query if query is not None else {
        "topic": "olfactory loss prodromal synucleinopathy",
        "seeds": ["10.1000/seed.1"],
    }
    (ws / "discovery" / "query.yml").write_text(yaml.safe_dump(q, sort_keys=False))
    return ws


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))


def gaps_doc(agent, gaps, hypotheses=()):
    return {"agent": agent, "topic": "t", "gaps": list(gaps), "hypotheses": list(hypotheses)}


def hyp(hid, gap, statement):
    return {"id": hid, "gap": gap, "statement": statement,
            "testable_prediction": "Olfactory testing predicts later motor conversion.",
            "confidence": 3,
            "experiment": {"design": "prospective", "data_needed": "olfaction scores",
                           "methods": "survival analysis", "feasibility_note": "feasible"}}


# --- settings ---------------------------------------------------------------------------------


def test_shipped_defaults_carry_the_gap_discovery_keys():
    d = yaml.safe_load((REPO / "config" / "defaults.yml").read_text())["research"]
    assert d["evolution_rounds"] == 1
    assert d["tournament_top_k"] == 8
    assert d["max_tournament_comparisons"] == 28


def test_settings_merge_run_overrides_and_refuse_bad_values(project):
    make_run(project)
    s = pl.settings(project, SLUG)
    assert (s["evolution_rounds"], s["tournament_top_k"], s["max_tournament_comparisons"]) \
        == (1, 8, 28)
    assert isinstance(s["seed"], int) and s["seed"] == pl.settings(project, SLUG)["seed"]
    make_run(project, config={"tournament_top_k": 3, "tournament_seed": 7})
    s = pl.settings(project, SLUG)
    assert s["tournament_top_k"] == 3 and s["seed"] == 7
    make_run(project, config={"max_tournament_comparisons": -1})
    with pytest.raises(pl.DiscoveryError, match="max_tournament_comparisons"):
        pl.settings(project, SLUG)
    make_run(project, config={"evolution_rounds": "once"})
    with pytest.raises(pl.DiscoveryError, match="evolution_rounds"):
        pl.settings(project, SLUG)


# --- evidence map -------------------------------------------------------------------------------


def test_plan_lists_exactly_the_outgoing_requests_and_sends_nothing(project):
    make_run(project, query={"topic": "olfactory loss prodromal synucleinopathy",
                             "seeds": ["10.1000/seed.1"],
                             "ctgov": {"condition": "REM sleep behavior disorder"},
                             "trends": ["REM sleep behavior disorder"],
                             "bridge": ["REM sleep behavior disorder", "gut microbiome"]})
    fake = FakeMCP()
    out = pl.gaps_evidence(project, SLUG, plan_only=True, mcp_factory=fake.factory,
                           environ=HOST_ENV)
    assert fake.calls == []
    assert not (project.run_dir(SLUG) / "discovery" / "evidence").exists()
    tools = [r["tool"] for r in out["plan"]["scite"]]
    assert tools == ["citation_graph", "search_literature"]
    assert out["plan"]["mcp_calls"] == 2
    assert out["plan"]["scite"][1]["args"]["term"] == "olfactory loss prodromal synucleinopathy"
    providers = {(r["provider"], r["detector"]) for r in out["plan"]["providers"]}
    assert providers == {("ctgov", "unpublished_trials"), ("openalex", "trend_breaks"),
                         ("openalex", "bridges")}


@pytest.mark.parametrize("query, what", [
    ({"topic": "olfaction in our cohort of n = 43 patients"}, "topic"),
    ({"topic": "olfaction", "seeds": ["../../config/scite.yml"]}, "seeds"),
    ({"topic": "olfaction", "bridge": ["[data:cohort_a]", "gut"]}, "bridge"),
    ({"topic": "olfaction", "extra": 1}, "extra"),
])
def test_query_refuses_leaks_and_unknown_keys(project, query, what):
    make_run(project, query=query)
    fake = FakeMCP()
    with pytest.raises(pl.DiscoveryError, match=what):
        pl.gaps_evidence(project, SLUG, plan_only=True, mcp_factory=fake.factory,
                         environ=HOST_ENV)
    assert fake.calls == []


def test_evidence_runs_both_scite_detectors_in_two_calls(project):
    ws = make_run(project)
    fake = FakeMCP()
    out = pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    assert [t for t, _ in fake.calls] == ["citation_graph", "search_literature"]
    assert out["mcp_calls"] == 2
    det = out["detectors"]
    assert det["disputes"]["status"] == "ok" and det["disputes"]["gaps"] == 1
    assert det["open_questions"]["status"] == "ok" and det["open_questions"]["gaps"] == 1
    for name in ("abc_links", "unpublished_trials", "bridges", "trend_breaks"):
        assert det[name]["status"] in ("off", "not-asked")
    saved = json.loads((ws / "discovery" / "evidence" / "disputes.json").read_text())
    assert saved["gaps"][0]["evidence"][0]["quote"].startswith("In contrast to the seed study")


def test_scite_off_sends_nothing_but_providers_still_may_run(project):
    make_run(project, scite="off")
    fake = FakeMCP()
    out = pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    assert fake.calls == []
    assert out["detectors"]["disputes"]["status"] == "off"
    assert out["detectors"]["open_questions"]["status"] == "off"


def test_scite_ask_without_a_human_consent_gate_is_refused(project):
    make_run(project, scite="ask")
    fake = FakeMCP()
    with pytest.raises(spolicy.PolicyError, match="scite-consent"):
        pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    assert fake.calls == []


def test_evidence_refuses_inside_a_dispatch(project):
    make_run(project)
    fake = FakeMCP()
    env = {**HOST_ENV, "SCIEFLOW_DISPATCH": "1"}
    with pytest.raises(spolicy.PolicyError):
        pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=env)
    assert fake.calls == []


def test_quota_on_the_first_call_stops_the_scite_detectors(project):
    make_run(project)
    quota = smcp.RPCError("Usage quota exceeded for your plan.", code=-32000)
    fake = FakeMCP(errors=[quota])
    out = pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    assert len(fake.calls) == 1                       # never retried, the second not sent
    assert out["detectors"]["disputes"]["status"] == "quota"
    assert out["detectors"]["open_questions"]["status"] == "quota"


def test_provider_detectors_use_the_gateway_results(project, monkeypatch):
    from scieflow.research.lib import ctgov, openalex_trends, pubtator
    make_run(project, scite="off", query={
        "topic": "olfaction", "pubtator": {"entity": "@DISEASE_REM_Sleep_Behavior_Disorder"},
        "ctgov": {"condition": "REM sleep behavior disorder"}, "until_year": 2025,
        "as_of": "2026-10-14"})
    monkeypatch.setattr(pubtator, "open_links", lambda gw, a, **k: {"a": a, "b": ["X"], "links": [
        {"a": a, "c": "@GENE_SNCA", "via": [{"b": "@CHEMICAL_X", "ab_type": "associate",
                                             "ab_publications": 4, "bc_type": "associate",
                                             "bc_publications": 2}]}]})
    monkeypatch.setattr(ctgov, "search_completed", lambda gw, c, **k: {"studies": [
        {"nct_id": "NCT00000001", "title": "Olfactory training in RBD", "status": "COMPLETED",
         "completion_date": "2020-01", "publications": []}], "total": 1, "complete": True,
        "pages": 1})
    monkeypatch.setattr(openalex_trends, "trend", lambda gw, t, **k: {"term": t, "breaks": []})

    class GW:
        class policy:
            @staticmethod
            def allows(name):
                return name in ("pubtator", "ctgov")
    out = pl.gaps_evidence(project, SLUG, gw=GW(), environ=HOST_ENV)
    assert out["detectors"]["abc_links"] == {"status": "ok", "gaps": 1}
    assert out["detectors"]["unpublished_trials"] == {"status": "ok", "gaps": 1, "complete": True}
    assert out["detectors"]["trend_breaks"]["status"] in ("off", "not-asked")


# --- verification gate ---------------------------------------------------------------------------


def run_evidence(project):
    fake = FakeMCP()
    pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    return fake


def test_verify_keeps_quoted_gaps_and_drops_invented_quotes(project):
    ws = make_run(project)
    run_evidence(project)
    write_json(ws / "gaps" / "stub_a.json", gaps_doc("stub_a", [
        {"id": "G1", "detector": "open_questions", "statement": "Timing of olfactory loss.",
         "novelty_rationale": "stated open",
         "evidence": [{"kind": "paper", "ref": "10.1000/open.1", "source": "scite_mcp",
                       "quote": "Whether olfactory loss precedes motor signs remains unclear"}]},
        {"id": "G2", "statement": "Invented.", "novelty_rationale": "none",
         "evidence": [{"kind": "paper", "ref": "10.1000/open.1", "source": "scite_mcp",
                       "quote": "olfactory loss always precedes motor signs by a decade"}]},
        {"id": "G3", "statement": "No quote at all.", "novelty_rationale": "none",
         "evidence": [{"kind": "paper", "ref": "10.1000/open.1"}]},
        {"id": "G4", "statement": "Own data.", "novelty_rationale": "local",
         "evidence": [{"kind": "data", "ref": "tbl-metrics", "source": "user",
                       "quote": "per-marker discrimination table"}]},
    ]))
    out = pl.verify(project, SLUG)
    kept = {(g["source"], g["statement"]) for g in out["kept"]}
    assert ("stub_a/G1", "Timing of olfactory loss.") in kept
    assert ("stub_a/G4", "Own data.") in kept
    assert not any(s in ("Invented.", "No quote at all.") for _, s in kept)
    assert {d["source"] for d in out["dropped"]} == {"stub_a/G2", "stub_a/G3"}
    assert [g["id"] for g in out["kept"]] == [f"G{i + 1}" for i in range(len(out["kept"]))]
    sources = [g["source"] for g in out["kept"]]
    assert sources[:2] == ["detector:disputes", "detector:open_questions"]
    assert json.loads((ws / "discovery" / "verified.json").read_text())["kept"] == out["kept"]


def test_verify_needs_the_evidence_map_first(project):
    make_run(project)
    with pytest.raises(pl.DiscoveryError, match="scite gaps"):
        pl.verify(project, SLUG)


# --- hypothesis pool -----------------------------------------------------------------------------


def verified_run(project):
    ws = make_run(project)
    run_evidence(project)
    pl.verify(project, SLUG)
    return ws


def test_pool_keeps_hypotheses_on_verified_gaps_with_author_and_family(project):
    ws = verified_run(project)
    write_json(ws / "hypotheses" / "stub_a.json", gaps_doc("stub_a", [], [
        hyp("H1", "G1", "Olfactory loss precedes motor conversion in prodromal disease."),
        hyp("H2", "G99", "A hypothesis on a gap that never survived verification.")]))
    write_json(ws / "hypotheses" / "stub_b.json", gaps_doc("stub_b", [], [
        hyp("H1", "G2", "Dysautonomia precedes olfactory loss in prodromal disease.")]))
    out = pl.pool(project, SLUG)
    assert [(h["id"], h["author"], h["family"], h["source"]) for h in out["hypotheses"]] == [
        ("H1", "stub_a", "claude", "stub_a/H1"), ("H2", "stub_b", "openai", "stub_b/H1")]
    assert out["dropped"] == [{"source": "stub_a/H2", "reason": "gap G99 is not a verified gap"}]
    assert all(h["debate_status"] == "undebated" for h in out["hypotheses"])


def test_pool_caps_each_agent_at_max_hypotheses(project):
    ws = make_run(project, config={"max_hypotheses": 1})
    run_evidence(project)
    pl.verify(project, SLUG)
    write_json(ws / "hypotheses" / "stub_a.json", gaps_doc("stub_a", [], [
        hyp("H1", "G1", "First."), hyp("H2", "G1", "Second.")]))
    out = pl.pool(project, SLUG)
    assert [h["source"] for h in out["hypotheses"]] == ["stub_a/H1"]
    assert out["dropped"] == [{"source": "stub_a/H2", "reason": "over max_hypotheses (1)"}]


def test_debate_status_is_recorded_and_withdrawn_items_leave_the_tournament(project):
    ws = pooled_run(project, n=3)
    pl.set_debate_status(project, SLUG, {"H1": "consensus", "H3": "withdrawn"})
    with pytest.raises(pl.DiscoveryError, match="H9"):
        pl.set_debate_status(project, SLUG, {"H9": "consensus"})
    with pytest.raises(pl.DiscoveryError, match="status"):
        pl.set_debate_status(project, SLUG, {"H1": "great"})
    sched = pl.schedule(project, SLUG, 1)
    ids = {m["a"] for m in sched["matches"]} | {m["b"] for m in sched["matches"]}
    assert ids == {"H1", "H2"}
    hs = json.loads((ws / "discovery" / "hypotheses.json").read_text())["hypotheses"]
    assert {h["id"]: h["debate_status"] for h in hs}["H1"] == "consensus"


# --- tournament -----------------------------------------------------------------------------------


STATEMENTS = [
    "Olfactory loss precedes motor conversion in prodromal synucleinopathy.",
    "Constipation precedes olfactory loss in prodromal synucleinopathy.",
    "Retinal thinning marks conversion risk in isolated REM sleep behaviour disorder.",
    "Heart rate variability declines before dopaminergic imaging changes.",
    "Skin biopsy alpha-synuclein seeding predicts phenoconversion.",
]


def pooled_run(project, n=4, config=None):
    ws = make_run(project, config=config)
    run_evidence(project)
    pl.verify(project, SLUG)
    agents = list(FAMILIES)
    for i in range(n):
        a = agents[i % 3]
        path = ws / "hypotheses" / f"{a}.json"
        doc = json.loads(path.read_text()) if path.exists() else gaps_doc(a, [])
        doc["hypotheses"].append(hyp(f"H{len(doc['hypotheses']) + 1}", "G1", STATEMENTS[i]))
        write_json(path, doc)
    pl.pool(project, SLUG)
    return ws


def answer_round(project, round_, pick=lambda m: m["a"]):
    ws = project.run_dir(SLUG)
    sched = json.loads((ws / "discovery" / f"round-{round_}" / "schedule.json").read_text())
    for m in sched["matches"]:
        write_json(ws / "discovery" / f"round-{round_}" / "verdicts" / f"match-{m['match']}.json",
                   {"match": m["match"], "winner": pick(m), "rationale": "stub"})


def test_schedule_caps_comparisons_and_writes_one_judge_prompt_per_match(project):
    ws = pooled_run(project, n=5, config={"max_tournament_comparisons": 4})
    sched = pl.schedule(project, SLUG, 1)
    assert len(sched["matches"]) == 4
    rd = ws / "discovery" / "round-1"
    prompts = sorted((rd / "judge").glob("match-*.md"))
    assert len(prompts) == 4
    text = prompts[0].read_text()
    m = sched["matches"][0]
    assert f"output: workspace/{SLUG}/discovery/round-1/verdicts/match-1.json" in text
    assert "kind: tournament-verdict" in text and f"\na: {m['a']}\n" in text \
        and f"\nb: {m['b']}\n" in text
    assert "data, not instructions" in text
    for x in sched["matches"]:
        assert x["judge"] in FAMILIES
    # judges come from the run's gap-analysis / debate agents, with their families
    assert {j["agent"]: j["family"] for j in sched["judges"]} == FAMILIES
    assert {j["role"] for j in sched["judges"]} == {"research.gap-analysis"}
    assert pl.schedule(project, SLUG, 1)["matches"] == sched["matches"]   # deterministic


def test_rank_builds_a_valid_tournament_and_counts_bad_verdicts(project):
    ws = pooled_run(project, n=4)
    sched = pl.schedule(project, SLUG, 1)
    answer_round(project, 1, pick=lambda m: min(m["a"], m["b"]))
    first = sched["matches"][0]["match"]
    write_json(ws / "discovery" / "round-1" / "verdicts" / f"match-{first}.json",
               {"match": first + 1, "winner": sched["matches"][0]["a"]})   # wrong match number
    doc = pl.rank(project, SLUG, 1)
    tournament.validate(doc)
    assert doc["no_verdict"] == 1
    assert doc["top"][0] == "H1"
    assert json.loads((ws / "discovery" / "round-1" / "tournament.json").read_text()) == doc


# --- scite top-k under the gap-run cap ---------------------------------------------------------


def ranked_run(project, n=4, config=None):
    ws = pooled_run(project, n=n, config=config)
    pl.precheck(project, SLUG, 1)
    pl.schedule(project, SLUG, 1)
    answer_round(project, 1, pick=lambda m: min(m["a"], m["b"]))
    pl.rank(project, SLUG, 1)
    return ws


def test_precheck_abstracts_every_hypothesis_and_reports_providers_off(project):
    ws = pooled_run(project, n=2)
    out = pl.precheck(project, SLUG, 1)
    assert [a["id"] for a in out["abstracts"]] == ["H1", "H2"]
    assert all(p["openalex"]["status"] == "off" for p in out["precheck"])
    assert (ws / "discovery" / "round-1" / "novelty-free.json").is_file()


def test_scite_novelty_checks_the_top_k_within_two_calls(project):
    ranked_run(project, config={"tournament_top_k": 2})
    fake = FakeMCP()
    out = pl.scite_novelty(project, SLUG, 1, mcp_factory=fake.factory, environ=HOST_ENV)
    assert 1 <= len(fake.calls) <= 2
    assert set(out["results"]) == {"H1", "H2"}
    assert out["gap_run"]["mcp_calls"] <= pl.GAP_RUN_MAX_MCP


def test_scite_novelty_plan_sends_nothing(project):
    ranked_run(project)
    fake = FakeMCP()
    out = pl.scite_novelty(project, SLUG, 1, plan_only=True, mcp_factory=fake.factory,
                           environ=HOST_ENV)
    assert fake.calls == [] and out["plan"]["requests"][0]["tool"] == "search_literature"


def test_the_gap_run_cap_is_enforced_in_code(project, monkeypatch):
    ranked_run(project)
    monkeypatch.setattr(pl, "GAP_RUN_MAX_MCP", 3)      # evidence already used 2
    fake = FakeMCP()
    out = pl.scite_novelty(project, SLUG, 1, mcp_factory=fake.factory, environ=HOST_ENV)
    assert [t for t, _ in fake.calls] == ["search_literature"]
    assert out["status"] == "partial"                 # the graph call hit the cap
    led = ledger_mod.Ledger(project.run_dir(SLUG), spolicy.load(project.root, HOST_ENV))
    assert led.calls("run", "mcp") == 3
    fake2 = FakeMCP()
    with pytest.raises(spolicy.PolicyError, match="quota"):
        pl.scite_novelty(project, SLUG, 1, mcp_factory=fake2.factory, environ=HOST_ENV)
    assert fake2.calls == []


# --- evolution --------------------------------------------------------------------------------------


def statement_of(ws, hid):
    hs = json.loads((ws / "discovery" / "hypotheses.json").read_text())["hypotheses"]
    return {h["id"]: h["statement"] for h in hs}[hid]


def test_evolution_refines_top_k_once_and_marks_changed_items(project):
    ws = ranked_run(project, config={"tournament_top_k": 2})
    write_json(ws / "discovery" / "evolution.json", {
        "H1": {"statement": "Olfactory loss precedes motor conversion by several years in "
                            "prodromal synucleinopathy.", "rationale": "sharpened timing"},
        "H2": {"statement": statement_of(ws, "H2")}})        # unchanged
    out = pl.evolve(project, SLUG)
    assert out["round"] == 2 and out["changed"] == ["H1"]
    r2 = json.loads((ws / "discovery" / "round-2" / "hypotheses.json").read_text())
    assert [h["id"] for h in r2["hypotheses"]] == ["H1", "H2"]
    assert r2["hypotheses"][0]["evolved_from"] == STATEMENTS[0]
    with pytest.raises(pl.DiscoveryError, match="evolution_rounds"):
        pl.evolve(project, SLUG, from_round=2)


def test_evolution_refuses_items_outside_the_top_k_and_when_switched_off(project):
    ws = ranked_run(project, config={"tournament_top_k": 2})
    write_json(ws / "discovery" / "evolution.json", {"H4": {"statement": "x"}})
    with pytest.raises(pl.DiscoveryError, match="H4"):
        pl.evolve(project, SLUG)
    cfg = yaml.safe_load((ws / "config.yml").read_text())
    cfg["evolution_rounds"] = 0
    (ws / "config.yml").write_text(yaml.safe_dump(cfg))
    write_json(ws / "discovery" / "evolution.json", {"H1": {"statement": "y z"}})
    with pytest.raises(pl.DiscoveryError, match="evolution_rounds"):
        pl.evolve(project, SLUG)


def test_round_two_rechecks_only_changed_items(project):
    ws = ranked_run(project, config={"tournament_top_k": 2})
    write_json(ws / "discovery" / "evolution.json", {
        "H1": {"statement": "Olfactory loss precedes motor conversion by several years in "
                            "prodromal synucleinopathy."}})
    pl.evolve(project, SLUG)
    free = pl.precheck(project, SLUG, 2)
    assert [a["id"] for a in free["abstracts"]] == ["H1"]
    pl.schedule(project, SLUG, 2)
    answer_round(project, 2)
    pl.rank(project, SLUG, 2)
    fake = FakeMCP()
    out = pl.scite_novelty(project, SLUG, 2, mcp_factory=fake.factory, environ=HOST_ENV)
    assert set(out["results"]) == {"H1"}


# --- report, table, selection ---------------------------------------------------------------------


def test_tournament_table_is_deterministic_and_report_check_needs_it(project):
    ws = ranked_run(project)
    table = pl.tournament_table(project, SLUG)
    assert table == pl.tournament_table(project, SLUG)
    assert table.splitlines()[0].startswith("| Rank | Hypothesis | Elo |")
    problems = pl.report_check(project, SLUG)
    assert any("meta-review" in p for p in problems)
    assert any("## Tournament" in p for p in problems)
    (ws / "report" / "meta-review.md").write_text("# Meta-review\n\nPatterns: none.\n")
    (ws / "report" / "gaps.md").write_text(
        "# Gap Report: t\n\n## Tournament\n\n" + table + "\n\n## Meta-review\n\nSee file.\n")
    assert pl.report_check(project, SLUG) == []
    assert json.loads((ws / "report" / "tournament.json").read_text())["top"][0] == "H1"


def test_selection_needs_a_human_answer_with_its_event(project):
    ws = ranked_run(project)
    gate = pl.open_selection(project, SLUG)
    assert gate["kind"] == "hypothesis-selection" and gate["requires_human"] is True
    with pytest.raises(pl.DiscoveryError, match="no human-answered"):
        pl.read_selection(project, SLUG)
    # a hand-written answer (no gate.answered event) is ignored
    forged = {**gate, "state": "answered", "answer": "H1", "answered_by": "human"}
    (ws / "gates" / f"{gate['id']}.json").write_text(json.dumps(forged))
    with pytest.raises(pl.DiscoveryError, match="no human-answered"):
        pl.read_selection(project, SLUG)
    (ws / "gates" / f"{gate['id']}.json").write_text(json.dumps(gate))
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, gate["id"], "H1", actor="agent", rationale="I like it")
    gates.answer(project, ws, gate["id"], "H2, H1")
    out = pl.read_selection(project, SLUG)
    assert out["selected"] == ["H2", "H1"]
    saved = json.loads((ws / "report" / "selected_hypotheses.json").read_text())
    assert [h["id"] for h in saved["hypotheses"]] == ["H2", "H1"]


def test_selection_refuses_unknown_ids(project):
    ws = ranked_run(project)
    gate = pl.open_selection(project, SLUG)
    gates.answer(project, ws, gate["id"], "H1, H42")
    with pytest.raises(pl.DiscoveryError, match="H42"):
        pl.read_selection(project, SLUG)


def test_selection_none_is_a_valid_answer(project):
    ws = ranked_run(project)
    gate = pl.open_selection(project, SLUG)
    gates.answer(project, ws, gate["id"], "none")
    assert pl.read_selection(project, SLUG)["selected"] == []
    assert any(e["type"] == "gate.answered" for e in events.read(ws))


# --- review findings (ticket 16) -----------------------------------------------------------------


def test_evidence_map_carries_public_tallies_for_the_seeds(project):
    ws = make_run(project)
    plan = pl.gaps_evidence(project, SLUG, plan_only=True, environ=HOST_ENV)["plan"]
    assert plan["rest"] == [{"op": "tallies", "dois": ["10.1000/seed.1"]}]
    assert REST_CALLS == []
    out = pl.gaps_evidence(project, SLUG, mcp_factory=FakeMCP().factory, environ=HOST_ENV)
    assert out["tallies"] == {"status": "ok", "dois": 1}
    assert REST_CALLS == [["10.1000/seed.1"]]
    saved = json.loads((ws / "discovery" / "evidence" / "tallies.json").read_text())
    assert saved["tallies"]["10.1000/seed.1"]["tally"]["contrasting"] == 2


def test_tallies_follow_scite_off(project):
    make_run(project, scite="off")
    out = pl.gaps_evidence(project, SLUG, mcp_factory=FakeMCP().factory, environ=HOST_ENV)
    assert out["tallies"]["status"] == "off" and REST_CALLS == []


def test_a_quota_stopped_evidence_map_is_never_rebuilt(project):
    make_run(project)
    quota = smcp.RPCError("Usage quota exceeded for your plan.", code=-32000)
    pl.gaps_evidence(project, SLUG, mcp_factory=FakeMCP(errors=[quota]).factory,
                     environ=HOST_ENV)
    again = FakeMCP()
    with pytest.raises(spolicy.PolicyError, match="quota"):
        pl.gaps_evidence(project, SLUG, mcp_factory=again.factory, environ=HOST_ENV)
    assert again.calls == []
    summary = json.loads((project.run_dir(SLUG) / "discovery" / "evidence" /
                          "summary.json").read_text())
    assert summary["detectors"]["disputes"]["status"] == "quota"      # not overwritten


def test_a_quota_stopped_scite_check_is_never_rerun(project, monkeypatch):
    ranked_run(project)
    monkeypatch.setattr(pl, "GAP_RUN_MAX_MCP", 2)                    # evidence used both
    spent = FakeMCP()
    first = pl.scite_novelty(project, SLUG, 1, mcp_factory=spent.factory, environ=HOST_ENV)
    assert first["status"] == "quota" and spent.calls == []
    assert all(r["scite_status"] == "quota" for r in first["results"].values())
    monkeypatch.setattr(pl, "GAP_RUN_MAX_MCP", 5)
    again = FakeMCP()
    with pytest.raises(spolicy.PolicyError, match="quota"):
        pl.scite_novelty(project, SLUG, 1, mcp_factory=again.factory, environ=HOST_ENV)
    assert again.calls == []


def test_the_default_client_never_retries_past_the_gap_run_cap(project, monkeypatch):
    make_run(project)
    sent = []

    def rpc_503(url, token, **kw):
        def rpc(tool, args):
            sent.append(tool)
            raise smcp.RPCError("Service Unavailable", status=503)
        return rpc
    monkeypatch.setattr(smcp, "sdk_rpc", rpc_503)
    monkeypatch.setattr(smcp, "sdk_available", lambda: True)
    out = pl.gaps_evidence(project, SLUG, environ=HOST_ENV)
    assert sent == ["citation_graph"]                     # no retry, the second not sent
    assert out["detectors"]["disputes"]["status"] == "unavailable"
    assert out["detectors"]["open_questions"]["status"] == "unavailable"


def test_the_cap_is_reread_from_the_ledger_before_every_call(project, monkeypatch):
    ws = make_run(project)
    led = ledger_mod.Ledger(ws, spolicy.load(project.root, HOST_ENV))
    calls = iter([0, 5])
    monkeypatch.setattr(pl, "_mcp_used", lambda ws_, policy: next(calls, 5))
    fake = FakeMCP()
    out = pl.gaps_evidence(project, SLUG, mcp_factory=fake.factory, environ=HOST_ENV)
    assert [t for t, _ in fake.calls] == ["citation_graph"]
    assert out["detectors"]["open_questions"]["status"] == "quota"
    assert led is not None


@pytest.mark.parametrize("seed", ["10.1000/n=43", "10.1000/x/[data:cohort_a]",
                                  "10.1000/x/data:cohort_a", "10.1000/p<.001"])
def test_seed_dois_carrying_data_shapes_are_refused(project, seed):
    make_run(project, query={"topic": "olfaction", "seeds": [seed]})
    with pytest.raises(pl.DiscoveryError, match="seeds"):
        pl.gaps_evidence(project, SLUG, plan_only=True, environ=HOST_ENV)


def test_ordinary_seed_dois_pass(project):
    make_run(project, query={"topic": "olfaction",
                             "seeds": ["10.1016/j.sleep.2020.01.001", "10.1002/mds.27920"]})
    out = pl.gaps_evidence(project, SLUG, plan_only=True, environ=HOST_ENV)
    assert out["plan"]["scite"][0]["args"]["seeds"] == ["10.1016/j.sleep.2020.01.001",
                                                         "10.1002/mds.27920"]


def test_precheck_plan_lists_the_outgoing_queries_and_sends_nothing(project):
    ws = pooled_run(project, n=2)

    class GW:
        sent: list = []

        class policy:
            @staticmethod
            def allows(name):
                return True

        def get_json(self, *a, **k):
            GW.sent.append(a)
            raise AssertionError("sent")
    out = pl.precheck(project, SLUG, 1, plan_only=True, gw=GW())
    assert GW.sent == []
    assert [q["id"] for q in out["queries"]] == ["H1", "H2"]
    assert all(q["providers"] == ["openalex", "europepmc"] for q in out["queries"])
    assert not (ws / "discovery" / "round-1" / "novelty-free.json").exists()


def test_article_text_only_verifies_article_refs(project):
    ws = make_run(project)
    run_evidence(project)
    (ws / "inputs" / "article").mkdir()
    (ws / "inputs" / "article" / "draft.md").write_text(
        "Our draft argues that smell tests are cheap screening tools.")
    write_json(ws / "gaps" / "stub_a.json", gaps_doc("stub_a", [
        {"id": "G1", "statement": "Article gap.", "novelty_rationale": "x",
         "evidence": [{"kind": "article", "ref": "article:Discussion", "source": "user",
                       "quote": "smell tests are cheap screening tools"}]},
        {"id": "G2", "statement": "Article text passed off as a paper.", "novelty_rationale": "x",
         "evidence": [{"kind": "article", "ref": "10.9999/fake.1", "source": "user",
                       "quote": "smell tests are cheap screening tools"}]},
        {"id": "G3", "statement": "Same quote as a paper.", "novelty_rationale": "x",
         "evidence": [{"kind": "paper", "ref": "10.9999/fake.1", "source": "scite_mcp",
                       "quote": "smell tests are cheap screening tools"}]},
    ]))
    out = pl.verify(project, SLUG)
    kept = {g["source"] for g in out["kept"]}
    assert "stub_a/G1" in kept and "stub_a/G2" not in kept and "stub_a/G3" not in kept


def test_agent_written_relevance_text_is_not_paper_evidence(project):
    ws = make_run(project)
    run_evidence(project)
    write_json(ws / "findings" / "stub_a.json", {"agent": "stub_a", "topic": "t", "papers": [
        {"doi": "10.1000/lit.1", "title": "A cohort study of smell", "abstract": "We measured "
         "smell identification in a prospective cohort.",
         "relevance": {"score": 5, "why": "It proves smell loss causes conversion."}}]})
    write_json(ws / "gaps" / "stub_a.json", gaps_doc("stub_a", [
        {"id": "G1", "statement": "Abstract quote.", "novelty_rationale": "x",
         "evidence": [{"kind": "paper", "ref": "10.1000/lit.1",
                       "quote": "smell identification in a prospective cohort"}]},
        {"id": "G2", "statement": "Relevance quote.", "novelty_rationale": "x",
         "evidence": [{"kind": "paper", "ref": "10.1000/lit.1",
                       "quote": "proves smell loss causes conversion"}]}]))
    kept = {g["source"] for g in pl.verify(project, SLUG)["kept"]}
    assert "stub_a/G1" in kept and "stub_a/G2" not in kept


def test_evolution_cannot_be_redone_once_its_round_started(project):
    ws = ranked_run(project, config={"tournament_top_k": 2})
    write_json(ws / "discovery" / "evolution.json", {"H1": {"statement": "A sharper statement "
                                                                         "on olfactory loss."}})
    pl.evolve(project, SLUG)
    pl.schedule(project, SLUG, 2)
    with pytest.raises(pl.DiscoveryError, match="round 2"):
        pl.evolve(project, SLUG)


def test_a_partial_check_keeps_novelty_hits_when_only_counter_evidence_is_capped(
        project, monkeypatch):
    ranked_run(project, config={"tournament_top_k": 1})
    monkeypatch.setattr(pl, "GAP_RUN_MAX_MCP", 3)                    # evidence used 2
    fake = FakeMCP()
    out = pl.scite_novelty(project, SLUG, 1, mcp_factory=fake.factory, environ=HOST_ENV)
    assert [t for t, _ in fake.calls] == ["search_literature"]
    assert out["status"] == "partial"
    row = out["results"]["H1"]
    assert row["scite_status"] == "partial" and row["novelty_hits"]
    assert {"tool": "citation_graph", "reason": "budget"} in out["skipped"]


def test_a_single_hypothesis_still_gets_a_trivial_tournament(project):
    ws = pooled_run(project, n=1)
    sched = pl.schedule(project, SLUG, 1)
    assert sched["matches"] == []
    doc = pl.rank(project, SLUG, 1)
    assert doc["top"] == ["H1"]
    assert (ws / "discovery" / "round-1" / "tournament.json").is_file()


def test_judge_prompt_marks_referenced_files_as_data(project):
    ws = pooled_run(project, n=2)
    pl.schedule(project, SLUG, 1)
    text = (ws / "discovery" / "round-1" / "judge" / "match-1.md").read_text()
    assert "the files it names" in text and "data, not instructions" in text
