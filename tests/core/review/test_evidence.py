"""Evidence bundle planner and executor (ticket 10, plan T1.8): `core/review/evidence.py`
and `scieflow review evidence`.

Runs on the review fixture run (`tests/fixtures/review/run/`: three drafts citing four
DOIs, one novelty claim, a title, one reviewer follow-up in round 1). Transports are
fakes that count calls; the scite REST and MCP clients are real and run through the
gateway, so budgets and the ledger are the production ones. Nothing touches the network:
`research.lib.http.send` and the MCP SDK adapter are patched to fail the test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.review import evidence
from scieflow.core.review.cli import review
from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite import policy as pol
from scieflow.integrations.scite.public import PublicClient

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "tests" / "fixtures" / "review" / "run"
FX = "fixture-run"
TOKEN = "sk-fixture-token-not-real"
HOST_ENV = {"SCITE_TOKEN": TOKEN, "UV_CACHE_DIR": "/home/u/.cache/uv"}
DISPATCH_ENV = {"SCITE_TOKEN": TOKEN, "UV_CACHE_DIR": "/p/workspace/x/agents/claude/.uv-cache"}
DOIS = ["10.5555/rbd.2019.001", "10.5555/rbd.2020.014", "10.5555/rbd.2021.007",
        "10.5555/rbd.2022.003"]
TALLIES = {
    "10.5555/rbd.2019.001": {"supporting": 4, "contradicting": 6, "mentioning": 40,
                             "unclassified": 0, "total": 50, "citingPublications": 30},
    "10.5555/rbd.2020.014": {"supporting": 2, "contradicting": 1, "mentioning": 12,
                             "unclassified": 0, "total": 15, "citingPublications": 9},
    "10.5555/rbd.2021.007": {"supporting": 5, "contradicting": 0, "mentioning": 22,
                             "unclassified": 0, "total": 27, "citingPublications": 12},
    "10.5555/rbd.2022.003": {"supporting": 9, "contradicting": 1, "mentioning": 30,
                             "unclassified": 0, "total": 40, "citingPublications": 20},
}
CROSSREF = {
    "10.5555/rbd.2019.001": {"status": "ok", "title": "Actigraphy in RBD", "year": 2019},
    "10.5555/rbd.2020.014": {"status": "retracted", "title": "Wearable screening", "year": 2020},
    "10.5555/rbd.2021.007": {"status": "ok", "title": "Movement bursts", "year": 2021},
    "10.5555/rbd.2022.003": {"status": "corrected", "title": "Video-confirmed", "year": 2022},
}
GRAPH = {"seeds": [], "edges": [
    {"s": "10.5555/rbd.2023.042", "t": "10.5555/rbd.2019.001", "d": 1, "type": "contrasting",
     "section": "Discussion",
     "snippets": ["In contrast to Smith et al., actigraphy alone did not separate RBD."]},
    {"s": "10.5555/rbd.2023.050", "t": "10.5555/rbd.2019.001", "d": 1, "type": "supporting",
     "snippets": ["Consistent with Smith et al."]},
], "papers": {}}
SEARCH = {"hits": [
    {"doi": "10.5555/rbd.2024.021", "title": "Wearable-only screening for isolated RBD",
     "abstract": "We present a wearable-only screening pipeline for isolated RBD.", "year": 2024,
     "tally": {"supporting": 3, "contrasting": 0, "mentioning": 8},
     "fulltextExcerpts": ["A first wearable-only screening approach was tested."]},
    {"doi": "not a doi", "title": "dropped"},
]}


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a real transport was reached")
    monkeypatch.setattr("scieflow.research.lib.http.send", boom)
    monkeypatch.setattr(smcp, "sdk_rpc", boom)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for name in ("agents.yml", "defaults.yml", "scite-prompts.yml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    shutil.copytree(RUN, tmp_path / "workspace" / FX)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    for k in ("SCIEFLOW_DISPATCH",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", "/home/u/.cache/uv")
    write_policy(tmp_path)
    return Project(tmp_path)


def write_policy(root: Path, **over) -> None:
    cfg = {"max_mcp_calls_per_round": 5, "max_mcp_calls_per_run": 20, **over}
    (root / "config" / "scite.yml").write_text(yaml.safe_dump(cfg))


def ws(project) -> Path:
    return project.run_dir(FX)


def set_scite(project, value: str) -> None:
    path = ws(project) / "config.yml"
    data = yaml.safe_load(path.read_text())
    data["review"]["scite"] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))


class Fakes:
    """Counting fakes for the free sources; real REST/MCP clients over fake transports."""

    def __init__(self, *, crossref=None, mcp_errors=None):
        self.crossref_calls: list[str] = []
        self.epmc_calls: list[tuple] = []
        self.rest_calls: list[tuple] = []
        self.mcp_calls: list[tuple] = []
        self.crossref_answers = crossref or CROSSREF
        self.mcp_errors = list(mcp_errors or [])

    def crossref(self, doi):
        self.crossref_calls.append(doi)
        return dict(self.crossref_answers[doi], doi=doi)

    def europepmc(self, doi, terms):
        self.epmc_calls.append((doi, tuple(terms)))
        if doi == "10.5555/rbd.2021.007":
            return {"doi": doi, "status": "abstract_only", "abstract_only": True,
                    "passages": [{"quote": "Bursts of wrist movement clustered in REM sleep."}]}
        return {"doi": doi, "status": "full_text", "abstract_only": False,
                "passages": [{"quote": f"Passage of {doi} on actigraphy."}]}

    def rest_transport(self, op, params):
        self.rest_calls.append((op, tuple(params.get("dois") or [params.get("doi")])))
        if op == "tallies":
            return {d: {"tally": {"supporting": t["supporting"], "contrasting": t["contradicting"],
                                  "mentioning": t["mentioning"], "unclassified": 0,
                                  "total": t["total"]}, "citing_publications": 1}
                    for d, t in TALLIES.items() if d in params["dois"]}, len(params["dois"])
        return None, 0

    def rpc_factory(self, url, token):
        assert token == TOKEN

        def rpc(tool, args):
            self.mcp_calls.append((tool, dict(args)))
            if self.mcp_errors:
                err = self.mcp_errors.pop(0)
                if err is not None:
                    raise err
            payload = GRAPH if tool == "citation_graph" else SEARCH
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        return rpc

    def factory(self, policy, ledger, cache):
        public = PublicClient(policy, cache, ledger, sleep=lambda s: None)
        public._transport = self.rest_transport
        client = smcp.MCPClient(policy, cache, ledger, rpc_factory=self.rpc_factory,
                                environ=HOST_ENV, sleep=lambda s: None, jitter=lambda: 0.0)
        return evidence.Sources(
            crossref=self.crossref,
            paper=lambda doi, r: public.paper(doi, round=r),
            tallies=lambda dois, r: public.tallies(dois, round=r),
            europepmc=self.europepmc,
            mcp=lambda tool, args, r: client.call(tool, args, round=r).value,
        )

    @property
    def total(self) -> int:
        return (len(self.crossref_calls) + len(self.epmc_calls) + len(self.rest_calls)
                + len(self.mcp_calls))


def build(project, round_=1, fakes=None, **kw):
    fakes = fakes or Fakes()
    out = evidence.build(project, FX, round_, environ=HOST_ENV,
                         sources_factory=fakes.factory, **kw)
    return out, fakes


def ledger_rows(project):
    path = ws(project) / "scite" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- plan ---------------------------------------------------------------------------------------


def test_fixture_plan_free_sources_cost_no_mcp_and_at_most_three_mcp(project):
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    by_source = {}
    for r in p.requests:
        by_source.setdefault(r.source, []).append(r)
    # metadata, tallies, support: free sources only
    assert sorted(r.sends["doi"] for r in by_source["crossref"]) == DOIS
    assert [r.sends["dois"] for r in by_source["scite_public"]] == [DOIS]   # one bulk request
    assert sorted(r.sends["doi"] for r in by_source["europepmc"]) == DOIS
    assert all(r.kind is None for s in ("crossref", "scite_public", "europepmc")
               for r in by_source[s])
    mcp = by_source["scite_mcp"]
    core = [r for r in mcp if r.kind != "followup"]
    assert sorted(r.kind for r in core) == ["contested", "novelty", "topic"]
    assert len(core) <= 3
    # the reviewer follow-up (round-1 evidence pass) is planned separately, within the cap
    assert [r.kind for r in mcp if r.kind == "followup"] == ["followup"]
    topic = next(r for r in mcp if r.kind == "topic")
    assert topic.sends == {"term": "Wearable actigraphy screening for REM sleep behaviour "
                                   "disorder", "limit": 20}
    contested = next(r for r in mcp if r.kind == "contested")
    assert contested.op == "citation_graph" and contested.conditional
    assert contested.sends["seed_candidates"] == DOIS
    assert p.to_json()["counts"]["mcp"] == 4


def test_plan_json_holds_only_what_leaves_the_machine(project):
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    text = json.dumps(p.to_json())
    assert "drafts/" not in text and "main.tex" not in text
    assert "precede dream enactment" not in text          # sentences never, key terms only


def test_followups_are_deduplicated_capped_and_leak_checked(project):
    rd = ws(project) / "review" / "round-1"
    extra = [{"query": "wearable screening isolated RBD", "reason": "dup of the fixture one"},
             {"query": "sensitivity n = 43 patients p<.001", "reason": "has numbers"},
             {"dois": ["10.5555/../../etc/passwd"], "reason": "path"}]
    for name in ("agy-on-claude-paper.evidence.json", "claude-paper-on-codex-paper.evidence.json"):
        data = json.loads((rd / name).read_text())
        data["followup_requests"] = extra[:2] if name.startswith("agy") else \
            [{"query": "actigraphy validation polysomnography", "reason": "x"},
             {"query": "isolated RBD conversion wearables", "reason": "y"}]
        (rd / name).write_text(json.dumps(data))
    (rd / "codex-paper-on-agy.evidence.json").write_text(json.dumps(
        {**json.loads((rd / "codex-paper-on-agy.evidence.json").read_text()),
         "followup_requests": [extra[2]]}))
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    follow = [r for r in p.requests if r.kind == "followup"]
    assert len(follow) == 2                                     # max_followups_per_round
    terms = [r.sends.get("term") for r in follow]
    assert len(set(terms)) == 2
    assert all("43" not in (t or "") and "<" not in (t or "") for t in terms)
    assert p.dropped                                  # the rest is reported
    assert any(r["reason"] == "not a safe DOI" for r in p.refused)
    assert "passwd" not in json.dumps(p.to_json())


# --- plan-only and off: nothing is sent --------------------------------------------------------


def test_plan_only_sends_nothing_and_writes_nothing(project):
    fakes = Fakes()
    out, _ = build(project, plan_only=True, fakes=fakes)
    assert out.status == "planned" and out.plan.mcp_calls() == 4
    assert fakes.total == 0
    assert not (ws(project) / "scite").exists()
    assert not (ws(project) / "review" / "round-2").exists()
    before = (RUN / "review" / "round-1" / "evidence" / "bundle.json").read_text()
    assert (ws(project) / "review" / "round-1" / "evidence" / "bundle.json").read_text() == before


def test_cli_plan_prints_the_exact_list_and_sends_nothing(project, monkeypatch):
    monkeypatch.setenv("SCITE_TOKEN", TOKEN)
    res = CliRunner().invoke(review, ["evidence", FX, "--round", "1", "--plan", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["counts"]["mcp"] == 4
    assert {r["source"] for r in data["requests"]} == {"crossref", "scite_public", "europepmc",
                                                        "scite_mcp"}
    res = CliRunner().invoke(review, ["evidence", FX, "--round", "1", "--plan"])
    assert "nothing has been sent" in res.output and "citation_graph" in res.output
    assert not (ws(project) / "scite").exists()


@pytest.mark.parametrize("how", ["run-setting", "no-config"])
def test_scite_off_sends_nothing_and_writes_no_bundle(project, how):
    if how == "run-setting":
        set_scite(project, "off")
    else:
        (project.root / "config" / "scite.yml").unlink()
    shutil.rmtree(ws(project) / "review" / "round-1" / "evidence")
    for plan_only in (False, True):
        out, fakes = build(project, plan_only=plan_only)
        assert out.status == "off" and out.scite_status == "off" and out.reason
        assert fakes.total == 0
    assert not (ws(project) / "review" / "round-1" / "evidence").exists()
    assert not (ws(project) / "scite").exists()


def test_cli_off_exits_zero_with_notice(project, monkeypatch):
    set_scite(project, "off")
    res = CliRunner().invoke(review, ["evidence", FX, "--round", "1"])
    assert res.exit_code == 0 and "nothing sent" in res.output


# --- consent and dispatch --------------------------------------------------------------------


def test_ask_without_consent_is_refused_before_any_call(project):
    set_scite(project, "ask")
    with pytest.raises(evidence.ConsentError):
        build(project)
    assert not (ws(project) / "scite").exists()


def test_ask_with_a_human_consent_gate_builds(project):
    set_scite(project, "ask")
    g = gates.open_gate(project, ws(project), "scite-consent", "send the plan?", ["yes", "no"])
    gates.answer(project, ws(project), g["id"], "yes", actor="human")
    out, _ = build(project)
    assert out.status == "built"


def test_ask_with_a_no_answer_is_refused(project):
    set_scite(project, "ask")
    g = gates.open_gate(project, ws(project), "scite-consent", "send the plan?", ["yes", "no"])
    gates.answer(project, ws(project), g["id"], "no", actor="human")
    with pytest.raises(evidence.ConsentError):
        build(project)


def test_cli_consent_refusal_exits_3(project, monkeypatch):
    set_scite(project, "ask")
    monkeypatch.setenv("SCITE_TOKEN", TOKEN)
    res = CliRunner().invoke(review, ["evidence", FX, "--round", "1"])
    assert res.exit_code == 3


def test_refused_inside_a_dispatch(project):
    fakes = Fakes()
    with pytest.raises(smcp.DispatchRefused):
        evidence.build(project, FX, 1, environ=DISPATCH_ENV, sources_factory=fakes.factory)
    with pytest.raises(smcp.DispatchRefused):
        evidence.build(project, FX, 1, plan_only=True, environ=DISPATCH_ENV)
    assert fakes.total == 0


# --- execution --------------------------------------------------------------------------------


def test_build_writes_a_valid_bundle_and_calls_match_the_ledger(project):
    out, fakes = build(project)
    assert out.status == "built" and out.scite_status == "ok"
    ev = ws(project) / "review" / "round-1" / "evidence"
    bundle = json.loads((ev / "bundle.json").read_text())
    evidence.validate(bundle)
    # MCP: contested + topic + novelty + 1 follow-up; free sources need none
    tools = [t for t, _ in fakes.mcp_calls]
    assert tools.count("citation_graph") == 1 and len(tools) == 4
    graph_args = next(a for t, a in fakes.mcp_calls if t == "citation_graph")
    # contrasting share: 2019.001 6/50 > 2020.014 1/15 > 2022.003 1/40; 2021.007 none
    assert graph_args["seeds"] == ["10.5555/rbd.2019.001", "10.5555/rbd.2020.014",
                                   "10.5555/rbd.2022.003"]
    assert graph_args["direction"] == "in" and graph_args["include_snippets"] is True
    assert len(fakes.rest_calls) == 1                      # one bulk tallies request
    calls = json.loads((ev / "calls.json").read_text())
    rows = ledger_rows(project)
    assert calls["ledger"] == rows
    assert bundle["calls"]["mcp"] == sum(1 for r in rows if r["source"] == "mcp"
                                         and not r["cached"]) == 4
    assert bundle["calls"]["rest"] == sum(1 for r in rows if r["source"] == "rest") == 1
    assert bundle["calls"]["skipped"] == []
    assert TOKEN not in (ev / "calls.json").read_text() + (ev / "bundle.json").read_text()
    kinds = {f["kind"] for f in bundle["flags"]}
    assert {"retracted", "corrected", "contested", "abstract_only", "missing_recent",
            "novelty_hit"} <= kinds
    retracted = [f for f in bundle["flags"] if f["kind"] == "retracted"]
    assert retracted == [{"id": "F1", "kind": "retracted", "blocker": True,
                          "detail": retracted[0]["detail"], "doi": "10.5555/rbd.2020.014",
                          "key": "novak2020"}]
    assert bundle["contested"] == [{"doi": "10.5555/rbd.2019.001", "statements": [
        {"citing_doi": "10.5555/rbd.2023.042",
         "quote": "In contrast to Smith et al., actigraphy alone did not separate RBD.",
         "stance": "contrasting"}]}]
    assert [s["kind"] for s in bundle["searches"]] == ["topic", "novelty", "followup"]
    assert [r["doi"] for r in bundle["searches"][0]["results"]] == ["10.5555/rbd.2024.021"]
    novelty = bundle["searches"][1]["results"][0]
    assert novelty["claims"] == ["C2"]
    built = events.read(ws(project), types=("review.evidence_built",))
    assert len(built) == 1 and built[0]["data"]["scite_status"] == "ok"


def test_unchanged_round_two_costs_nothing(project):
    build(project)
    rows = len(ledger_rows(project))
    out, fakes = build(project, round_=2)
    assert fakes.total == 0
    assert len(ledger_rows(project)) == rows
    b1 = json.loads((ws(project) / "review" / "round-1" / "evidence" / "bundle.json").read_text())
    b2 = out.bundle
    assert b2["calls"] == {"mcp": 0, "rest": 0, "cached": 0, "skipped": []}
    for key in ("references", "claims", "contested", "flags", "manuscript_hash"):
        assert b2[key] == b1[key], key
    # round 2 has no reviewer follow-up yet, so only the core searches carry over
    assert [s["kind"] for s in b2["searches"]] == ["topic", "novelty"]


def test_round_two_requeries_only_the_changed_claim(project):
    build(project)
    draft = ws(project) / "manuscript" / "drafts" / "agy" / "main.tex"
    draft.write_text(draft.read_text().replace(
        "Wrist movement bursts precede dream enactment",
        "Wrist movement bursts predict dream enactment episodes"))
    out, fakes = build(project, round_=2)
    assert fakes.crossref_calls == [] and fakes.rest_calls == [] and fakes.mcp_calls == []
    assert [d for d, _ in fakes.epmc_calls] == ["10.5555/rbd.2022.003"]


def test_budget_boundary_mid_plan_skips_the_rest_and_marks_quota(project):
    write_policy(project.root, max_mcp_calls_per_round=2)
    out, fakes = build(project)
    assert out.status == "built" and out.scite_status == "quota"
    assert len(fakes.mcp_calls) == 2
    skipped = out.bundle["calls"]["skipped"]
    assert [s["reason"] for s in skipped] == ["budget", "budget"]
    assert out.bundle["scite_status"] == "quota"
    evidence.validate(out.bundle)
    assert sum(1 for r in ledger_rows(project) if r["source"] == "mcp") == 2


def test_quota_error_mid_call_stops_mcp_and_is_reported_once(project):
    err = smcp.RPCError("Usage quota exceeded for your plan.", code=-32000)
    fakes = Fakes(mcp_errors=[None, err])
    out, _ = build(project, fakes=fakes)
    assert out.scite_status == "quota"
    assert len(fakes.mcp_calls) == 2                       # no retry after QUOTA
    assert [s["reason"] for s in out.bundle["calls"]["skipped"]] == ["quota"] * 3
    assert len(events.read(ws(project), types=("scite.unavailable",))) == 1


def test_no_token_builds_free_evidence_and_marks_mcp_unavailable(project):
    fakes = Fakes()
    out = evidence.build(project, FX, 1, environ={"UV_CACHE_DIR": "/home/u/.cache/uv"},
                         sources_factory=fakes.factory)
    assert out.scite_status == "unavailable"
    assert fakes.mcp_calls == []
    assert {s["reason"] for s in out.bundle["calls"]["skipped"]} == {"policy"}
    assert out.bundle["references"][0]["tally"]["contrasting"] == 6


def test_unresolvable_and_malformed_dois_are_blocker_flags(project):
    bib = ws(project) / "manuscript" / "references.bib"
    bib.write_text(bib.read_text() + "@article{bad2020,\n  doi = {not-a-doi}\n}\n")
    answers = dict(CROSSREF)
    answers["10.5555/rbd.2021.007"] = {"status": "unresolvable", "reason": "not registered"}
    out, _ = build(project, fakes=Fakes(crossref=answers))
    blockers = [(f["kind"], f.get("doi"), f.get("key")) for f in out.bundle["flags"]
                if f["blocker"]]
    assert ("unresolvable", "10.5555/rbd.2021.007", "lee2021") in blockers
    assert ("unresolvable", None, "bad2020") in blockers
    assert ("retracted", "10.5555/rbd.2020.014", "novak2020") in blockers


def test_crossref_outage_falls_back_to_scite_papers_never_a_blocker(project):
    class Down(Fakes):
        def crossref(self, doi):
            self.crossref_calls.append(doi)
            raise OSError("down")

        def rest_transport(self, op, params):
            if op == "paper":
                self.rest_calls.append((op, (params["doi"],)))
                return ({"doi": params["doi"], "title": "T", "year": 2020,
                         "retracted": params["doi"] == "10.5555/rbd.2020.014"}, 1)
            return super().rest_transport(op, params)

    out, fakes = build(project, fakes=Down())
    status = {r["doi"]: r["status"] for r in out.bundle["references"]}
    assert status["10.5555/rbd.2020.014"] == "retracted"
    assert status["10.5555/rbd.2019.001"] == "ok"
    assert not [f for f in out.bundle["flags"] if f["kind"] == "unresolvable"]


def test_cli_build_reports_counts(project, monkeypatch):
    fakes = Fakes()
    monkeypatch.setattr(evidence, "default_sources", fakes.factory)
    monkeypatch.setenv("SCITE_TOKEN", TOKEN)
    res = CliRunner().invoke(review, ["evidence", FX, "--round", "1"])
    assert res.exit_code == 0, res.output
    assert "written: workspace/fixture-run/review/round-1/evidence/bundle.json" in res.output
    assert "4 MCP" in res.output


# --- scite_status (service contract, filled in M1) -------------------------------------------


def test_scite_status_configured_mcp_tier_with_day_quota_and_last_error(project, monkeypatch):
    from scieflow.core.review import service

    write_policy(project.root, max_mcp_calls_per_day=10)
    monkeypatch.setenv("SCITE_TOKEN", TOKEN)
    err = smcp.RPCError("Server error", status=503)
    fakes = Fakes(mcp_errors=[None, err, err, err])
    build(project, fakes=fakes)
    got = service.scite_status(project)
    assert got["configured"] is True and got["tier"] == "mcp" and got["reason"] is None
    used = sum(1 for r in ledger_rows(project) if r["source"] == "mcp")
    assert got["quota_remaining"] == 10 - used
    assert got["last_error"]["source"] == "mcp" and got["last_error"]["error"] == "TRANSIENT"
    assert TOKEN not in json.dumps(got)


def test_scite_status_public_tier_without_token(project, monkeypatch):
    from scieflow.core.review import service

    monkeypatch.delenv("SCITE_TOKEN", raising=False)
    got = service.scite_status(project)
    assert got["configured"] is True and got["tier"] == "public"
    assert "SCITE_TOKEN" in got["reason"] and got["quota_remaining"] is None


def test_scite_status_not_configured_and_invalid_config(project):
    from scieflow.core.review import service

    (project.root / "config" / "scite.yml").write_text("bogus_key: 1\n")
    got = service.scite_status(project)
    assert got["configured"] is False and "unknown key" in got["reason"]
    (project.root / "config" / "scite.yml").unlink()
    got = service.scite_status(project)
    assert got == {"configured": False, "tier": None, "quota_remaining": None,
                   "last_error": None, "reason": got["reason"]}
    assert "config/scite.yml not found" in got["reason"]


def test_research_scite_plan_is_the_review_evidence_plan(project, monkeypatch):
    from scieflow.research.cli import research

    monkeypatch.setenv("SCITE_TOKEN", TOKEN)
    a = CliRunner().invoke(research, ["scite", "plan", FX, "--round", "1", "--json"])
    b = CliRunner().invoke(review, ["evidence", FX, "--round", "1", "--plan", "--json"])
    assert a.exit_code == 0 and a.output == b.output
    assert not (ws(project) / "scite").exists()


# --- review findings (ticket 10) ---------------------------------------------------------------


def test_one_mcp_call_per_kind_even_with_extra_run_prompts(project):
    (ws(project) / "review" / "prompts.yml").write_text(yaml.safe_dump({
        "version": 1, "prompts": [{
            "id": "extra-topic", "stage": "review", "instruction": "More on {{topic}}.",
            "queries": [{"source": "scite_mcp", "tool": "search_literature",
                         "template": "{{topic}} review", "limit": 5},
                        {"source": "scite_mcp", "tool": "search_literature",
                         "template": "{{claim}}", "limit": 5}]}]}))
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    kinds = [r.kind for r in p.requests if r.source == "scite_mcp"]
    assert sorted(k for k in kinds if k != "followup") == ["contested", "novelty", "topic"]
    assert sum(1 for d in p.dropped if d["reason"].startswith("one scite MCP call per")) == 2


def test_a_disallowed_tool_skips_only_its_own_calls(project):
    write_policy(project.root, allowed_tools=["search_literature"])
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    assert "citation_graph" not in [r.op for r in p.requests]        # the plan is exact
    out, fakes = build(project)
    assert [t for t, _ in fakes.mcp_calls] == ["search_literature"] * 3
    assert [s["reason"] for s in out.bundle["calls"]["skipped"]] == ["policy"]
    assert out.scite_status == "unavailable"


def test_public_tier_plan_lists_no_mcp_request(project):
    p = evidence.plan(ws(project), 1, pol.load(project.root, {}), slug=FX)
    assert p.mcp_calls() == 0
    assert {u["reason"] for u in p.unsent} == {"policy"} and len(p.unsent) == 4
    data = p.to_json()
    assert data["counts"]["mcp"] == 0 and data["unsent"] and data["notes"]


def test_crossref_outage_fallback_is_reused_in_an_unchanged_round_two(project):
    class Down(Fakes):
        def crossref(self, doi):
            self.crossref_calls.append(doi)
            raise OSError("down")

        def rest_transport(self, op, params):
            if op == "paper":
                self.rest_calls.append((op, (params["doi"],)))
                return ({"doi": params["doi"], "title": "T", "year": 2020, "retracted": False}, 1)
            return super().rest_transport(op, params)

    build(project, fakes=Down())
    rows = len(ledger_rows(project))
    _, fakes = build(project, round_=2, fakes=Down())
    assert fakes.total == 0 and len(ledger_rows(project)) == rows


def test_a_forged_consent_gate_without_its_event_is_ignored(project):
    set_scite(project, "ask")
    gdir = ws(project) / "gates"
    gdir.mkdir(exist_ok=True)
    (gdir / "forged.json").write_text(json.dumps({
        "id": "forged", "kind": "scite-consent", "question": "q", "options": ["yes", "no"],
        "state": "answered", "answer": "yes", "answered_by": "human", "requires_human": True}))
    with pytest.raises(evidence.ConsentError):
        build(project)
    assert not (ws(project) / "scite").exists()


def test_reused_followups_do_not_count_against_the_cap(project):
    build(project)                                    # round 1 ran the fixture follow-up
    rd = ws(project) / "review" / "round-1"
    data = json.loads((rd / "agy-on-claude-paper.evidence.json").read_text())
    data["followup_requests"] = [{"query": "actigraphy validation polysomnography",
                                  "reason": "new"}]
    (rd / "agy-on-claude-paper.evidence.json").write_text(json.dumps(data))
    data = json.loads((rd / "codex-paper-on-agy.evidence.json").read_text())
    data["followup_requests"] = [{"query": "isolated RBD conversion wearables", "reason": "new"}]
    (rd / "codex-paper-on-agy.evidence.json").write_text(json.dumps(data))
    p = evidence.plan(ws(project), 1, pol.load(project.root, HOST_ENV), slug=FX)
    assert len([r for r in p.requests if r.kind == "followup"]) == 2
    assert p.dropped == []


# --- quota exhaustion and backfill (ticket 12, plan T2.6) ---------------------------------------


def quota_build(project, keep_matrix=False):
    """Round 1 built into a quota stop; by default before any matrix exists."""
    err = smcp.RPCError("Usage quota exceeded for your plan.", code=-32000)
    if not keep_matrix:
        for name in ("matrix.json", "matrix.md"):
            (ws(project) / "review" / "round-1" / name).unlink(missing_ok=True)
    return build(project, fakes=Fakes(mcp_errors=[None, err]))


def test_quota_mid_round_marks_matrix_rows_and_the_round_completes(project):
    from scieflow.core.review import matrix as mx

    out, _ = quota_build(project, keep_matrix=True)
    assert out.status == "built"
    sk = mx.skeleton(ws(project), 1)
    assert sk["scite_status"] == "quota"
    rd = ws(project) / "review" / "round-1"
    data = json.loads((rd / "matrix.json").read_text())
    data.pop("bundle_hash", None)
    (rd / "matrix.json").write_text(json.dumps(data))
    with pytest.raises(mx.MatrixError, match="scite_status ok does not match the round's quota"):
        mx.check(ws(project), 1)


def test_budget_exhaustion_notifies_once_per_run(project):
    write_policy(project.root, max_mcp_calls_per_round=2)
    build(project)
    build(project, round_=2)
    got = events.read(ws(project), types=("scite.unavailable",))
    assert len(got) == 1 and got[0]["data"]["reason"] == "budget"


def test_backfill_fills_only_the_missing_parts(project):
    quota_build(project)
    before = len(ledger_rows(project))
    fakes = Fakes()
    out = evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=fakes.factory)
    assert out.status == "built" and out.scite_status == "ok"
    assert fakes.crossref_calls == [] and fakes.rest_calls == [] and fakes.epmc_calls == []
    assert len(fakes.mcp_calls) == 3                     # the one that failed and the two skipped
    # the contested-sources graph succeeded before the quota error: not asked again
    assert [tool for tool, _ in fakes.mcp_calls] == ["search_literature"] * 3
    assert out.bundle["calls"]["skipped"] == []
    assert sum(1 for r in ledger_rows(project)[before:] if r["source"] == "mcp") == 3
    ev = events.read(ws(project), types=("review.evidence_built",))
    assert ev[-1]["data"]["backfill"] is True and ev[-1]["data"]["scite_status"] == "ok"


def test_backfill_stays_within_the_budget(project):
    quota_build(project)
    used = sum(1 for r in ledger_rows(project) if r["source"] == "mcp")
    write_policy(project.root, max_mcp_calls_per_round=used + 1)
    fakes = Fakes()
    out = evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=fakes.factory)
    assert len(fakes.mcp_calls) == 1
    assert out.scite_status == "quota"
    assert [s["reason"] for s in out.bundle["calls"]["skipped"]] == ["budget", "budget"]


def test_backfill_refuses_a_round_with_nothing_missing(project):
    build(project)
    with pytest.raises(evidence.BackfillError, match="nothing to backfill"):
        evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=Fakes().factory)


def test_backfill_refuses_a_round_without_a_bundle(project):
    with pytest.raises(evidence.BackfillError, match="no evidence bundle"):
        evidence.backfill(project, FX, 2, environ=HOST_ENV, sources_factory=Fakes().factory)


def test_backfill_refuses_inside_a_dispatch(project):
    quota_build(project)
    fakes = Fakes()
    with pytest.raises(pol.PolicyError):
        evidence.backfill(project, FX, 1, environ=DISPATCH_ENV, sources_factory=fakes.factory)
    assert fakes.total == 0


def test_backfill_cli(project, monkeypatch):
    from scieflow.research.cli import research

    res = CliRunner().invoke(research, ["scite", "backfill", FX, "--round", "2"])
    assert res.exit_code != 0 and "no evidence bundle" in res.output


def test_backfill_refuses_a_changed_manuscript(project):
    quota_build(project)
    draft = ws(project) / "manuscript" / "drafts" / "agy" / "main.tex"
    draft.write_text(draft.read_text() + "\n% edited\n")
    fakes = Fakes()
    with pytest.raises(evidence.BackfillError, match="manuscript changed"):
        evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=fakes.factory)
    assert fakes.total == 0


def test_backfill_refuses_a_round_with_a_matrix_unless_forced(project):
    quota_build(project, keep_matrix=True)
    rd = ws(project) / "review" / "round-1"
    assert (rd / "matrix.json").is_file()                 # the fixture's matrix
    fakes = Fakes()
    with pytest.raises(evidence.BackfillError, match="matrix"):
        evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=fakes.factory)
    assert fakes.total == 0
    out = evidence.backfill(project, FX, 1, environ=HOST_ENV, sources_factory=fakes.factory,
                            force=True)
    assert out.status == "built" and len(fakes.mcp_calls) == 3
