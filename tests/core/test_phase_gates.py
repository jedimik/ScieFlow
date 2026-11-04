"""M-G2 t2: the phase-gate seam in actions.mark_phase (M-G2 5.3-5.6).

The claim check and the revision coverage are stubbed here and counted; the real checks run in
tests/research/manuscript/claims/test_verify_gate.py and tests/research/revision/test_respond_gate.py.
Synthetic metadata only: every slug, goal and status here is invented.
"""

import sys
from pathlib import Path

import pytest

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.core.run import actions, status
from scieflow.research.manuscript.claims import compile as claims_compile
from scieflow.research.revision import coverage as revision_coverage
from scieflow.research.revision import letter as revision_letter

ROOT = Path(__file__).resolve().parents[2]


class Result:
    """A stand-in for claims CheckResult: the attributes the gate reads."""

    def __init__(self, errors=0, warnings=0, status_unreadable=False):
        self.errors = [{"severity": "error"}] * errors
        self.warnings = [{"severity": "warning"}] * warnings
        self.status_unreadable = status_unreadable


class Stubs:
    """The claim check and the coverage check, replaced and counted. An exception value makes the call raise."""

    def __init__(self, monkeypatch):
        self.claims = Result()
        self.claim_calls = 0
        self.coverage = {"ok": True, "problems": []}
        self.coverage_calls = 0
        self.evaluated_rounds = []
        self.rounds = [1]
        monkeypatch.setattr(claims_compile, "check_run", self._check_run)
        monkeypatch.setattr(revision_letter, "existing_rounds", lambda run: list(self.rounds))
        monkeypatch.setattr(revision_coverage, "evaluate", self._evaluate)

    def _check_run(self, project, run_folder, main="main.tex"):
        self.claim_calls += 1
        if isinstance(self.claims, BaseException):
            raise self.claims
        return self.claims

    def _evaluate(self, run, round_):
        self.coverage_calls += 1
        self.evaluated_rounds.append(round_)
        if isinstance(self.coverage, BaseException):
            raise self.coverage
        return dict(self.coverage, round=round_)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: autonomous\nmax_iterations: 5\nmax_experiment_runs: 40\nmax_wall_minutes: 120\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "status-research", "gates"):
        (tmp_path / "schemas" / f"{name}.yml").write_bytes((ROOT / "schemas" / f"{name}.yml").read_bytes())
    (tmp_path / "workspace").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "_VOCAB", None)
    return Project(tmp_path)


@pytest.fixture
def calls(project, monkeypatch):
    return Stubs(monkeypatch)


def make_run(project, slug, workflow, *, manuscript=False):
    ws = project.run_dir(slug)
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status(slug, "per-campaign", workflow))
    if manuscript:
        (ws / "manuscript").mkdir()
        (ws / "manuscript" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    return ws


def gate_events(ws):
    return events.read(ws, types=("note.phase_gate",))


def status_bytes(ws):
    return (ws / "status.yml").read_bytes()


def snapshot(ws):
    """Every file of the run except its event log and that log's lock file, by relative path."""
    return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*"))
            if p.is_file() and not p.name.startswith("events.jsonl")}


# --- the seam: which runs and phases reach a gate ---------------------------

