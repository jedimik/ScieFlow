"""End-to-end review pool with the stub agent (ticket 13, plan T2.10).

Drives the paper-draft Phase 4 and the paper-review loop the way the protocols
(`src/scieflow/research/skills/{paper-draft,paper-review}/SKILL.md`) tell the
coordinator to: `scieflow review assign` → pass A dispatches → `review evidence`
(which freezes pass A) → pass B dispatches → `review matrix` → coordinator
clustering → `review matrix --check` (opens the decision and blocker gates) →
answers → `review revise` → revision dispatch → `review coverage` →
`review merge-check`.

Reviews and responses come from the stub agent dispatched through
`scieflow.core.agent_run` with `--role` (so the pool must be staffed through the
role, as the protocol says). The CLI runs in-process so the evidence transports
can be replaced by counting fakes; nothing reaches the network.

Scenarios: 3 authors + an extra reviewer; the family / self switches off and
on; `scite: off`; scite consent through a human gate; quota exhausted mid-round
and backfill; a retracted DOI blocking merge until a human overrules it;
decisions filtering the revision; the single-author fallback; and paper-review
with a reviewer pool separated from the submitter by model family.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.review import evidence
from scieflow.core.review import passes as passes_mod
from scieflow.core.review import service
from scieflow.core.review.cli import review
from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite.public import PublicClient
from scieflow.research.lib import http as http_lib

REPO = Path(__file__).resolve().parents[2]
STUB_FILE = REPO / "src" / "scieflow" / "core" / "stub_agent.py"
SLUG = "2026-10-e2e-review"
TOKEN = "sk-e2e...fake"
HOST_ENV = {"SCITE_TOKEN": TOKEN, "UV_CACHE_DIR": "/home/u/.cache/uv"}
FAMILIES = {"stub_a": "claude", "stub_b": "openai", "stub_c": "gemini", "stub_r": "openai"}
AUTHORS = ["stub_a", "stub_b", "stub_c"]
DOI = "10.0000/stub.1"

BIB = """\
@article{stub,
  author = {Stub, A.},
  title = {Stub Paper One},
  journal = {Journal of Stubs},
  year = {2024},
  doi = {10.0000/stub.1},
}
"""

DRAFT = """\\documentclass{article}
\\title{Wearable actigraphy screening for REM sleep behaviour disorder}
\\begin{document}
\\maketitle
\\section{Introduction}
Wrist actigraphy has been proposed for screening \\cite{stub}.
\\section{Discussion}
To our knowledge this is the first wearable-only screening study \\cite{stub}.
\\end{document}
"""


# --- fakes ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a real transport was reached")
    monkeypatch.setattr(http_lib, "send", boom)
    monkeypatch.setattr(smcp, "sdk_rpc", boom)


class Fakes:
    """Counting fakes for the free sources; real REST / MCP clients over fake transports."""

    def __init__(self, *, retracted=False, mcp_errors=None):
        self.retracted = retracted
        self.mcp_errors = list(mcp_errors or [])
        self.calls: list[tuple] = []
        self.mcp_calls: list[tuple] = []

    def crossref(self, doi):
        self.calls.append(("crossref", doi))
        return {"doi": doi, "status": "retracted" if self.retracted else "ok",
                "title": "Stub Paper One", "year": 2024}

    def europepmc(self, doi, terms):
        self.calls.append(("europepmc", doi))
        return {"doi": doi, "status": "full_text", "abstract_only": False,
                "passages": [{"quote": "Wrist actigraphy separated cases from controls."}]}

    def rest_transport(self, op, params):
        self.calls.append(("rest", op))
        if op == "tallies":
            return {d: {"tally": {"supporting": 3, "contrasting": 1, "mentioning": 9,
                                  "unclassified": 0, "total": 13}, "citing_publications": 4}
                    for d in params["dois"]}, len(params["dois"])
        return None, 0

    def rpc_factory(self, url, token):
        assert token == TOKEN

        def rpc(tool, args):
            self.mcp_calls.append((tool, dict(args)))
            if self.mcp_errors:
                err = self.mcp_errors.pop(0)
                if err is not None:
                    raise err
            payload = ({"seeds": [], "edges": [], "papers": {}} if tool == "citation_graph"
                       else {"hits": [{"doi": "10.0000/stub.9", "title": "A later stub study",
                                       "abstract": "A wearable-only screening study.",
                                       "year": 2025}]})
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


# --- project and run ------------------------------------------------------------------------


def agents_yml(families: dict[str, str]) -> str:
    # The stub is run by file path: a sandboxed child resets PYTHONPATH, so `-m scieflow...`
    # would import whatever checkout the venv's editable install points at, not this tree.
    stub = f"{sys.executable} {STUB_FILE} {{prompt}}"
    return "agents:\n" + "".join(
        f'  {a}: {{cmd: "{stub}", enabled: true, timeout_min: 1, tier: primary, '
        f"model_family: {fam}}}\n" for a, fam in families.items())


@pytest.fixture
def project(tmp_path, monkeypatch, grant_credentials, stub_child_home):
    (tmp_path / "config").mkdir()
    for name in ("defaults.yml", "scite-prompts.yml"):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    (tmp_path / "config" / "agents.yml").write_text(agents_yml(FAMILIES))
    (tmp_path / "config" / "scite.yml").write_text(yaml.safe_dump(
        {"max_mcp_calls_per_round": 5, "max_mcp_calls_per_run": 20}))
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    # K sandbox S4: stub agents are dispatched sandboxed and need a credentials entry.
    # Fixture-only: a fake 0600 token under the isolated credentials dir (tests/conftest.py).
    # config/sandbox.yml in the repo stays deny-by-default.
    grant_credentials(tmp_path, *FAMILIES)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    for key, value in HOST_ENV.items():
        monkeypatch.setenv(key, value)
    return Project(tmp_path)


def ws(project) -> Path:
    return project.run_dir(SLUG)


def make_run(project, *, workflow="paper-draft", assignments=None, review_block=None,
             authors=AUTHORS) -> Path:
    w = ws(project)
    for d in ("prompts", "logs", "review", "report", "manuscript"):
        (w / d).mkdir(parents=True, exist_ok=True)
    (w / "manuscript" / "references.bib").write_text(BIB)
    if workflow == "paper-draft":
        for a in authors:
            (w / "manuscript" / "drafts" / a).mkdir(parents=True)
            (w / "manuscript" / "drafts" / a / "main.tex").write_text(DRAFT)
    else:
        (w / "manuscript" / "sections").mkdir()
        (w / "manuscript" / "main.tex").write_text(
            DRAFT.replace("\\end{document}", "\\input{sections/results}\n\\end{document}"))
        (w / "manuscript" / "sections" / "results.tex").write_text(
            "\\section{Results}\nScreening separated the groups \\cite{stub}.\n")
    config = {"assignments": assignments or {}, "review": {"scite": "approved",
                                                           **(review_block or {})}}
    (w / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    (w / "status.yml").write_text(yaml.safe_dump(
        {"workflow": workflow, "slug": SLUG, "phase": "cross-review", "phases": {}}))
    return w


def cli(group, *args, code=0):
    res = CliRunner().invoke(group, list(args))
    assert res.exit_code == code, f"{args}: exit {res.exit_code}\n{res.output}{res.exception!r}"
    return res


def dispatch(project, role, agent, prompt: Path) -> None:
    log = ws(project) / "logs" / (prompt.stem + ".log")
    proc = subprocess.run([sys.executable, "-m", "scieflow.core.agent_run", "--role", role,
                           agent, str(prompt), str(log)],
                          capture_output=True, text=True, cwd=project.root)
    assert proc.returncode == 0, f"{agent} {prompt.name}: {proc.stdout}{proc.stderr}"


def assigned_pairs(project, n=1) -> list[tuple[str, str]]:
    data = json.loads((ws(project) / "review" / f"round-{n}" / "assignment.json").read_text())
    return [(p["reviewer"], p["author"]) for p in data["pairs"]]


def run_pass(project, pass_, n=1, role="research.draft-reviewers") -> None:
    """Write each pair's prompt with `review prompt --write`, dispatch it, check it."""
    for reviewer, author in assigned_pairs(project, n):
        cli(review, "prompt", SLUG, "--round", str(n), "--reviewer", reviewer,
            "--author", author, "--pass", pass_, "--write")
        prompt = (ws(project) / "prompts"
                  / f"review-{pass_}-{reviewer}-on-{author}-round-{n}.md")
        dispatch(project, role, reviewer, prompt)
        cli(review, "check", SLUG, "--round", str(n), "--reviewer", reviewer,
            "--author", author, "--pass", pass_)


