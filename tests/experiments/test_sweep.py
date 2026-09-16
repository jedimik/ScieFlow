import json

import yaml

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep


def test_run_sweep_records_all_runs(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    results_path = run_sweep(campaign, stage, project / "experiments", mode="direct")

    results = json.loads(results_path.read_text())
    assert results["campaign"] == "gain-sweep"
    assert results["rank_by"] == "ssim"
    assert len(results["runs"]) == 2
    by_gain = {r["params"]["gain"]: r for r in results["runs"]}
    assert by_gain[1.0]["status"] == "completed"
    assert by_gain[1.0]["metrics"]["ssim"] > by_gain[0.5]["metrics"]["ssim"]

    copied = yaml.safe_load(
        (project / "experiments/gain-sweep/campaign.yaml").read_text()
    )
    assert copied["input"].startswith("/")  # absolute — survives the copy