def test_paper_draft_verify_done_runs_the_claims_gate_once(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 1 and calls.coverage_calls == 0
    assert status.read_status(ws)["phases"]["verify"] == "done"
    (ev,) = gate_events(ws)
    assert ev["actor"] == "agent"
    assert ev["data"] == {"phase": "verify", "workflow": "paper-draft", "gate": "claims",
                          "outcome": "passed", "errors": 0, "warnings": 0}
    assert [e["type"] for e in events.read(ws)][-2:] == ["note.phase_gate", "phase.done"]


def test_paper_revision_verify_done_runs_the_claims_gate(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 1 and calls.coverage_calls == 0
    (ev,) = gate_events(ws)
    assert ev["data"]["gate"] == "claims" and ev["data"]["workflow"] == "paper-revision"


@pytest.mark.parametrize("state", ["running", "failed", "pending"])
def test_verify_states_other_than_done_never_run_the_gate(project, calls, state):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    actions.mark_phase(ws, "verify", state, "agent")
    assert calls.claim_calls == 0 and gate_events(ws) == []
    assert status.read_status(ws)["phases"]["verify"] == state


def test_other_paper_phases_never_run_a_gate(project, calls):
    pd = make_run(project, "pd", "paper-draft", manuscript=True)
    actions.mark_phase(pd, "draft", "done", "agent")
    rv = make_run(project, "rv", "paper-revision", manuscript=True)
    actions.mark_phase(rv, "revise", "done", "agent")
    assert calls.claim_calls == 0 and calls.coverage_calls == 0
    assert gate_events(pd) == [] and gate_events(rv) == []


def test_research_loop_runs_are_untouched(project, calls):
    ws = make_run(project, "loop", "research-loop", manuscript=True)
    actions.mark_phase(ws, "synthesize", "done", "agent")
    assert calls.claim_calls == 0 and calls.coverage_calls == 0
    assert gate_events(ws) == []
    assert status.read_status(ws)["phases"]["synthesize"] == "done"


def test_a_status_without_a_workflow_is_never_gated(project, calls):
    # A research-loop status has no workflow key; the default must be the research loop, which has no verify phase.
    ws = make_run(project, "loop", "research-loop", manuscript=True)
    assert "workflow" not in status.read_status(ws)
    before = status_bytes(ws)
    with pytest.raises(ValueError, match="unknown phase: verify"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 0 and gate_events(ws) == []
    assert status_bytes(ws) == before


def test_paper_revision_respond_done_runs_the_coverage_gate_only(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    actions.mark_phase(ws, "respond", "done", "agent")
    assert calls.claim_calls == 0 and calls.coverage_calls == 1
    (ev,) = gate_events(ws)
    assert ev["data"]["gate"] == "coverage" and ev["data"]["outcome"] == "passed"


def test_a_status_that_cannot_be_read_returns_before_the_gate(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    (ws / "status.yml").write_text("phases: [unclosed\n", encoding="utf-8")
    with pytest.raises(Exception) as info:
        actions.mark_phase(ws, "verify", "done", "agent")
    assert "refused" not in str(info.value)
    assert calls.claim_calls == 0 and gate_events(ws) == []


# --- fail closed --------------------------------------------------------------

def test_a_claim_check_that_raises_is_refused_fail_closed(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    calls.claims = RuntimeError("synthetic internal failure")
    before = status_bytes(ws)
    with pytest.raises(ValueError, match=r"verify refused: the claim check could not run \(RuntimeError\); "
                                         r"fail closed"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert status_bytes(ws) == before
    (ev,) = gate_events(ws)
    assert ev["data"]["outcome"] == "failed-closed"


def test_a_missing_claims_module_is_refused(project, calls, monkeypatch):
    monkeypatch.setitem(sys.modules, "scieflow.research.manuscript.claims.compile", None)
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    before = status_bytes(ws)
    with pytest.raises(ValueError, match=r"verify refused: the claim check could not run \(ImportError\)"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert status_bytes(ws) == before


def test_a_missing_revision_module_refuses_respond(project, calls, monkeypatch):
    monkeypatch.setitem(sys.modules, "scieflow.research.revision.coverage", None)
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    before = status_bytes(ws)
    with pytest.raises(ValueError, match=r"respond refused: the coverage check could not run \(ImportError\)"):
        actions.mark_phase(ws, "respond", "done", "agent")
    assert status_bytes(ws) == before


def test_a_project_that_cannot_be_found_is_refused(project, calls, monkeypatch):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)

    def missing(start=None):
        raise FileNotFoundError("synthetic: no config/agents.yml above the run")

    monkeypatch.setattr(Project, "discover", staticmethod(missing))
    with pytest.raises(ValueError, match=r"verify refused: the claim check could not run \(FileNotFoundError\)"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert status.read_status(ws)["phases"]["verify"] == "pending"


def test_a_linked_manuscript_folder_is_refused(project, calls, tmp_path):
    ws = make_run(project, "pd", "paper-draft")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (ws / "manuscript").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="manuscript must be a folder inside the run, not a link or a file"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 0
    assert gate_events(ws)[0]["data"]["outcome"] == "refused"


def test_a_manuscript_that_is_a_file_is_refused(project, calls):
    ws = make_run(project, "pd", "paper-draft")
    (ws / "manuscript").write_text("not a folder\n", encoding="utf-8")
    with pytest.raises(ValueError, match="manuscript must be a folder inside the run, not a link or a file"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 0


def test_an_unreadable_review_status_is_refused(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    calls.claims = Result(status_unreadable=True)
    with pytest.raises(ValueError, match="the review status could not be read; fail closed"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert gate_events(ws)[0]["data"]["outcome"] == "refused"


# --- the no-manuscript rule and the claim-error message ----------------------

def test_a_paper_draft_without_a_manuscript_passes_as_no_manuscript(project, calls):
    ws = make_run(project, "pd", "paper-draft")
    actions.mark_phase(ws, "verify", "done", "agent")
    assert calls.claim_calls == 0
    assert status.read_status(ws)["phases"]["verify"] == "done"
    (ev,) = gate_events(ws)
    assert ev["data"]["outcome"] == "no-manuscript"


def test_a_paper_revision_without_a_manuscript_is_refused(project, calls):
    ws = make_run(project, "rv", "paper-revision")
    with pytest.raises(ValueError, match="a paper-revision run needs its manuscript/ folder"):
        actions.mark_phase(ws, "verify", "done", "agent")
    assert status.read_status(ws)["phases"]["verify"] == "pending"


def test_claim_warnings_never_refuse_verify(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    calls.claims = Result(errors=0, warnings=2)
    actions.mark_phase(ws, "verify", "done", "agent")
    assert status.read_status(ws)["phases"]["verify"] == "done"
    (ev,) = gate_events(ws)
    assert ev["data"] == {"phase": "verify", "workflow": "paper-draft", "gate": "claims", "outcome": "passed",
                          "errors": 0, "warnings": 2}


def test_a_human_marking_is_recorded_as_human_on_pass_and_on_refusal(project, calls):
    passed_ws = make_run(project, "pd", "paper-draft", manuscript=True)
    actions.mark_phase(passed_ws, "verify", "done", "human")
    calls.claims = Result(errors=1)
    refused_ws = make_run(project, "pd2", "paper-draft", manuscript=True)
    with pytest.raises(ValueError, match="claim error"):
        actions.mark_phase(refused_ws, "verify", "done", "human")
    assert [e["actor"] for e in gate_events(passed_ws)] == ["human"]
    assert [e["actor"] for e in gate_events(refused_ws)] == ["human"]


def test_claim_errors_refuse_with_the_commands_to_run_and_no_other_write(project, calls):
    ws = make_run(project, "pd", "paper-draft", manuscript=True)
    calls.claims = Result(errors=2, warnings=1)
    before = snapshot(ws)
    before_status = status_bytes(ws)
    lines_before = len(events.read(ws))
    with pytest.raises(ValueError) as info:
        actions.mark_phase(ws, "verify", "done", "agent")
    msg = str(info.value)
    assert "verify refused: 2 claim error(s)" in msg
    assert "scieflow manuscript claims build pd" in msg and "scieflow manuscript claims check pd" in msg
    assert status_bytes(ws) == before_status
    assert snapshot(ws) == before
    assert len(events.read(ws)) == lines_before + 1
    (ev,) = gate_events(ws)
    assert ev["data"] == {"phase": "verify", "workflow": "paper-draft", "gate": "claims", "outcome": "refused",
                          "errors": 2, "warnings": 1}


# --- the coverage gate ----------------------------------------------------------

def test_respond_without_a_letter_round_is_refused(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    calls.rounds = []
    with pytest.raises(ValueError) as info:
        actions.mark_phase(ws, "respond", "done", "agent")
    assert "respond refused: no decision letter ingested" in str(info.value)
    assert "scieflow manuscript revision ingest rv --letter FILE" in str(info.value)
    assert calls.coverage_calls == 0


def test_respond_with_coverage_problems_names_the_round_and_the_command(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    calls.rounds = [1, 2]
    calls.coverage = {"ok": False, "problems": [{"level": "error"}] * 3 + [{"level": "warning"}]}
    with pytest.raises(ValueError) as info:
        actions.mark_phase(ws, "respond", "done", "agent")
    msg = str(info.value)
    assert "respond refused: round 2 coverage has 3 problem(s)" in msg
    assert "scieflow manuscript revision coverage rv --round 2" in msg
    assert calls.evaluated_rounds == [2]
    (ev,) = gate_events(ws)
    assert ev["data"] == {"phase": "respond", "workflow": "paper-revision", "gate": "coverage",
                          "outcome": "refused", "errors": 3, "warnings": 1, "round": 2}


def test_respond_with_a_failing_coverage_check_is_refused_fail_closed(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    calls.coverage = RuntimeError("synthetic internal failure")
    with pytest.raises(ValueError, match=r"respond refused: the coverage check could not run \(RuntimeError\); "
                                         r"fail closed"):
        actions.mark_phase(ws, "respond", "done", "agent")
    assert gate_events(ws)[0]["data"]["outcome"] == "failed-closed"


def test_respond_passes_with_warnings_and_names_the_round(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    calls.coverage = {"ok": True, "problems": [{"level": "warning"}]}
    actions.mark_phase(ws, "respond", "done", "agent")
    assert status.read_status(ws)["phases"]["respond"] == "done"
    (ev,) = gate_events(ws)
    assert ev["data"] == {"phase": "respond", "workflow": "paper-revision", "gate": "coverage",
                          "outcome": "passed", "errors": 0, "warnings": 1, "round": 1}


def test_the_highest_existing_round_is_the_one_checked(project, calls):
    ws = make_run(project, "rv", "paper-revision", manuscript=True)
    calls.rounds = [1, 3, 2]
    actions.mark_phase(ws, "respond", "done", "agent")
    assert calls.evaluated_rounds == [3]
