import importlib.util
from pathlib import Path

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.compare import compare_campaign
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("replicate_support.py")
_spec = importlib.util.spec_from_file_location("replicate_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

def test_replicate_comparison_ranks_configurations_by_mean(project):
    support.add_seeded_stage(project)
    campaign = Campaign.load(support.write_campaign(
        project, support.campaign_dict(replicates=3)))
    stage = find_stage(project / "pipelines", "demo", "seeded")
    run_sweep(campaign, stage, project / "experiments", mode="direct")

    comparison = compare_campaign(project / "experiments/rep-sweep", include_direct=True)
    configs = comparison["configs"]
    means = [c["mean"] for c in configs]
    assert means == sorted(means, reverse=True)  # ssim: higher is better
    assert comparison["best_config"] == configs[0]["config_id"]
    for c in configs:
        assert c["n"] == 3 and c["n_failed"] == 0 and len(c["runs"]) == 3
    assert comparison["best"] in {r["run_id"] for r in comparison["ranked"]}


def test_plain_comparison_has_no_configuration_keys(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    comparison = compare_campaign(project / "experiments/gain-sweep", include_direct=True)
    assert "configs" not in comparison and "best_config" not in comparison

