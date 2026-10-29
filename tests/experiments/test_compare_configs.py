import importlib.util
import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.cli import experiment
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


# Hand-written results.json (synthetic): the configuration ranking without a sweep.

def _run(config, k, value, metric, status="completed"):
    metrics = {metric: value} if status == "completed" else {}
    return {
        "run_id": f"{config}_r{k}", "config_id": config, "replicate": k, "seed": k,
        "params": {"gain": config}, "status": status, "metrics": metrics,
        "mode": "apptainer",
    }


def _hand_campaign(tmp_path, rank_by, runs):
    d = tmp_path / "experiments" / "hand"
    (d / "runs").mkdir(parents=True)
    (d / "campaign.yaml").write_text(yaml.safe_dump(
        {"name": "hand", "pipeline": "demo", "stage": "scale", "rank_by": rank_by}))
    doc = {"campaign": "hand", "rank_by": rank_by,
           "replicates": {"n": 3, "seed": 0}, "runs": runs}
    (d / "results.json").write_text(json.dumps(doc))
    return d


def test_lower_is_better_orders_configurations_ascending(tmp_path):
    runs = [
        _run("cfgA", 0, 4.0, "mse"), _run("cfgA", 1, 6.0, "mse"), _run("cfgA", 2, 8.0, "mse"),
        _run("cfgB", 0, 1.0, "mse"), _run("cfgB", 1, 2.0, "mse"), _run("cfgB", 2, 3.0, "mse"),
        _run("cfgC", 0, 3.0, "mse"), _run("cfgC", 1, 3.0, "mse"), _run("cfgC", 2, 3.0, "mse"),
    ]
    comparison = compare_campaign(_hand_campaign(tmp_path, "mse", runs))
    assert [c["config_id"] for c in comparison["configs"]] == ["cfgB", "cfgC", "cfgA"]
    assert comparison["best_config"] == "cfgB"
    assert [c["mean"] for c in comparison["configs"]] == [2.0, 3.0, 6.0]


def test_configuration_mean_is_recomputed_from_its_runs(tmp_path):
    runs = [_run("cfg", k, v, "ssim") for k, v in enumerate([1.0, 2.0, 4.0])]
    comparison = compare_campaign(_hand_campaign(tmp_path, "ssim", runs))
    (config,) = comparison["configs"]
    assert config["mean"] == (1.0 + 2.0 + 4.0) / 3
    assert config["n"] == 3 and config["runs"] == ["cfg_r0", "cfg_r1", "cfg_r2"]


def test_tie_on_mean_is_broken_by_config_id(tmp_path):
    runs = [
        _run("b-cfg", 0, 1.0, "ssim"), _run("b-cfg", 1, 3.0, "ssim"),
        _run("a-cfg", 0, 2.0, "ssim"), _run("a-cfg", 1, 2.0, "ssim"),
    ]
    comparison = compare_campaign(_hand_campaign(tmp_path, "ssim", runs))
    assert [c["config_id"] for c in comparison["configs"]] == ["a-cfg", "b-cfg"]
    assert comparison["best_config"] == "a-cfg"


def test_failed_replicate_is_counted_and_left_out_of_the_mean(tmp_path):
    runs = [
        _run("g1", 0, 1.0, "mse"), _run("g1", 1, None, "mse", status="failed"),
        _run("g1", 2, 3.0, "mse"),
    ]
    comparison = compare_campaign(_hand_campaign(tmp_path, "mse", runs))
    (config,) = comparison["configs"]
    assert config["n"] == 2 and config["n_failed"] == 1
    assert config["mean"] == 2.0
    assert sorted(config["runs"]) == ["g1_r0", "g1_r2"]


def test_configuration_without_completed_run_is_last_and_never_best(tmp_path):
    runs = [
        _run("ok", 0, 9.0, "mse"),
        _run("dead", 0, None, "mse", status="failed"),
        _run("dead", 1, None, "mse", status="failed"),
        _run("dead", 2, None, "mse", status="failed"),
    ]
    comparison = compare_campaign(_hand_campaign(tmp_path, "mse", runs))
    assert [c["config_id"] for c in comparison["configs"]] == ["ok", "dead"]
    dead = comparison["configs"][-1]
    assert dead["n"] == 0 and dead["n_failed"] == 3 and dead["mean"] is None
    assert dead["runs"] == []
    assert comparison["best_config"] == "ok"


def test_non_finite_configuration_mean_is_null_last_and_never_best(tmp_path):
    inf, nan = float("inf"), float("nan")
    runs = [
        _run("inf-cfg", 0, inf, "mse"), _run("inf-cfg", 1, -inf, "mse"),
        _run("nan-cfg", 0, nan, "mse"), _run("nan-cfg", 1, 1.0, "mse"),
        _run("fin", 0, 1.0, "mse"), _run("fin", 1, 2.0, "mse"),
    ]
    comparison = compare_campaign(_hand_campaign(tmp_path, "mse", runs))
    assert [c["config_id"] for c in comparison["configs"]][0] == "fin"
    assert comparison["best_config"] == "fin"
    for c in comparison["configs"][1:]:
        assert c["mean"] is None and c["n"] == 2
    # The configuration ranking never writes a non-standard number token.
    text = json.dumps(comparison["configs"])
    assert "NaN" not in text and "Infinity" not in text


def test_compare_cli_prints_configuration_lines(tmp_path):
    runs = [
        _run("cfgB", 0, 1.0, "mse"), _run("cfgB", 1, 2.0, "mse"), _run("cfgB", 2, 3.0, "mse"),
        _run("dead", 0, None, "mse", status="failed"),
    ]
    d = _hand_campaign(tmp_path, "mse", runs)
    result = CliRunner().invoke(experiment, ["compare", str(d)])
    assert result.exit_code == 0, result.output
    assert "config cfgB  mean mse=2.0000  n=3 failed=0" in result.output
    assert "config dead  mean mse=n/a  n=0 failed=1" in result.output
    assert "best config: cfgB" in result.output