def build_evidence(project, fakes, n=1):
    return evidence.build(project, SLUG, n, environ=HOST_ENV, sources_factory=fakes.factory)


def cluster(project, n=1, accept=lambda author, cid: True) -> dict:
    """The coordinator's clustering, done mechanically: one cluster per (author, comment
    id) over every reviewer and pass, one per evidence-bundle flag."""
    cli(review, "matrix", SLUG, "--round", str(n))
    rd = passes_mod.round_dir(ws(project), n)
    sk = json.loads((rd / "matrix.skeleton.json").read_text())
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in sk["items"]:
        groups.setdefault((item["member"]["author"], item["member"]["id"]), []).append(item)
    clusters = []
    for (author, cid), items in sorted(groups.items()):
        reviewers = {i["member"]["reviewer"] for i in items}
        clusters.append({
            "cluster": f"K{len(clusters) + 1}", "title": f"{cid} on {author}",
            "severity": items[0]["severity"], "members": [i["member"] for i in items],
            "flags": [], "agreement": "single" if len(reviewers) == 1 else "consensus",
            "evidence_for": [], "evidence_against": [], "blocker": False,
            "scite_status": sk["scite_status"],
            "recommendation": "accept" if accept(author, cid) else "reject",
            "rationale": "mechanical stub clustering"})
    for flag in sk["flags"]:
        c = {"cluster": f"K{len(clusters) + 1}", "title": f"{flag['kind']} {flag['doi']}",
             "severity": "major", "members": [], "flags": [flag["id"]], "agreement": "single",
             "evidence_for": [], "evidence_against": [], "blocker": flag["blocker"],
             "scite_status": sk["scite_status"], "recommendation": "accept",
             "rationale": flag.get("detail") or flag["kind"]}
        if flag["blocker"]:
            c["blocker_reason"] = flag["kind"]
        clusters.append(c)
    matrix = {"version": 1, "round": n, "clusters": clusters}
    if "bundle_hash" in sk:
        matrix["bundle_hash"] = sk["bundle_hash"]
    (rd / "matrix.json").write_text(json.dumps(matrix, indent=2))
    cli(review, "matrix", SLUG, "--round", str(n), "--check")
    return matrix


