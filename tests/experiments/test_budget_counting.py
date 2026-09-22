from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import actions, budget, status
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.cli import experiment


def make_run(root, runs_cap):
    ws = root / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, runs_cap, 600))
    return ws


def sweep(root, experiments_dir):
    campaign = root / "pipelines" / "demo" / "campaigns" / "gain-sweep.yaml"
    return CliRunner().invoke(experiment, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(experiments_dir),
        "--pipelines-dir", str(root / "pipelines")])


def test_sweep_counts_runs_against_the_budget(project):
    ws = make_run(project, runs_cap=10)
    result = sweep(project, ws / "experiments")
    assert result.exit_code == 0, result.output
    n_runs = len(Campaign.load(project / "pipelines" / "demo" / "campaigns"
                               / "gain-sweep.yaml").expand())
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == n_runs == 2
    assert "budget.recorded" in [e["type"] for e in events.read(ws)]


def test_sweep_refused_when_runs_are_spent(project):
    ws = make_run(project, runs_cap=2)
    actions.record_spend(ws, experiment_runs=2)
    result = sweep(project, ws / "experiments")
    assert result.exit_code != 0
    assert "budget exhausted" in result.output
    assert not (ws / "experiments").exists()


def test_sweep_outside_a_run_is_unaffected(project):
    result = sweep(project, project / "exp")
    assert result.exit_code == 0, result.output
