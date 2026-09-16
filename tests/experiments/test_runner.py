import json

import pytest
import yaml

from scieflow.experiments.campaign import Campaign, RunSpec
from scieflow.experiments.runner import execute_run, recompute_metrics
from scieflow.experiments.stage import find_stage


def load_campaign_and_stage(project):
    campaign = Campaign.load(
        project / "pipelines/demo/campaigns/gain-sweep.yaml"
    )
    stage = find_stage(project / "pipelines", "demo", "scale")
    return campaign, stage


def test_execute_run_direct_records_everything(project):
    campaign, stage = load_campaign_and_stage(project)
    spec = RunSpec(run_id="000_gain-1.0", params={"gain": 1.0})
    run_dir = execute_run(
        campaign, stage, spec, project / "experiments", mode="direct"
    )
    assert run_dir == project / "experiments/gain-sweep/runs/000_gain-1.0"
    assert (run_dir / "artifacts/result.png").exists()
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert config["status"] == "completed"
    assert config["params"] == {"gain": 1.0}
    assert config["metrics"] == ["ssim", "mse"]
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["ssim"] == pytest.approx(1.0)
    env = json.loads((run_dir / "environment.json").read_text())
    assert env["mode"] == "direct"
    assert (run_dir / "logs/stdout.log").exists()


def test_failed_run_recorded_not_raised(project):
    campaign, stage = load_campaign_and_stage(project)
    spec = RunSpec(run_id="000_bad", params={"gain": "not-a-float"})
    run_dir = execute_run(
        campaign, stage, spec, project / "experiments", mode="direct"
    )
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert config["status"] == "failed"
    assert config["returncode"] != 0
    assert not (run_dir / "metrics.json").exists()
    assert (run_dir / "logs/stderr.log").read_text() != ""


def test_recompute_metrics(project):
    campaign, stage = load_campaign_and_stage(project)
    spec = RunSpec(run_id="000_gain-1.0", params={"gain": 1.0})
    run_dir = execute_run(
        campaign, stage, spec, project / "experiments", mode="direct"
    )
    (run_dir / "metrics.json").unlink()
    metrics = recompute_metrics(run_dir)
    assert metrics["ssim"] == pytest.approx(1.0)
    assert (run_dir / "metrics.json").exists()
