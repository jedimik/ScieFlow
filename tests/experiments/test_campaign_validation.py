from pathlib import Path

import pytest
import yaml

from scieflow.experiments.campaign import Campaign, CampaignError

ROOT = Path(__file__).resolve().parents[2]
SIGMA_SWEEP = ROOT / "pipelines" / "denoise" / "campaigns" / "sigma-sweep.yaml"

BASE = {
    "name": "sweep",
    "pipeline": "demo",
    "stage": "scale",
    "input": "data/noisy.png",
    "parameters": {"sigma": {"grid": [0.5, 1.0]}},
    "metrics": ["ssim"],
}


def write(tmp_path, data):
    p = tmp_path / "campaign.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_backend_remote_is_refused_with_pointer(tmp_path):
    with pytest.raises(CampaignError, match="skills/remote-exec/SKILL.md"):
        Campaign.load(write(tmp_path, BASE | {"backend": "remote"}))


def test_remote_only_keys_are_refused_with_pointer(tmp_path):
    for extra in ({"remote": "meta"}, {"resources": {"cpus": 4}}):
        with pytest.raises(CampaignError, match="skills/remote-exec/SKILL.md"):
            Campaign.load(write(tmp_path, BASE | extra))


def test_typo_key_is_refused_by_name(tmp_path):
    with pytest.raises(CampaignError, match="backnd"):
        Campaign.load(write(tmp_path, BASE | {"backnd": "local"}))


def test_resource_key_without_backend_gets_generic_refusal(tmp_path):
    with pytest.raises(CampaignError, match="walltime"):
        Campaign.load(write(tmp_path, BASE | {"walltime": "1h"}))


def test_unknown_keys_are_listed_sorted(tmp_path):
    with pytest.raises(CampaignError, match="alpha, zeta"):
        Campaign.load(write(tmp_path, BASE | {"zeta": 1, "alpha": 2}))


def test_unsupported_backend_value_is_refused(tmp_path):
    with pytest.raises(CampaignError, match="backend 'slurm'"):
        Campaign.load(write(tmp_path, BASE | {"backend": "slurm"}))


def test_backend_local_loads(tmp_path):
    c = Campaign.load(write(tmp_path, BASE | {"backend": "local"}))
    assert [s.run_id for s in c.expand()] == ["000_sigma-0.5", "001_sigma-1.0"]


def test_recorded_campaign_with_pipeline_dir_loads(tmp_path):
    recorded = Campaign.load(write(tmp_path, BASE)).to_dict()
    recorded["pipeline_dir"] = "/x/pipelines/demo"
    c = Campaign.load(write(tmp_path, recorded))
    assert [s.run_id for s in c.expand()] == ["000_sigma-0.5", "001_sigma-1.0"]


def test_committed_sigma_sweep_loads_unchanged():
    c = Campaign.load(SIGMA_SWEEP)
    assert c.stage == "denoise"
    assert len(c.expand()) == 7
