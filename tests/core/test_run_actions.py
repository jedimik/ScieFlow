from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import actions, budget, status


def make_loop_run(tmp_path, **caps):
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(caps.get("it", 2), caps.get("runs", 5),
                                              caps.get("wall", 60)))
    return ws


def test_mark_phase_writes_status_and_logs_an_event(tmp_path):
    ws = make_loop_run(tmp_path)
    actions.mark_phase(ws, "hypothesize", "running")
    actions.mark_phase(ws, "hypothesize", "done")
    assert status.read_status(ws)["phases"]["hypothesize"] == "done"
    assert [e["type"] for e in events.read(ws)] == ["phase.started", "phase.done"]


def test_advance_refused_when_iterations_are_spent(tmp_path):
    ws = make_loop_run(tmp_path, it=1)
    for phase in status.PHASES:
        actions.mark_phase(ws, phase, "done")
    actions.record_spend(ws, iterations=1)
    with pytest.raises(actions.BudgetExhausted) as exc:
        actions.advance_iteration(ws)
    assert exc.value.dims == ["iterations"]
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_advance_allowed_within_budget(tmp_path):
    ws = make_loop_run(tmp_path, it=2)
    for phase in status.PHASES:
        actions.mark_phase(ws, phase, "done")
    actions.record_spend(ws, iterations=1)
    assert actions.advance_iteration(ws)["iteration"] == 2


def test_guard_budget_checkpoints_once_and_refuses(tmp_path):
    ws = make_loop_run(tmp_path, wall=1)
    actions.record_spend(ws, wall_minutes=1.5)
    for _ in range(2):
        with pytest.raises(actions.BudgetExhausted):
            actions.guard_budget(ws, ("wall_minutes",))
    types = [e["type"] for e in events.read(ws)]
    assert types.count("checkpoint") == 1 and types.count("job.refused") == 2


def test_guard_budget_ignores_other_dimensions_and_research_runs(tmp_path):
    ws = make_loop_run(tmp_path, it=1)
    actions.record_spend(ws, iterations=1)
    actions.guard_budget(ws, ("wall_minutes",))  # iterations spent: not this guard's job
    research = tmp_path / "workspace" / "lit"
    research.mkdir()
    actions.guard_budget(research, ("wall_minutes",))  # no budget.yml: nothing to guard


def test_run_for_path_finds_the_enclosing_run(tmp_path):
    ws = make_loop_run(tmp_path)
    assert actions.run_for_path(ws / "logs" / "p.md") == ws
    assert actions.run_for_path(tmp_path) is None


def test_run_cli_mark_events_and_log(tmp_path, monkeypatch):
    from scieflow.core.run.cli import run as run_group

    ws = make_loop_run(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    real = Path(__file__).resolve().parents[2] / "schemas" / "status.yml"
    (tmp_path / "schemas" / "status.yml").write_text(real.read_text())
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    assert cli.invoke(run_group, ["mark", "r1", "hypothesize", "running"]).exit_code == 0
    assert cli.invoke(run_group, ["log", "r1", "note.idea", "--message", "try sigma 2"]).exit_code == 0
    out = cli.invoke(run_group, ["events", "r1", "--json"])
    assert out.exit_code == 0, out.output
    assert '"note.idea"' in out.output and '"phase.started"' in out.output
    assert cli.invoke(run_group, ["log", "r1", "phase.done"]).exit_code != 0  # agents log notes only


def test_run_cli_spend_records_what_the_runner_cannot_see(tmp_path, monkeypatch):
    from scieflow.core.run.cli import run as run_group

    ws = make_loop_run(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    real = Path(__file__).resolve().parents[2] / "schemas" / "status.yml"
    (tmp_path / "schemas" / "status.yml").write_text(real.read_text())
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    done = cli.invoke(run_group, ["spend", "r1", "--experiment-runs", "3", "--as-agent"])
    assert done.exit_code == 0, done.output
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 3
    assert "budget.recorded" in [e["type"] for e in events.read(ws)]
    empty = cli.invoke(run_group, ["spend", "r1"])
    assert empty.exit_code != 0 and "nothing to record" in empty.output