def open_gates(project, kind=None) -> list[dict]:
    return [g for g in gates.list_gates(ws(project), state="open")
            if kind is None or g["kind"] == kind]


def cluster_of(gate: dict) -> str:
    return gate["question"].split("cluster ")[1].split(" ")[0]


def by_title(matrix: dict) -> dict[str, str]:
    return {c["title"]: c["cluster"] for c in matrix["clusters"]}


# --- 3 authors + an extra reviewer, scite on, a retracted DOI ---------------------------------


def test_three_authors_extra_reviewer_full_round(project):
    make_run(project, assignments={
        "research.draft-authors": AUTHORS,
        "research.draft-reviewers": [*AUTHORS, "stub_r"]})

    # Assignment: every draft reviewed by 2 balanced authors (+ stub_r on all), never
    # by itself, never by its own model family (stub_r and stub_b are both openai).
    cli(review, "assign", SLUG, "--round", "1")
    pairs = assigned_pairs(project)
    for author in AUTHORS:
        reviewers = [r for r, a in pairs if a == author]
        assert author not in reviewers
        assert all(FAMILIES[r] != FAMILIES[author] for r in reviewers)
    assert ("stub_r", "stub_a") in pairs and ("stub_r", "stub_c") in pairs
    assert ("stub_r", "stub_b") not in pairs                       # same family: openai
    data = json.loads((ws(project) / "review" / "round-1" / "assignment.json").read_text())
    assert all(p["fresh_dispatch"] for p in data["pairs"])

    counts = {a: sum(1 for r, x in pairs if x == a and r != "stub_r") for a in AUTHORS}
    assert all(n >= 1 for n in counts.values())
    assert max(counts.values()) - min(counts.values()) <= 1           # balanced

    # Pass A (blind) for every pair, then the bundle (which freezes pass A first).
    res = cli(review, "prompt", SLUG, "--round", "1", "--reviewer", pairs[0][0],
              "--author", pairs[0][1], "--pass", "blind")
    assert "--role research.draft-reviewers " in res.output
    run_pass(project, "blind")
    fakes = Fakes(retracted=True)
    out = build_evidence(project, fakes)
    assert out.status == "built" and out.scite_status == "ok"
    assert len(fakes.mcp_calls) <= 3
    frozen = json.loads((ws(project) / "review" / "round-1" / "assignment.json").read_text())
    assert all(p.get("blind_sha256") for p in frozen["pairs"])

    # Pass B (evidence) for every pair.
    run_pass(project, "evidence")
    assert len(events.read(ws(project), types=("review.pass_done",))) == 2 * len(pairs)

    # Matrix: the retracted DOI is a blocker cluster; --check opens the gates.
    matrix = cluster(project)
    titles = by_title(matrix)
    blocker = titles[f"retracted {DOI}"]
    assert [b["cluster"] for b in service.blockers(project, SLUG)] == [blocker]
    assert len(events.read(ws(project), types=("review.blocker",))) == 1
    assert [cluster_of(g) for g in open_gates(project, "review-blocker")] == [blocker]
    decision_gates = {cluster_of(g): g for g in open_gates(project, "review-decision")}
    assert set(decision_gates) == set(titles.values()) - {blocker}
    cli(review, "merge-check", SLUG, code=1)

    # The user decides: accept M1 on stub_a, reject M1 on stub_b, defer every minor.
    plan = {"M1 on stub_a": "accept", "M1 on stub_b": "reject", "M1 on stub_c": "accept",
            "m1 on stub_a": "defer", "m1 on stub_b": "defer", "m1 on stub_c": "defer"}
    for title, decision in plan.items():
        gates.answer(project, ws(project), decision_gates[titles[title]]["id"], decision,
                     "human")
    assert len(events.read(ws(project), types=("review.decided",))) == len(plan)

    # Revision: stub_a gets only its accepted cluster; the blocker still reaches it as
    # an undecided blocker is not accepted. stub_b has nothing accepted → no dispatch.
    cli(review, "revise", SLUG, "--round", "1", "--author", "stub_a", "--write")
    prompt = ws(project) / "prompts" / "revise-stub_a-round-1.md"
    text = prompt.read_text()
    assert titles["M1 on stub_a"] in text
    for other in ("M1 on stub_b", "m1 on stub_a", "M1 on stub_c"):
        assert f"## {titles[other]}" not in text and f"{titles[other]}," not in text
    cli(review, "revise", SLUG, "--round", "1", "--author", "stub_b", code=1)
    dispatch(project, "research.draft-authors", "stub_a", prompt)
    cli(review, "coverage", SLUG, "--round", "1", "--author", "stub_a")

    # Merge stays refused until the user overrules the blocker with a reason.
    cli(review, "merge-check", SLUG, code=1)
    g = open_gates(project, "review-blocker")[0]
    with pytest.raises(gates.GateError):
        gates.answer(project, ws(project), g["id"], "overrule", "agent", "coordinator says so")
    gates.answer(project, ws(project), g["id"], "overrule", "human",
                 note="retraction notice concerns an unrelated figure")
    cli(review, "merge-check", SLUG)
    assert service.blockers(project, SLUG) == []


