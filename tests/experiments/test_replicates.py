import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.experiments.campaign import Campaign, CampaignError, RunSpec
from scieflow.experiments.cli import experiment
from scieflow.experiments.runner import build_command
from scieflow.experiments.stage import Stage, StageError, find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("replicate_support.py")
_spec = importlib.util.spec_from_file_location("replicate_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)


def campaign_file(tmp_path, data):
    p = tmp_path / "campaign.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


BASE = {
    "name": "sweep", "pipeline": "demo", "stage": "seeded", "input": "data/noisy.png",
    "reference": "data/truth.png", "metrics": ["ssim", "mse"],
    "parameters": {"gain": {"grid": [0.5, 1.0]}},
}


def with_(**extra):
    return {**BASE, **extra}


def test_replicates_expand_configurations_outer_replicates_inner(tmp_path):
    c = Campaign.load(campaign_file(tmp_path, with_(replicates=3)))
    specs = c.expand()
    assert [s.run_id for s in specs] == [
        "000_gain-0.5_r0", "000_gain-0.5_r1", "000_gain-0.5_r2",
        "001_gain-1.0_r0", "001_gain-1.0_r1", "001_gain-1.0_r2",
    ]
    assert [s.config_id for s in specs] == ["000_gain-0.5"] * 3 + ["001_gain-1.0"] * 3
    assert [s.seed for s in specs] == [0, 1, 2, 0, 1, 2]
    assert [s.replicate for s in specs] == [0, 1, 2, 0, 1, 2]
    assert specs[0].params == {"gain": 0.5} and specs[5].params == {"gain": 1.0}


def test_mapping_form_sets_seed_base(tmp_path):
    c = Campaign.load(campaign_file(tmp_path, with_(replicates={"n": 2, "seed": 100})))
    assert [s.seed for s in c.expand()] == [100, 101, 100, 101]


def test_short_form_is_seed_zero(tmp_path):
    c = Campaign.load(campaign_file(tmp_path, with_(replicates=2)))
    assert c.replicates.n == 2 and c.replicates.seed == 0


def test_boundary_seed_is_accepted(tmp_path):
    c = Campaign.load(campaign_file(tmp_path, with_(replicates={"n": 2, "seed": 2**31 - 2})))
    assert [s.seed for s in c.expand()[:2]] == [2**31 - 2, 2**31 - 1]


def test_campaign_without_replicates_expands_as_before(tmp_path):
    c = Campaign.load(campaign_file(tmp_path, BASE))
    specs = c.expand()
    assert [s.run_id for s in specs] == ["000_gain-0.5", "001_gain-1.0"]
    assert all(s.config_id == s.run_id for s in specs)
    assert all(s.replicate is None and s.seed is None for s in specs)
    assert "replicates" not in c.to_dict()


@pytest.mark.parametrize("bad", [
    0, 1001, True, "3", [], {"n": 2, "x": 1}, {"n": 2, "seed": -1},
    {"n": 0}, {"seed": 3}, {"n": True}, {"n": 2, "seed": 2**31 - 1},
])
def test_invalid_replicates_are_refused(tmp_path, bad):
    with pytest.raises(CampaignError) as e:
        Campaign.load(campaign_file(tmp_path, with_(replicates=bad)))
    assert "replicates" in str(e.value)
    assert "unknown campaign key" not in str(e.value)  # the key itself is known


def test_seed_in_parameters_refused_with_replicates(tmp_path):
    data = with_(replicates=2, parameters={"gain": {"grid": [0.5]}, "seed": {"value": 3}})
    with pytest.raises(CampaignError, match="'seed' is set by replicates"):
        Campaign.load(campaign_file(tmp_path, data))


def test_seed_in_scenario_refused_with_replicates(tmp_path):
    data = {k: v for k, v in with_(replicates=2).items() if k != "parameters"}
    data["scenarios"] = [{"name": "a", "gain": 1.0, "seed": 3}]
    with pytest.raises(CampaignError, match="'seed' is set by replicates"):
        Campaign.load(campaign_file(tmp_path, data))


def test_non_mapping_scenario_with_replicates_is_a_campaign_error(tmp_path):
    data = {k: v for k, v in with_(replicates=2).items() if k != "parameters"}
    data["scenarios"] = [1, 2]
    with pytest.raises(CampaignError, match="scenarios must be a list of mappings"):
        Campaign.load(campaign_file(tmp_path, data))


def test_string_scenario_is_refused_by_shape_not_substring_match(tmp_path):
    data = {k: v for k, v in with_(replicates=2).items() if k != "parameters"}
    data["scenarios"] = ["seedling"]
    with pytest.raises(CampaignError, match="scenarios must be a list of mappings") as e:
        Campaign.load(campaign_file(tmp_path, data))
    assert "set by replicates" not in str(e.value)


def test_replicates_refusal_names_the_campaign_file(tmp_path):
    path = campaign_file(tmp_path, with_(replicates=0))
    with pytest.raises(CampaignError) as e:
        Campaign.load(path)
    assert str(path.resolve()) in str(e.value)


@pytest.mark.parametrize("parameters", [5, "seedling", ["gain"]])
def test_non_mapping_parameters_with_replicates_is_a_campaign_error(tmp_path, parameters):
    data = with_(replicates=2, parameters=parameters)
    with pytest.raises(CampaignError, match="parameters must be a mapping") as e:
        Campaign.load(campaign_file(tmp_path, data))
    assert "set by replicates" not in str(e.value)


def write_stage(root, name, accepts_seed_text):
    d = root / name
    d.mkdir(parents=True)
    (d / "run.py").write_text("print(1)\n")
    body = "name: s\nscript: run.py\noutput_file: out.png\n" + accepts_seed_text
    (d / "stage.yaml").write_text(body)
    return d


def test_accepts_seed_defaults_false_and_must_be_bool(tmp_path):
    assert Stage.load(write_stage(tmp_path, "plain", "")).accepts_seed is False
    assert Stage.load(write_stage(tmp_path, "yes", "accepts_seed: true\n")).accepts_seed is True
    with pytest.raises(StageError, match="accepts_seed"):
        Stage.load(write_stage(tmp_path, "bad", "accepts_seed: \"yes\"\n"))


def test_check_replicates_refuses_deterministic_stage(tmp_path):
    from scieflow.experiments.campaign import check_replicates

    c = Campaign.load(campaign_file(tmp_path, with_(replicates=2)))
    stage_no = Stage.load(write_stage(tmp_path, "plain", ""))
    with pytest.raises(CampaignError, match="accepts_seed: true"):
        check_replicates(c, stage_no)
    stage_yes = Stage.load(write_stage(tmp_path, "seeded", "accepts_seed: true\n"))
    check_replicates(c, stage_yes)  # no error


def test_build_command_appends_seed_after_sorted_params(tmp_path):
    stage_dir = support.add_seeded_stage(tmp_path)
    stage = Stage.load(stage_dir)
    inp = tmp_path / "in.png"
    art = tmp_path / "art"
    seeded = build_command(stage, RunSpec("r", {"gain": 0.5}, seed=1), inp, art, "direct")
    assert seeded[-4:] == ["--gain", "0.5", "--seed", "1"]
    plain = build_command(stage, RunSpec("r", {"gain": 0.5}), inp, art, "direct")
    assert "--seed" not in plain


def test_seeded_sweep_records_config_replicate_and_seed(tmp_path, project):
    support.add_seeded_stage(project)
    camp = support.write_campaign(project, support.campaign_dict(replicates=3))
    campaign = Campaign.load(camp)
    stage = find_stage(project / "pipelines", "demo", "seeded")
    results_path = run_sweep(campaign, stage, project / "experiments", mode="direct")

    results = json.loads(results_path.read_text())
    assert results["replicates"] == {"n": 3, "seed": 0}
    by_id = {r["run_id"]: r for r in results["runs"]}
    r1 = by_id["000_gain-0.5_r1"]
    assert (r1["config_id"], r1["replicate"], r1["seed"]) == ("000_gain-0.5", 1, 1)

    cfg = yaml.safe_load((project / "experiments/rep-sweep/runs/000_gain-0.5_r1/config.yaml").read_text())
    assert (cfg["config_id"], cfg["replicate"], cfg["seed"]) == ("000_gain-0.5", 1, 1)
    camp_yaml = yaml.safe_load((project / "experiments/rep-sweep/campaign.yaml").read_text())
    assert camp_yaml["replicates"] == {"n": 3, "seed": 0}


def test_plain_sweep_records_have_no_new_config_keys(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    cfg = yaml.safe_load((project / "experiments/gain-sweep/runs/000_gain-0.5/config.yaml").read_text())
    assert not {"config_id", "replicate", "seed"} & set(cfg)
    camp_yaml = yaml.safe_load((project / "experiments/gain-sweep/campaign.yaml").read_text())
    assert set(camp_yaml) == {"name", "pipeline", "stage", "input", "reference", "metrics",
                              "parameters", "scenarios", "validation", "rank_by", "notes",
                              "pipeline_dir"}
    results = json.loads((project / "experiments/gain-sweep/results.json").read_text())
    assert "replicates" not in results
    assert results["runs"][0]["replicate"] is None and results["runs"][0]["seed"] is None


def test_seeds_reach_the_stage_and_are_reproducible(project):
    support.add_seeded_stage(project)
    campaign = Campaign.load(support.write_campaign(project, support.campaign_dict(replicates=2)))
    stage = find_stage(project / "pipelines", "demo", "seeded")
    run_sweep(campaign, stage, project / "experiments_a", mode="direct")
    run_sweep(campaign, stage, project / "experiments_b", mode="direct")
    a = project / "experiments_a/rep-sweep/runs/000_gain-0.5_r0/artifacts/result.png"
    b = project / "experiments_b/rep-sweep/runs/000_gain-0.5_r0/artifacts/result.png"
    r1 = project / "experiments_a/rep-sweep/runs/000_gain-0.5_r1/artifacts/result.png"
    assert a.read_bytes() == b.read_bytes()  # same seed, two sweeps: byte-equal
    assert a.read_bytes() != r1.read_bytes()  # different seed: different output


def test_sweep_of_replicates_on_deterministic_stage_is_refused(project):
    camp = support.write_campaign(project, support.campaign_dict(
        stage="scale", replicates=2, name="det-sweep"))
    ws = project / "workspace" / "r1"
    ws.mkdir(parents=True)
    from scieflow.core.run import status, budget
    status.write_status(ws, status.new_status("r1", "autonomous"))
    budget.write_budget(ws, budget.new_budget(3, 10, 600))
    before = events.read(ws)
    result = CliRunner().invoke(experiment, [
        "sweep", "-c", str(camp), "--direct", "--experiments-dir", str(ws / "experiments"),
        "--pipelines-dir", str(project / "pipelines")])
    assert result.exit_code == 1
    assert "accepts_seed: true" in result.output
    assert not (ws / "experiments").exists()
    assert events.read(ws) == before
    assert budget.read_budget(ws)["spent"]["experiment_runs"] == 0
