"""Dry-run the gap-discovery mechanical pipeline with 3 stub agents.
No tokens, no network. Judgment phases are represented by their file
contracts, mirroring tests/test_e2e_stub.py."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = ["stub_a", "stub_b", "stub_c"]

MANIFEST = """\
package: 2026-07-e2e-gaps
delivered: 2026-07-08
processing: processing.md
artifacts:
  - id: tbl-metrics
    file: results/metrics.csv
    kind: table
    description: stub metrics table
"""


def sh(argv, cwd):
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    assert proc.returncode == 0, f"{argv}: {proc.stdout}{proc.stderr}"
    return proc


def make_root(tmp_path: Path):
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    agents_yml = "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1}}\n' for a in AGENTS
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(agents_yml)
    ws = tmp_path / "workspace" / "2026-07-e2e-gaps"
    for d in ("inputs/results", "prompts", "logs", "findings", "gaps",
              "debate/round-0", "debate/round-1", "report"):
        (ws / d).mkdir(parents=True)
    return tmp_path, ws


def test_gap_discovery_pipeline(tmp_path):
    root, ws = make_root(tmp_path)
    rel = ws.relative_to(root)

    # Phase 1: intake — manifest + processing description, validated
    (ws / "inputs" / "results" / "metrics.csv").write_text("model,acc\nstub,0.9\n")
    (ws / "inputs" / "processing.md").write_text("Processed by stub pipeline.\n")
    (ws / "inputs" / "manifest.yml").write_text(MANIFEST)
    sh([sys.executable, "-m", "scieflow.research.validate",
        str(ws / "inputs" / "manifest.yml"), "--schema", "manifest"], cwd=root)

    # Phase 2: literature grounding fan-out (findings kind, no cross-review)
    for a in AGENTS:
        prompt = ws / "prompts" / f"search-{a}.md"
        prompt.write_text(f"search task\noutput: {rel}/findings/{a}.json\nkind: findings\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(prompt),
            str(ws / "logs" / f"search-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "findings" / f"{a}.json")], cwd=root)

    # Phase 3: gap-analysis fan-out
    for a in AGENTS:
        prompt = ws / "prompts" / f"gaps-{a}.md"
        prompt.write_text(f"gap task\noutput: {rel}/gaps/{a}.json\nkind: gaps\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(prompt),
            str(ws / "logs" / f"gaps-{a}.log")], cwd=root)
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "gaps" / f"{a}.json"), "--schema", "gaps"], cwd=root)

    # Phase 4: debate — round 0 propose + round 1 discuss, then adjudication
    for a in AGENTS:
        p0 = ws / "prompts" / f"debate-propose-{a}.md"
        p0.write_text(f"propose\noutput: {rel}/debate/round-0/{a}.md\nkind: perspective\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(p0),
            str(ws / "logs" / f"debate-0-{a}.log")], cwd=root)
        p1 = ws / "prompts" / f"debate-round-1-{a}.md"
        p1.write_text(f"discuss\noutput: {rel}/debate/round-1/{a}.md\nkind: debate-response\n")
        sh([sys.executable, "-m", "scieflow.core.agent_run", a, str(p1),
            str(ws / "logs" / f"debate-1-{a}.log")], cwd=root)
    assert len(list((ws / "debate" / "round-0").glob("*.md"))) == 3
    assert len(list((ws / "debate" / "round-1").glob("*.md"))) == 3
    (ws / "debate" / "round-1" / "adjudication.md").write_text(
        "| item | status | note |\n|---|---|---|\n| stub_a/P1 | consensus | |\n"
    )

    # Phase 5: synthesize file contract
    merged = json.loads((ws / "gaps" / "stub_a.json").read_text())
    merged["agent"] = "consensus"
    (ws / "report" / "hypotheses.json").write_text(json.dumps(merged, indent=2))
    sh([sys.executable, "-m", "scieflow.research.validate",
        str(ws / "report" / "hypotheses.json"), "--schema", "gaps"], cwd=root)
    (ws / "report" / "gaps.md").write_text("# Gap Report: stub\n")
    (ws / "report" / "selected_dois.txt").write_text("10.0000/stub.1\n")


# --- the Robin / Co-Scientist pipeline end to end (ticket 16, spec §7) ---------------------------

import shutil  # noqa: E402

import pytest  # noqa: E402
import yaml  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from scieflow.core import gates  # noqa: E402
from scieflow.core.project import Project  # noqa: E402
from scieflow.integrations.scite import mcp as smcp  # noqa: E402
from scieflow.research.cli import research  # noqa: E402
from scieflow.research.lib import http as http_lib  # noqa: E402
from scieflow.research.discovery import pipeline  # noqa: E402

GAP_SLUG = "2026-10-e2e-robin"
TOKEN = "sk-e2e...fake"
HOST_ENV = {"SCITE_TOKEN": TOKEN, "UV_CACHE_DIR": "/home/u/.cache/uv"}
FAMILIES = {"stub_a": "claude", "stub_b": "openai", "stub_c": "gemini"}
GRAPH = {"seeds": ["10.1000/seed.1"], "papers": {"10.1000/seed.1": {"title": "Seed study"}},
         "edges": [{"s": "10.1000/citing.1", "t": "10.1000/seed.1", "type": "contrasting",
                    "snippets": [{"text": "In contrast to the seed study, we found no "
                                          "association with olfactory loss."}]}]}
SEARCH = {"hits": [{"doi": "10.1000/open.1",
                    "title": "Open problems in prodromal synucleinopathy",
                    "abstract": "Conversion rates are well described. Whether olfactory loss "
                                "precedes motor signs remains unclear in prospective work."}]}


class CountingMCP:
    def __init__(self):
        self.calls: list[tuple] = []

    def factory(self, policy, ledger, cache):
        def rpc_factory(url, token):
            assert token == TOKEN

            def rpc(tool, args):
                self.calls.append((tool, dict(args)))
                payload = GRAPH if tool == "citation_graph" else SEARCH
                return {"content": [{"type": "text", "text": json.dumps(payload)}]}
            return rpc
        client = smcp.MCPClient(policy, cache, ledger, rpc_factory=rpc_factory,
                                environ=HOST_ENV, sleep=lambda s: None, jitter=lambda: 0.0)
        return lambda tool, args, r: client.call(tool, args, round=r).value


@pytest.fixture
def robin(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network attempted")
    monkeypatch.setattr(smcp, "sdk_rpc", boom)
    monkeypatch.setattr(http_lib, "send", boom)
    fake = CountingMCP()
    monkeypatch.setattr(pipeline, "_default_mcp_factory", fake.factory)
    fake.rest = []

    def rest_factory(policy, ledger, cache):
        def tallies(dois, r):
            fake.rest.append(list(dois))
            return {d: {"tally": {"supporting": 1, "contrasting": 1, "mentioning": 4,
                                  "unclassified": 0, "total": 6}} for d in dois}
        return tallies
    monkeypatch.setattr(pipeline, "_default_rest_factory", rest_factory)
    stub = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "defaults.yml", tmp_path / "config" / "defaults.yml")
    (tmp_path / "config" / "agents.yml").write_text("agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1, tier: primary, '
        f"model_family: {fam}}}\n" for a, fam in FAMILIES.items()))
    (tmp_path / "config" / "scite.yml").write_text(yaml.safe_dump(
        {"max_mcp_calls_per_round": 5, "max_mcp_calls_per_run": 20}))
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    for k, v in HOST_ENV.items():
        monkeypatch.setenv(k, v)
    return Project(tmp_path), fake


def cli(*args, code=0):
    res = CliRunner().invoke(research, list(args))
    assert res.exit_code == code, f"{args}: exit {res.exit_code}\n{res.output}{res.exception!r}"
    return res.output


def dispatch(root, agent, prompt: Path, log: Path, role=None):
    sh([sys.executable, "-m", "scieflow.core.agent_run", *(["--role", role] if role else []),
        agent, str(prompt), str(log)], cwd=root)


def test_robin_gap_discovery_end_to_end_within_the_scite_budget(robin):
    project, fake = robin
    root = project.root
    ws = project.run_dir(GAP_SLUG)
    rel = ws.relative_to(root)
    for d in ("inputs/results", "prompts", "logs", "findings", "gaps", "hypotheses",
              "debate/round-0", "debate/round-1", "reflection", "discovery", "report"):
        (ws / d).mkdir(parents=True)
    (ws / "config.yml").write_text(yaml.safe_dump({
        "assignments": {"research.gap-analysis": list(FAMILIES),
                        "research.debate": list(FAMILIES)},
        "review": {"scite": "approved"}}, sort_keys=False))
    (ws / "status.yml").write_text(yaml.safe_dump(
        {"workflow": "gap-discovery", "slug": GAP_SLUG, "phase": "intake", "phases": {}}))

    # Phase 1-2: intake + literature grounding
    (ws / "inputs" / "results" / "metrics.csv").write_text("model,acc\nstub,0.9\n")
    (ws / "inputs" / "processing.md").write_text("Processed by stub pipeline.\n")
    (ws / "inputs" / "manifest.yml").write_text(MANIFEST)
    for a in FAMILIES:
        p = ws / "prompts" / f"search-{a}.md"
        p.write_text(f"search task\noutput: {rel}/findings/{a}.json\nkind: findings\n")
        dispatch(root, a, p, ws / "logs" / f"search-{a}.log")

    # Phase 3, steps 1-2: evidence map + typed detectors (plan first: sends nothing)
    (ws / "discovery" / "query.yml").write_text(yaml.safe_dump({
        "topic": "olfactory loss prodromal synucleinopathy", "seeds": ["10.1000/seed.1"]}))
    plan = json.loads(cli("scite", "gaps", GAP_SLUG, "--plan"))
    assert fake.calls == [] and plan["plan"]["mcp_calls"] == 2
    cli("scite", "gaps", GAP_SLUG)
    assert [t for t, _ in fake.calls] == ["citation_graph", "search_literature"]
    assert fake.rest == [["10.1000/seed.1"]]          # public tallies of the seeds (REST)

    # detector-fed gap fan-out: two agents quote their detector's evidence, one invents
    oq = json.loads((ws / "discovery" / "evidence" / "open_questions.json").read_text())
    ev = oq["gaps"][0]["evidence"][0]
    for a in FAMILIES:
        fed = (f"detector: open_questions\nquote-ref: {ev['ref']}\nquote: {ev['quote']}\n"
               if a != "stub_c" else "")
        p = ws / "prompts" / f"gaps-{a}.md"
        p.write_text(f"gap task\noutput: {rel}/gaps/{a}.json\nkind: gaps\n{fed}")
        dispatch(root, a, p, ws / "logs" / f"gaps-{a}.log", role="research.gap-analysis")
        sh([sys.executable, "-m", "scieflow.research.validate",
            str(ws / "gaps" / f"{a}.json"), "--schema", "gaps"], cwd=root)

    # step 3: verification gate
    out = cli("discover", "verify", GAP_SLUG)
    verified = json.loads((ws / "discovery" / "verified.json").read_text())
    sources = [g["source"] for g in verified["kept"]]
    assert sources == ["detector:disputes", "detector:open_questions", "stub_a/G1", "stub_b/G1"]
    assert "dropped stub_c/G1" in out

    # step 4: hypotheses from verified gaps only
    for a in FAMILIES:
        p = ws / "prompts" / f"hypotheses-{a}.md"
        p.write_text(f"hypotheses\noutput: {rel}/hypotheses/{a}.json\nkind: gaps\ngap-id: G3\n")
        dispatch(root, a, p, ws / "logs" / f"hypotheses-{a}.log")
    cli("discover", "pool", GAP_SLUG)
    pooled = json.loads((ws / "discovery" / "hypotheses.json").read_text())["hypotheses"]
    assert [(h["id"], h["author"], h["family"]) for h in pooled] == [
        ("H1", "stub_a", "claude"), ("H2", "stub_b", "openai"), ("H3", "stub_c", "gemini")]

    # step 5: debate (unchanged protocol), adjudication recorded
    for a in FAMILIES:
        for n, kind in ((0, "perspective"), (1, "debate-response")):
            p = ws / "prompts" / f"debate-{n}-{a}.md"
            p.write_text(f"debate\noutput: {rel}/debate/round-{n}/{a}.md\nkind: {kind}\n")
            dispatch(root, a, p, ws / "logs" / f"debate-{n}-{a}.log")
    cli("discover", "debate-status", GAP_SLUG, "H1=consensus", "H2=disputed")

    # step 6: blind reflection, then the free pre-check (providers off: nothing sent)
    for a in FAMILIES:
        p = ws / "prompts" / f"reflect-{a}.md"
        p.write_text(f"reflect\noutput: {rel}/reflection/{a}.md\nkind: critique\n")
        dispatch(root, a, p, ws / "logs" / f"reflect-{a}.log")

    def tournament_round(r):
        planned = json.loads(cli("discover", "precheck", GAP_SLUG, "--round", str(r), "--plan"))
        assert planned["queries"] and all(q["providers"] == [] for q in planned["queries"])
        cli("discover", "precheck", GAP_SLUG, "--round", str(r))
        sched = json.loads(cli("discover", "schedule", GAP_SLUG, "--round", str(r)) and
                           (ws / "discovery" / f"round-{r}" / "schedule.json").read_text())
        for m in sched["matches"]:
            prompt = ws / "discovery" / f"round-{r}" / "judge" / f"match-{m['match']}.md"
            assert m["judge_tier"] == "other-family"
            role = {j["agent"]: j["role"] for j in sched["judges"]}[m["judge"]]
            assert role == "research.gap-analysis"
            dispatch(root, m["judge"], prompt, ws / "logs" / f"judge-{r}-{m['match']}.log",
                     role=role)
        cli("discover", "rank", GAP_SLUG, "--round", str(r))
        cli("scite", "novelty", GAP_SLUG, "--round", str(r))
        return sched

    # steps 7-8: tournament + scite top-k
    sched1 = tournament_round(1)
    assert len(sched1["matches"]) == 3                      # all pairs, under the cap of 28
    t1 = json.loads((ws / "discovery" / "round-1" / "tournament.json").read_text())
    assert t1["top"][0] == "H1" and t1["no_verdict"] == 0

    # step 9: one evolution round, re-running 6-8 on the changed item only
    (ws / "discovery" / "evolution.json").write_text(json.dumps(
        {"H1": {"statement": "X drives Y in the stub regime within two years.",
                "rationale": "adds a time window"}}))
    cli("discover", "evolve", GAP_SLUG)
    tournament_round(2)
    free2 = json.loads((ws / "discovery" / "round-2" / "novelty-free.json").read_text())
    assert [a["id"] for a in free2["abstracts"]] == ["H1"]
    scite2 = json.loads((ws / "discovery" / "round-2" / "scite.json").read_text())
    assert set(scite2["results"]) == {"H1"}
    cli("discover", "evolve", GAP_SLUG, "--from-round", "2", code=1)   # evolution_rounds: 1

    # the whole run stayed within the gap-run scite budget
    assert 3 <= len(fake.calls) <= pipeline.GAP_RUN_MAX_MCP

    # step 10: meta-review + report with the tournament table
    cli("discover", "check", GAP_SLUG, code=1)
    table = cli("discover", "table", GAP_SLUG).strip()
    (ws / "report" / "meta-review.md").write_text(
        "# Meta-review\n\nCritiques agree the predictions need time windows.\n")
    (ws / "report" / "gaps.md").write_text(
        "# Gap Report: stub\n\n## Tournament\n\n" + table +
        "\n\n## Meta-review\n\nSee report/meta-review.md.\n")
    cli("discover", "check", GAP_SLUG)
    report = (ws / "report" / "gaps.md").read_text()
    assert "| 1 | H1 |" in report and "within two years" in report
    assert (ws / "report" / "tournament.json").is_file()

    # step 11: the user's decision gate
    cli("discover", "select", GAP_SLUG)
    cli("discover", "select", GAP_SLUG, "--read", code=1)       # not answered yet
    gate = gates.list_gates(ws, state="open")[0]
    assert gate["kind"] == "hypothesis-selection" and gate["requires_human"]
    gates.answer(project, ws, gate["id"], "H1")
    cli("discover", "select", GAP_SLUG, "--read")
    chosen = json.loads((ws / "report" / "selected_hypotheses.json").read_text())
    assert chosen["selected"] == ["H1"]


def test_gap_discovery_protocol_runs_spec_section_7_in_order():
    text = (REPO / "src" / "scieflow" / "research" / "skills" / "gap-discovery" /
            "SKILL.md").read_text()
    flat = " ".join(text.split())
    steps = ["scieflow research scite gaps <slug> --plan", "scieflow research scite gaps <slug>",
             "scieflow research discover verify <slug>", "scieflow research discover pool <slug>",
             "debate-protocol.md", "scieflow research discover debate-status <slug>",
             "kind: critique", "scieflow research discover precheck <slug> --round 1 --plan",
             "scieflow research discover schedule <slug> --round 1",
             "scieflow research discover rank <slug> --round 1",
             "scieflow research scite novelty <slug> --round 1",
             "scieflow research discover evolve <slug>", "# Meta-review",
             "scieflow research discover table <slug>",
             "scieflow research discover check <slug>",
             "scieflow research discover select <slug>"]
    pos = [flat.find(s) for s in steps]
    assert all(p >= 0 for p in pos), [s for s, p in zip(steps, pos) if p < 0]
    assert pos == sorted(pos)
    for must in ("external-sharing", "scite-consent", "hypothesis-selection", "evolution_rounds",
                 "tournament_top_k", "max_tournament_comparisons", "## Tournament",
                 "## Meta-review", "gaps_from", "never a sub-agent"):
        assert must in flat, must