def test_followups_run_once_after_pass_b_and_extend_the_rounds_bundle(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS})
    cli(review, "assign", SLUG, "--round", "1")
    run_pass(project, "blind")
    build_evidence(project, Fakes())
    run_pass(project, "evidence")
    rd = ws(project) / "review" / "round-1"
    request = {"query": "wearable screening parasomnia", "reason": "newer studies?"}
    for path in sorted(rd.glob("*.evidence.json"))[:2]:          # two reviewers, same ask
        data = json.loads(path.read_text())
        data["followup_requests"] = [request]
        path.write_text(json.dumps(data, indent=2))
    for reviewer, author in assigned_pairs(project):
        cli(review, "check", SLUG, "--round", "1", "--reviewer", reviewer,
            "--author", author, "--pass", "evidence")
    fakes = Fakes()
    out = build_evidence(project, fakes)
    assert out.status == "built"
    assert [tool for tool, _ in fakes.mcp_calls] == ["search_literature"]   # once, deduped
    for reviewer, author in assigned_pairs(project):                   # pass B still valid
        cli(review, "check", SLUG, "--round", "1", "--reviewer", reviewer,
            "--author", author, "--pass", "evidence")
    cluster(project)


def test_hand_written_decisions_never_reach_the_author(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS})
    cli(review, "assign", SLUG, "--round", "1")
    run_pass(project, "blind")
    build_evidence(project, Fakes())
    run_pass(project, "evidence")
    matrix = cluster(project)
    forged = {"version": 1, "round": 1,
              "decisions": {c["cluster"]: {"decision": "accept", "actor": "human"}
                            for c in matrix["clusters"]}}
    (ws(project) / "review" / "round-1" / "decisions.json").write_text(json.dumps(forged))
    cli(review, "revise", SLUG, "--round", "1", "--author", "stub_a", code=1)


