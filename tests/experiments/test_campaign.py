from pathlib import Path

import pytest
import yaml

from scieflow.experiments.campaign import Campaign, CampaignError

CAMPAIGN = {
    "name": "sweep",
    "pipeline": "demo",
    "stage": "scale",
    "input": "data/noisy.png",
    "reference": "data/truth.png",
    "parameters": {"sigma": {"grid": [0.5, 1.0]}, "mode": {"value": "fast"}},
    "metrics": ["ssim", "mse"],
    "validation": {"ssim": {"min": 0.7}},
    "notes": "test campaign",
}


def write_campaign(tmp_path, data):
    p = tmp_path / "campaign.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_load_resolves_paths_relative_to_file(tmp_path):
    c = Campaign.load(write_campaign(tmp_path, CAMPAIGN))
    assert c.name == "sweep"
    assert c.input == (tmp_path / "data/noisy.png").resolve()
    assert c.reference == (tmp_path / "data/truth.png").resolve()
    assert c.rank_by == "ssim"  # defaults to first metric


def test_load_missing_field_raises(tmp_path):
    bad = {k: v for k, v in CAMPAIGN.items() if k != "stage"}
    with pytest.raises(CampaignError, match="stage"):
        Campaign.load(write_campaign(tmp_path, bad))


def test_load_rejects_both_parameters_and_scenarios(tmp_path):
    bad = dict(CAMPAIGN, scenarios=[{"name": "a", "sigma": 1}])
    with pytest.raises(CampaignError, match="exactly one"):
        Campaign.load(write_campaign(tmp_path, bad))


def test_expand_grid_cartesian_with_fixed(tmp_path):
    data = dict(CAMPAIGN)
    data["parameters"] = {
        "sigma": {"grid": [0.5, 1.0]},
        "k": {"grid": [3, 5]},
        "mode": {"value": "fast"},
    }
    specs = Campaign.load(write_campaign(tmp_path, data)).expand()
    assert len(specs) == 4
    assert all(s.params["mode"] == "fast" for s in specs)
    assert specs[0].run_id == "000_k-3_sigma-0.5"
    assert {(s.params["sigma"], s.params["k"]) for s in specs} == {
        (0.5, 3), (0.5, 5), (1.0, 3), (1.0, 5)
    }


def test_expand_scenarios(tmp_path):
    data = {k: v for k, v in CAMPAIGN.items() if k != "parameters"}
    data["scenarios"] = [{"name": "light", "sigma": 0.5}, {"name": "heavy", "sigma": 3.0}]
    specs = Campaign.load(write_campaign(tmp_path, data)).expand()
    assert [s.run_id for s in specs] == ["000_light", "001_heavy"]
    assert specs[1].params == {"sigma": 3.0}


def test_expand_empty_grid_raises(tmp_path):
    data = dict(CAMPAIGN)
    data["parameters"] = {"sigma": {"grid": []}}
    c = Campaign.load(write_campaign(tmp_path, data))
    with pytest.raises(CampaignError, match="empty grid"):
        c.expand()


def test_to_dict_has_absolute_paths(tmp_path):
    c = Campaign.load(write_campaign(tmp_path, CAMPAIGN))
    d = c.to_dict()
    assert Path(d["input"]).is_absolute()
    reloaded = yaml.safe_load(yaml.safe_dump(d))
    assert reloaded["name"] == "sweep"
