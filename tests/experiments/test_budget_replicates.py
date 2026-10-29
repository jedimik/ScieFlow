import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from click.exceptions import ClickException
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.run import actions, budget, status
from scieflow.experiments.cli import experiment

_SUPPORT = Path(__file__).with_name("replicate_support.py")
_spec = importlib.util.spec_from_file_location("replicate_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)


def make_run(root, runs_cap):
    ws = root / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, runs_cap, 600))
    return ws


def cli(root, campaign, experiments_dir, *extra):
    return CliRunner().invoke(experiment, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(experiments_dir),
        "--pipelines-dir", str(root / "pipelines"), *extra])


def replicate_campaign(root, **kw):
    kw.setdefault("replicates", 3)
    return support.write_campaign(root, support.campaign_dict(**kw))


def test_replicate_sweep_counts_every_run_against_the_budget(project):
    support.add_seeded_stage(project)
    ws = make_run(project, runs_cap=10)
    camp = replicate_campaign(project)
    result = cli(project, camp, ws / "experiments")
    assert result.exit_code == 0, result.output
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 6  # 2 configs x 3 replicates
    assert [e["type"] for e in events.read(ws)].count("budget.recorded") == 1


def test_replicate_sweep_refused_before_any_run_when_budget_is_short(project):
    support.add_seeded_stage(project)
    ws = make_run(project, runs_cap=10)
    actions.record_spend(ws, experiment_runs=5)
    camp = replicate_campaign(project)
    result = cli(project, camp, ws / "experiments")
    assert result.exit_code == 1
    assert "insufficient" in result.output
    assert not (ws / "experiments").exists()
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 5
    refusals = [e for e in events.read(ws) if e["type"] == "job.refused"]
    assert len(refusals) == 1
    data = refusals[0]["data"]
    assert data["reason"] == "budget" and data["rule"] == "insufficient"
    assert data["planned"] == 6 and data["remaining"] == 5
    assert not status.read_status(ws).get("stopped")  # no checkpoint


def test_zero_cap_means_unlimited(project):
    support.add_seeded_stage(project)
    ws = make_run(project, runs_cap=0)
    result = cli(project, replicate_campaign(project), ws / "experiments")
    assert result.exit_code == 0, result.output
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 6


def test_no_budget_file_means_no_refusal_and_no_new_budget(project):
    support.add_seeded_stage(project)
    ws = project / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    result = cli(project, replicate_campaign(project), ws / "experiments")
    assert result.exit_code == 0, result.output
    assert len(list((ws / "experiments" / "rep-sweep" / "runs").iterdir())) == 6
    types = [e["type"] for e in events.read(ws)]
    assert "job.refused" not in types and "budget.recorded" not in types
    assert not (ws / "budget.yml").exists()


def test_null_cap_means_no_refusal(project):
    support.add_seeded_stage(project)
    ws = make_run(project, runs_cap=None)
    result = cli(project, replicate_campaign(project), ws / "experiments")
    assert result.exit_code == 0, result.output
    assert "job.refused" not in [e["type"] for e in events.read(ws)]


def test_guard_capacity_without_budgets_key_passes(tmp_path):
    from scieflow.experiments.cli import _guard_capacity

    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    budget.write_budget(ws, {"budgets": {"max_iterations": 3}, "spent": {}})
    _guard_capacity(ws, 6)  # no raise


def test_guard_capacity_missing_spent_counts_as_zero(tmp_path):
    from scieflow.experiments.cli import _guard_capacity as guard

    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    budget.write_budget(ws, {"budgets": {"max_experiment_runs": 10}, "spent": {}})
    guard(ws, 6)  # passes
    with pytest.raises(ClickException, match="insufficient"):
        guard(ws, 11)
    refusal = [e for e in events.read(ws) if e["type"] == "job.refused"][-1]
    assert refusal["data"]["remaining"] == 10 and refusal["data"]["planned"] == 11


def test_guard_capacity_outside_a_run_never_refuses():
    from scieflow.experiments.cli import _guard_capacity

    _guard_capacity(None, 10**6)  # no raise


def test_single_run_with_seed_records_one_and_passes_the_seed(project):
    support.add_seeded_stage(project)
    ws = make_run(project, runs_cap=10)
    camp = support.write_campaign(project, support.campaign_dict(name="single"))
    result = CliRunner().invoke(experiment, [
        "run", "-c", str(camp), "-p", "gain=0.5", "--seed", "3", "--direct",
        "--experiments-dir", str(ws / "experiments"),
        "--pipelines-dir", str(project / "pipelines")])
    assert result.exit_code == 0, result.output
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 1
    run_dir = ws / "experiments" / "single" / "runs" / "manual_gain-0.5"
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert cfg["config_id"] == "manual_gain-0.5" and cfg["replicate"] is None and cfg["seed"] == 3
    env = json.loads((run_dir / "environment.json").read_text())
    assert env["command"][-2:] == ["--seed", "3"]


def test_single_run_with_seed_on_deterministic_stage_is_refused(project):
    camp = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    result = CliRunner().invoke(experiment, [
        "run", "-c", str(camp), "-p", "gain=0.5", "--seed", "3", "--direct",
        "--experiments-dir", str(project / "exp"),
        "--pipelines-dir", str(project / "pipelines")])
    assert result.exit_code == 1
    assert "accepts_seed: true" in result.output
    assert not (project / "exp").exists()