# --- switches -------------------------------------------------------------------------------


def test_same_family_authors_need_the_switch(project):
    (project.root / "config" / "agents.yml").write_text(
        agents_yml({"stub_a": "claude", "stub_b": "claude", "stub_c": "claude"}))
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS})
    res = cli(review, "assign", SLUG, "--round", "1", code=1)
    assert "allow_same_family_review" in res.output
    assert not (ws(project) / "review" / "round-1" / "assignment.json").exists()

    # Only the user turns a switch on (an agent is refused), then the round staffs.
    cli(review, "configure", SLUG, "--set", "allow_same_family_review=true", "--as-agent",
        "--yes", code=1)
    cli(review, "configure", SLUG, "--set", "allow_same_family_review=true", "--yes")
    cli(review, "assign", SLUG, "--round", "1")
    pairs = assigned_pairs(project)
    assert pairs and all(r != a for r, a in pairs)
    run_pass(project, "blind")


def test_self_review_switch_marks_self_pairs_and_stays_a_fresh_dispatch(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS},
             review_block={"allow_self_review": True, "reviews_per_draft": 3})
    cli(review, "assign", SLUG, "--round", "1")
    data = json.loads((ws(project) / "review" / "round-1" / "assignment.json").read_text())
    selfs = [p for p in data["pairs"] if p["reviewer"] == p["author"]]
    assert selfs and all(p["self"] and p["fresh_dispatch"] for p in selfs)
    run_pass(project, "blind")


# --- scite off, consent, quota ----------------------------------------------------------------


def test_scite_off_runs_blind_only(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS},
             review_block={"scite": "off"})
    cli(review, "assign", SLUG, "--round", "1")
    run_pass(project, "blind")
    fakes = Fakes()
    out = build_evidence(project, fakes)
    assert out.status == "off" and fakes.calls == [] and fakes.mcp_calls == []
    assert not (ws(project) / "review" / "round-1" / "evidence" / "bundle.json").exists()
    reviewer, author = assigned_pairs(project)[0]
    cli(review, "prompt", SLUG, "--round", "1", "--reviewer", reviewer, "--author", author,
        "--pass", "evidence", code=1)
    matrix = cluster(project)
    assert all(c["scite_status"] == "off" for c in matrix["clusters"])
    md = (ws(project) / "review" / "round-1" / "matrix.md").read_text()
    assert "| off |" in md
    cli(review, "merge-check", SLUG)


def test_scite_ask_needs_a_human_consent_gate(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS},
             review_block={"scite": "ask"})
    cli(review, "assign", SLUG, "--round", "1")
    run_pass(project, "blind")
    plan = cli(review, "evidence", SLUG, "--round", "1", "--plan", "--json")
    assert json.loads(plan.output)
    fakes = Fakes()
    with pytest.raises(evidence.ConsentError):
        build_evidence(project, fakes)
    assert fakes.calls == [] and fakes.mcp_calls == []
    g = gates.open_gate(project, ws(project), "scite-consent",
                        "Send this run's sanitised query plan to scite?", ["yes", "no"])
    with pytest.raises(gates.GateError):
        gates.answer(project, ws(project), g["id"], "yes", "agent", "autonomous")
    gates.answer(project, ws(project), g["id"], "yes", "human")
    assert build_evidence(project, fakes).status == "built"


