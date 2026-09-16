import json

import yaml

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.compare import check_validation, compare_campaign
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep


def test_check_validation_min_max():
    result = check_validation({"ssim": 0.95, "mse": 12.0},
                              {"ssim": {"min": 0.9}, "mse": {"max": 10.0}})
    assert result["passed"] is False
    assert result["checks"]["ssim"]["passed"] is True
    assert result["checks"]["mse"]["passed"] is False


def test_check_validation_missing_metric_fails():
    result = check_validation({}, {"ssim": {"min": 0.9}})
    assert result["passed"] is False


def test_compare_campaign_ranks_and_validates(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")

    comparison = compare_campaign(project / "experiments/gain-sweep")
    assert comparison["rank_by"] == "ssim"
    assert comparison["best"].endswith("gain-1.0")
    assert comparison["ranked"][0]["validation"]["passed"] is True
    assert comparison["ranked"][-1]["validation"]["passed"] is False
    assert comparison["failed"] == []
    assert json.loads(
        (project / "experiments/gain-sweep/comparison.json").read_text()
    )["best"] == comparison["best"]


def test_compare_campaign_completed_without_metric_counts_as_failed(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    results_path = run_sweep(campaign, stage, project / "experiments", mode="direct")

    results = json.loads(results_path.read_text())
    victim = results["runs"][0]
    victim["status"] = "completed"
    victim["metrics"] = {}
    results_path.write_text(json.dumps(results, indent=2))

    comparison = compare_campaign(project / "experiments/gain-sweep")

    assert victim["run_id"] in comparison["failed"]
    ranked_ids = [r["run_id"] for r in comparison["ranked"]]
    assert victim["run_id"] not in ranked_ids
    other = results["runs"][1]
    assert other["run_id"] in ranked_ids
    assert comparison["best"] == other["run_id"]


def test_compare_campaign_resolves_pipeline_local_metric_in_fresh_process(project):
    metrics_dir = project / "pipelines/demo/metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "constant.py").write_text(
        "def const_half(output, reference):\n"
        "    return 0.5\n"
        "\n"
        "METRICS = {\"const_half\": (const_half, True)}\n"
    )

    campaign_yaml_path = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    campaign_data = yaml.safe_load(campaign_yaml_path.read_text())
    campaign_data["metrics"] = ["const_half"]
    campaign_data["rank_by"] = "const_half"
    campaign_data.pop("validation", None)
    campaign_yaml_path.write_text(yaml.safe_dump(campaign_data))

    campaign = Campaign.load(campaign_yaml_path)
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")

    from scieflow.experiments.metrics import REGISTRY
    REGISTRY.pop("const_half", None)

    comparison = compare_campaign(project / "experiments/gain-sweep")
    assert comparison["rank_by"] == "const_half"
