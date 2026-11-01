"""M-D3 t1: `backend: remote` campaign keys load, round-trip and refuse by key name (plan 5.1)."""

import pytest
import yaml
from click.testing import CliRunner

from scieflow.experiments.campaign import Campaign, CampaignError, RemoteTarget, Resources
from scieflow.experiments.cli import experiment

BASE = {
    "name": "sweep",
    "pipeline": "demo",
    "stage": "scale",
    "input": "data/noisy.png",
    "parameters": {"sigma": {"grid": [0.5, 1.0]}},
    "metrics": ["ssim"],
}
REMOTE = {"remote": {"name": "meta", "dir": "/data/x"}}


def write(tmp_path, data, name="campaign.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


def test_remote_campaign_loads_with_defaults(tmp_path):
    c = Campaign.load(write(tmp_path, BASE | {"backend": "remote"} | REMOTE))
    assert c.backend == "remote"
    assert c.remote == RemoteTarget(name="meta", dir="/data/x", jobs="per-run")
    assert c.resources == Resources(
        walltime="01:00:00", cpus=1, mem_gb=4, gpus=0,
        queue="default", scratch_type="none", scratch_gb=0,
    )


def test_remote_resources_override_and_jobs_array(tmp_path):
    data = BASE | {
        "backend": "remote",
        "remote": {"name": "meta", "dir": "/data/x", "jobs": "array"},
        "resources": {"walltime": "02:30:00", "cpus": 4, "mem_gb": 16, "gpus": 1,
                      "queue": "gpu", "scratch_type": "scratch_local", "scratch_gb": 10},
    }
    c = Campaign.load(write(tmp_path, data))
    assert c.remote.jobs == "array"
    assert c.resources.cpus == 4 and c.resources.gpus == 1
    assert c.resources.walltime == "02:30:00" and c.resources.scratch_gb == 10


def test_local_campaign_has_no_remote_fields(tmp_path):
    c = Campaign.load(write(tmp_path, BASE))
    assert c.backend == "local"
    assert c.remote is None and c.resources is None
    assert set(c.to_dict()) == {
        "name", "pipeline", "stage", "input", "reference", "metrics", "parameters",
        "scenarios", "validation", "rank_by", "notes",
    }


def test_remote_campaign_round_trips_through_campaign_yaml(tmp_path):
    original = Campaign.load(write(tmp_path, BASE | {"backend": "remote"} | REMOTE))
    recorded = original.to_dict()
    assert recorded["backend"] == "remote"
    assert recorded["remote"] == {"name": "meta", "dir": "/data/x", "jobs": "per-run"}
    assert recorded["resources"]["cpus"] == 1
    reloaded = Campaign.load(write(tmp_path, recorded, name="copy.yaml"))
    assert reloaded.to_dict() == recorded
    assert reloaded.remote == original.remote
    assert reloaded.resources == original.resources


def test_backend_remote_without_remote_block_names_remote(tmp_path):
    with pytest.raises(CampaignError, match="'remote' mapping"):
        Campaign.load(write(tmp_path, BASE | {"backend": "remote"}))


@pytest.mark.parametrize("extra, key", [
    ({"remote": "meta"}, "remote"),
    ({"remote": {"name": "meta", "dir": "/data/x", "queue": "gpu"}}, "queue"),
    ({"remote": {"name": "a b", "dir": "/data/x"}}, "name"),
    ({"remote": {"name": "meta", "dir": "data/x"}}, "dir"),
    ({"remote": {"name": "meta", "dir": "/data/../x"}}, "dir"),
    ({"remote": {"name": "meta", "dir": "/"}}, "dir"),
    ({"remote": {"name": "meta", "dir": "/a;b"}}, "dir"),
    ({"remote": {"name": "meta", "dir": "/data//x"}}, "dir"),
    ({"remote": {"name": "meta", "dir": "/data/x", "jobs": "parallel"}}, "jobs"),
    ({"resources": {"gpu": 1}}, "gpu"),
    ({"resources": {"cpus": 0}}, "cpus"),
    ({"resources": {"cpus": True}}, "cpus"),
    ({"resources": {"mem_gb": "4"}}, "mem_gb"),
    ({"resources": {"gpus": -1}}, "gpus"),
    ({"resources": {"walltime": "1h"}}, "walltime"),
    ({"resources": {"queue": "a b"}}, "queue"),
    ({"resources": {"scratch_gb": -1}}, "scratch_gb"),
])
def test_bad_remote_keys_are_refused_by_name(tmp_path, extra, key):
    with pytest.raises(CampaignError, match=key):
        Campaign.load(write(tmp_path, BASE | {"backend": "remote"} | REMOTE | extra))


def test_remote_run_ids_must_be_path_safe(tmp_path):
    data = BASE | {
        "backend": "remote", "parameters": None,
        "scenarios": [{"name": "a b", "sigma": 1}],
    } | REMOTE
    with pytest.raises(CampaignError, match="a b"):
        Campaign.load(write(tmp_path, data))


def test_same_scenario_still_loads_as_local(tmp_path):
    data = BASE | {"parameters": None, "scenarios": [{"name": "a b", "sigma": 1}]}
    c = Campaign.load(write(tmp_path, data))
    assert [s.run_id for s in c.expand()] == ["000_a b"]


def test_remote_keys_without_backend_remote_are_refused(tmp_path):
    with pytest.raises(CampaignError, match="need backend: remote"):
        Campaign.load(write(tmp_path, BASE | REMOTE))


def test_run_refuses_a_remote_campaign_without_running(project):
    campaign = project / "pipelines/demo/campaigns/remote-sweep.yaml"
    campaign.write_text(yaml.safe_dump(
        BASE | {"backend": "remote"} | REMOTE | {"input": str(project / "pipelines/demo/data/noisy.png")}
    ))
    exp = project / "experiments"
    result = CliRunner().invoke(experiment, [
        "run", "-c", str(campaign), "--experiments-dir", str(exp),
        "--pipelines-dir", str(project / "pipelines"), "--direct",
    ])
    assert result.exit_code == 1
    assert "runs locally only; submit a backend: remote campaign with sweep" in result.output
    assert not (exp / "sweep" / "runs").exists()


def test_sweep_refuses_a_remote_campaign_without_running(project):
    campaign = project / "pipelines/demo/campaigns/remote-sweep.yaml"
    campaign.write_text(yaml.safe_dump(
        BASE | {"backend": "remote"} | REMOTE | {"input": str(project / "pipelines/demo/data/noisy.png")}
    ))
    exp = project / "experiments"
    result = CliRunner().invoke(experiment, [
        "sweep", "-c", str(campaign), "--experiments-dir", str(exp),
        "--pipelines-dir", str(project / "pipelines"), "--direct",
    ])
    assert result.exit_code == 1
    assert "remote campaigns are submitted by sweep" in result.output
    assert not (exp / "sweep" / "runs").exists()
    assert not (exp / "sweep" / "results.json").exists()