def test_quota_exhausted_mid_round_completes_and_backfills(project):
    make_run(project, assignments={"research.draft-authors": AUTHORS,
                                   "research.draft-reviewers": AUTHORS})
    cli(review, "assign", SLUG, "--round", "1")
    run_pass(project, "blind")
    quota = smcp.RPCError("Usage quota exceeded for your plan.", code=-32000)
    out = build_evidence(project, Fakes(mcp_errors=[quota]))
    assert out.status == "built" and out.scite_status == "quota"
    assert len(events.read(ws(project), types=("scite.unavailable",))) == 1
    run_pass(project, "evidence")
    reviews = [json.loads(p.read_text())
               for p in (ws(project) / "review" / "round-1").glob("*.evidence.json")]
    assert reviews and all(r["scite_status"] == "quota" for r in reviews)
    # A rebuild of the quota round (e.g. for follow-ups) would retry the refused
    # calls: refused, nothing sent — backfill is the way.
    fakes = Fakes()
    with pytest.raises(evidence.QuotaRoundError, match="backfill"):
        build_evidence(project, fakes)
    assert fakes.calls == [] and fakes.mcp_calls == []
    cli(review, "evidence", SLUG, "--round", "1", code=3)
    matrix = cluster(project)
    assert all(c["scite_status"] == "quota" for c in matrix["clusters"])

    # Later, with quota back: backfill fills only what was skipped (it would
    # invalidate the matrix, so it needs --force once a matrix exists).
    fakes = Fakes()
    with pytest.raises(evidence.BackfillError, match="force"):
        evidence.backfill(project, SLUG, 1, environ=HOST_ENV, sources_factory=fakes.factory)
    out = evidence.backfill(project, SLUG, 1, environ=HOST_ENV, sources_factory=fakes.factory,
                            force=True)
    assert out.status == "built" and out.scite_status == "ok"
    assert fakes.calls == [] and 1 <= len(fakes.mcp_calls) <= 3


# --- single-author fallback ---------------------------------------------------------------------


def test_single_surviving_author_without_other_reviewers_skips_cross_review(project):
    make_run(project, assignments={"research.draft-authors": ["stub_a"]}, authors=["stub_a"])
    res = cli(review, "assign", SLUG, "--round", "1", code=1)
    assert "allow_self_review" in res.output or "allow_same_family_review" in res.output
    assert not (ws(project) / "review" / "round-1" / "assignment.json").exists()


def test_single_surviving_author_is_still_reviewed_by_the_extra_reviewer(project):
    make_run(project, assignments={"research.draft-authors": ["stub_a"],
                                   "research.draft-reviewers": ["stub_a", "stub_r"]},
             authors=["stub_a"])
    cli(review, "assign", SLUG, "--round", "1")
    assert assigned_pairs(project) == [("stub_r", "stub_a")]
    run_pass(project, "blind")


# --- paper-review: same pool and passes, submitter separated by family --------------------------


def test_paper_review_pool_reviews_the_submitters_manuscript(project):
    make_run(project, workflow="paper-review", assignments={
        "research.submitter": "stub_b",
        "research.reviewer": ["stub_a", "stub_c", "stub_r"]})
    # Left-over paper-draft drafts in the same run are not what paper-review reviews.
    stale = ws(project) / "manuscript" / "drafts" / "stub_a"
    stale.mkdir(parents=True)
    (stale / "main.tex").write_text("\\section{Results}\nDRAFT-ONLY SENTENCE \\cite{stub}.\n")
    from scieflow.core.review import claims
    assert all("drafts" not in f.as_posix() for f, _ in claims.sources(ws(project)))
    cli(review, "assign", SLUG, "--round", "1")
    pairs = assigned_pairs(project)
    # stub_r shares the submitter's model family (openai): not eligible.
    assert sorted(pairs) == [("stub_a", "stub_b"), ("stub_c", "stub_b")]

    run_pass(project, "blind", role="research.reviewer")
    reviewer, author = pairs[0]
    prompt = (ws(project) / "prompts" / f"review-blind-{reviewer}-on-{author}-round-1.md")
    text = prompt.read_text()
    assert "manuscript/drafts" not in text and "DRAFT-ONLY SENTENCE" not in text
    assert "Wrist actigraphy has been proposed" in text
    assert "Screening separated the groups" in text                  # \input'd section
    res = cli(review, "prompt", SLUG, "--round", "1", "--reviewer", reviewer, "--author",
              author, "--pass", "blind", "--write")
    assert "--role research.reviewer " in res.output

    build_evidence(project, Fakes(retracted=True))
    run_pass(project, "evidence", role="research.reviewer")
    matrix = cluster(project, accept=lambda author, cid: cid == "M1")
    titles = by_title(matrix)
    for g in open_gates(project, "review-decision"):
        cid = cluster_of(g)
        decision = "accept" if cid == titles["M1 on stub_b"] else "reject"
        gates.answer(project, ws(project), g["id"], decision, "human")
    cli(review, "revise", SLUG, "--round", "1", "--author", "stub_b", "--write")
    prompt = ws(project) / "prompts" / "revise-stub_b-round-1.md"
    revise_text = prompt.read_text()
    assert "manuscript/drafts" not in revise_text
    assert "manuscript/sections/results.tex" in revise_text          # the files to edit
    res = cli(review, "revise", SLUG, "--round", "1", "--author", "stub_b", "--write")
    assert "--role research.submitter " in res.output
    dispatch(project, "research.submitter", "stub_b", prompt)
    cli(review, "coverage", SLUG, "--round", "1", "--author", "stub_b")
    cli(review, "merge-check", SLUG, code=1)                       # retracted DOI


