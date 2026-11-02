"""Port regression tests for the paper-lifecycle landing (plan section 5.4, P1-P5).

Synthetic metadata only. They pin what the port must keep (research-loop
init and statuses) and the refusals it adds for paper runs.
"""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events, service, store
from scieflow.core.project import Project
from scieflow.core.run import budget, status, validate

ROOT = Path(__file__).resolve().parents[2]
LOOP_PHASES = ["hypothesize", "experiment", "literature", "synthesize"]
PAPER_PHASES = ["intake", "outline", "draft", "cross-review", "merge", "verify", "handoff"]


@pytest.fixture
def paper_project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "config" / "defaults.yml").write_text(
        "approval: autonomous\nmax_iterations: 5\nmax_experiment_runs: 40\n"
        "max_wall_minutes: 120\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "status-research", "gates"):
        (tmp_path / "schemas" / f"{name}.yml").write_bytes(
            (ROOT / "schemas" / f"{name}.yml").read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(status, "_VOCAB", None)
    return Project(tmp_path)


def invoke(*args):
    return CliRunner().invoke(main, list(args))


def snapshot(ws):
    return {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")}


def prepared(project):
    ws = project.run_dir("prepared")
    (ws / "inputs").mkdir(parents=True)
    (ws / "inputs" / "metadata.txt").write_bytes(b"synthetic input\r\n")
    (ws / "goal.md").write_bytes(b"Existing synthetic goal\r\n")
    (ws / "config.yml").write_text(
        "# Preserve formatting and comments\nworkflow: paper-draft\n"
        "review: {scite: off, allow_self_review: false}\n")
    (ws / "events.jsonl").write_bytes(b'{"type":"note.synthetic","data":{}}\r\n')
    return ws


def test_p1_schemas_agree_and_phase_lookup_refuses_other_workflows(paper_project):
    main_schema = yaml.safe_load((ROOT / "schemas" / "status.yml").read_text())
    research = yaml.safe_load((ROOT / "schemas" / "status-research.yml").read_text())
    assert main_schema["workflows"]["paper-draft"] == research["workflows"]["paper-draft"]
    assert status.phases_for("research-loop") == main_schema["phases"]
    assert status.phases_for("paper-draft") == PAPER_PHASES
    with pytest.raises(ValueError, match="unknown workflow"):
        status.phases_for("lit-review")


def test_p2_loop_init_keeps_the_loop_shape(paper_project):
    goal = paper_project.root / "goal.md"
    goal.write_text("Synthetic loop goal.\n")
    result = invoke("run", "init", "loop", "--goal", str(goal))
    assert result.exit_code == 0, result.output
    ws = paper_project.run_dir("loop")
    st = status.read_status(ws)
    assert set(st) == {"run", "id", "created", "approval", "iteration", "phases", "stopped"}
    assert st["run"] == "loop" and st["iteration"] == 1 and st["stopped"] is None
    assert list(st["phases"]) == LOOP_PHASES
    assert set(st["phases"].values()) == {"pending"}
    assert (ws / "iterations").is_dir() and (ws / "notebook.md").is_file()
    defaults = yaml.safe_load((paper_project.root / "config" / "defaults.yml").read_text())
    assert yaml.safe_load((ws / "config.yml").read_text()) == {
        **defaults, "workflow": "research-loop"}
    assert budget.read_budget(ws)["budgets"] == {
        "max_iterations": 5, "max_experiment_runs": 40, "max_wall_minutes": 120}
    assert validate.validate_status(st) == []


def test_p3_paper_advance_is_refused_before_any_budget_checkpoint(paper_project):
    goal = paper_project.root / "goal.md"
    goal.write_text("Synthetic paper goal.\n")
    assert invoke("run", "init", "paper", "--workflow", "paper-draft", "--goal", str(goal),
                  "--approval", "per-campaign", "--max-wall-minutes", "5",
                  "--max-experiment-runs", "0", "--max-iterations", "1").exit_code == 0
    ws = paper_project.run_dir("paper")
    assert invoke("run", "spend", "paper", "--iterations", "1", "--as-agent").exit_code == 0
    names = ("status.yml", "budget.yml", "events.jsonl")
    before = {n: (ws / n).read_bytes() for n in names}
    result = invoke("run", "advance", "paper", "--as-agent")
    assert result.exit_code != 0
    assert "only research-loop runs can advance iterations" in result.output
    with pytest.raises(service.ServiceError,
                       match="only research-loop runs can advance iterations"):
        service.advance_run(paper_project, "paper", actor="agent")
    assert {n: (ws / n).read_bytes() for n in names} == before
    assert status.read_status(ws)["stopped"] is None
    assert "job.refused" not in [e["type"] for e in events.read(ws)]


def test_p4_other_research_workflows_are_refused_cleanly(paper_project):
    ws = paper_project.run_dir("lit")
    ws.mkdir(parents=True)
    legacy = {"workflow": "lit-review", "slug": ws.name, "id": "synthetic-lit",
              "created": "2026-11-02T00:00:00+00:00", "approval": "per-campaign",
              "stopped": None,
              "phases": dict.fromkeys(["brief", "search", "cross-review", "synthesize",
                                       "export"], "pending")}
    status.write_status(ws, legacy)
    assert validate.validate_status_research(legacy) == []
    before = snapshot(ws)
    for args in (("mark", "lit", "brief", "done", "--as-agent"),
                 ("checkpoint", "lit", "--reason", "user", "--as-agent")):
        result = invoke("run", *args)
        assert result.exit_code == 1, result.output
        assert "Error:" in result.output and "unknown workflow" in result.output
        assert isinstance(result.exception, SystemExit), repr(result.exception)
        assert snapshot(ws) == before
    assert validate.validate_status_research(status.read_status(ws)) == []


def test_p5_adoption_retry_guard_end_to_end(paper_project, monkeypatch):
    from scieflow.core.run.init import adopt_workspace

    ws = prepared(paper_project)
    (ws / "config.yml").unlink()  # adoption writes config.yml, so its cleanup can fail
    history = (ws / "events.jsonl").read_bytes()
    guard = ws / ".adoption-incomplete"
    original_write = store._atomic_write
    original_unlink = Path.unlink

    def failing_status_write(path, text):
        if path.name == "status.yml":
            raise OSError("injected status write failure")
        return original_write(path, text)

    def failing_config_cleanup(self, missing_ok=False):
        if self.name == "config.yml":
            raise OSError("injected cleanup failure")
        return original_unlink(self, missing_ok=missing_ok)

    def adopt():
        return adopt_workspace("prepared", paper_project.workspace_root,
                               workflow="paper-draft", approval="per-campaign")

    monkeypatch.setattr(store, "_atomic_write", failing_status_write)
    monkeypatch.setattr(Path, "unlink", failing_config_cleanup)
    with pytest.raises(OSError, match="rollback incomplete") as info:
        adopt()
    assert str(guard) in str(info.value)
    assert guard.is_file() and not (ws / "status.yml").exists()
    assert (ws / "events.jsonl").read_bytes() == history

    monkeypatch.setattr(Path, "unlink", original_unlink)
    monkeypatch.setattr(store, "_atomic_write", original_write)
    with pytest.raises(OSError, match="incomplete adoption"):
        adopt()
    result = invoke("run", "adopt", "prepared", "--workflow", "paper-draft",
                    "--approval", "per-campaign", "--as-agent")
    assert result.exit_code == 1 and "Error:" in result.output
    assert "incomplete adoption" in result.output
    assert not (ws / "status.yml").exists()

    guard.unlink()
    outside = paper_project.root / "outside-guard"
    outside.write_text("outside sentinel")
    guard.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        adopt()
    assert guard.is_symlink() and outside.read_text() == "outside sentinel"

    # What the inspecting person does: remove the guard, then retry.
    guard.unlink()
    adopt()
    assert not guard.exists()
    assert status.read_status(ws)["workflow"] == "paper-draft"
    assert (ws / "events.jsonl").read_bytes().startswith(history)
    assert [e["type"] for e in events.read(ws)] == ["note.synthetic", "note.run-adopted"]