@pytest.mark.parametrize("how", ["pool", "matrix_override"])
def test_paper_review_submitter_never_reviews_itself(project, how):
    review_block = {"allow_self_review": True, "allow_same_family_review": True}
    if how == "pool":
        review_block["pool"] = [{"agent": "stub_a"}, {"agent": "stub_b"}]
    else:
        review_block["matrix_override"] = {"stub_b": ["stub_b"]}
    make_run(project, workflow="paper-review", review_block=review_block, assignments={
        "research.submitter": "stub_b", "research.reviewer": ["stub_a"]})
    res = cli(review, "assign", SLUG, "--round", "1", code=1)
    assert "submitter" in res.output and "stub_b" in res.output


def test_paper_review_same_family_pool_names_the_switch(project):
    make_run(project, workflow="paper-review", assignments={
        "research.submitter": "stub_b", "research.reviewer": ["stub_r"]})
    res = cli(review, "assign", SLUG, "--round", "1", code=1)
    assert "allow_same_family_review" in res.output


def test_paper_review_without_a_submitter_is_refused(project):
    make_run(project, workflow="paper-review",
             assignments={"research.reviewer": ["stub_a"]})
    # the project default names a submitter; drop it for this run
    (project.root / "config" / "defaults.yml").write_text(
        (project.root / "config" / "defaults.yml").read_text().replace(
            "research.submitter: codex-paper", "research.submitter: null"))
    res = cli(review, "assign", SLUG, "--round", "1", code=1)
    assert "research.submitter" in res.output


# --- protocols and rules say what the machinery does ------------------------------------------


SKILLS = REPO / "src" / "scieflow" / "research" / "skills"


def flat(path: Path) -> str:
    """The file's text with line wrapping undone (protocols wrap commands)."""
    return " ".join(path.read_text().split())


def test_paper_draft_protocol_runs_the_two_pass_round():
    text = flat(SKILLS / "paper-draft" / "SKILL.md")
    phase4 = text[text.index("## Phase 4"):text.index("## Phase 5")]
    order = ["scieflow review assign", "--pass blind", "scieflow review evidence",
             "--pass evidence", "scieflow review matrix", "--check", "scieflow review revise",
             "scieflow review coverage"]
    positions = [phase4.index(step) for step in order]
    assert positions == sorted(positions), order
    for needle in ("research.draft-reviewers", "allow_same_family_review", "allow_self_review",
                   "scite-consent", "external-sharing", "--plan", "review-decision",
                   "review-blocker", "scieflow review merge-check", "skipped-quorum",
                   "single-author, not cross-reviewed", "scite backfill", "fresh dispatch"):
        assert needle in text, needle


def test_paper_review_protocol_uses_the_pool_and_two_passes():
    text = flat(SKILLS / "paper-review" / "SKILL.md")
    for needle in ("research.reviewer", "research.submitter", "model family",
                   "allow_same_family_review", "--pass blind", "--pass evidence",
                   "scieflow review matrix", "review-decision", "scieflow review revise",
                   "scieflow review coverage", "scite-consent", "workflow: paper-review"):
        assert needle in text, needle


def test_root_rule_17_scite():
    text = flat(REPO / "AGENTS.md")
    rule = text[text.index("17. **scite**"):]
    for needle in ("scieflow research scite", "scieflow review evidence", "sub-agent",
                   "off", "ask", "approved", "boundary", "sanitised", "human",
                   "research.draft-reviewers"):
        assert needle in rule, needle
    research_rules = flat(REPO / "src" / "scieflow" / "research" / "AGENTS.md")
    assert "research.draft-reviewers" in research_rules
    assert "rule 17" in research_rules
