"""M-D3 t1: `backend: remote` campaign keys load, round-trip and refuse by key name (plan 5.1)."""

import re

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


@pytest.mark.parametrize("extra, fragment", [
    ({"remote": "meta"}, "'remote' mapping"),
    ({"remote": {"name": "meta", "dir": "/data/x", "queue": "gpu"}}, "remote: unknown key(s) queue"),
    ({"remote": {"name": "a b", "dir": "/data/x"}}, "remote.name must be"),
    ({"remote": {"name": "meta", "dir": "data/x"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "/data/../x"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "/"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "/a;b"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "/data//x"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "//data/x"}}, "remote.dir must be"),
    ({"remote": {"name": "meta", "dir": "/data/x", "jobs": "parallel"}}, "remote.jobs must be"),
    ({"resources": {"gpu": 1}}, "resources: unknown key(s) gpu"),
    ({"resources": {"cpus": 0}}, "resources.cpus must be"),
    ({"resources": {"cpus": True}}, "resources.cpus must be"),
    ({"resources": {"mem_gb": "4"}}, "resources.mem_gb must be"),
    ({"resources": {"gpus": -1}}, "resources.gpus must be"),
    ({"resources": {"walltime": "1h"}}, "resources.walltime must be"),
    ({"resources": {"queue": "a b"}}, "resources.queue must be"),
    ({"resources": {"scratch_gb": -1}}, "resources.scratch_gb must be"),
])
def test_bad_remote_keys_are_refused_by_name(tmp_path, extra, fragment):
    # Match the qualified key the message states, not a bare word: a bare key such as
    # "name" also matches the pytest tmp path and passes for any CampaignError (review r1).
    with pytest.raises(CampaignError, match=re.escape(fragment)):
        Campaign.load(write(tmp_path, BASE | {"backend": "remote"} | REMOTE | extra))


@pytest.mark.parametrize("name", ["my campaign", "a/b", "..", ".", 12])
def test_remote_campaign_name_must_be_path_safe(tmp_path, name):
    data = BASE | {"name": name, "backend": "remote"} | REMOTE
    with pytest.raises(CampaignError, match=re.escape("campaign name")):
        Campaign.load(write(tmp_path, data))


def test_same_campaign_names_still_load_as_local(tmp_path):
    for name in ("my campaign", "a/b"):
        c = Campaign.load(write(tmp_path, BASE | {"name": name}))
        assert c.name == name and c.backend == "local"


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


def refuse_any_execution(*args, **kwargs):
    raise AssertionError("a remote campaign must not execute a run locally")


def spy_on_execution_and_spend(monkeypatch):
    """Fail on any local execute_run; record every budget spend (plan 6 t1 case 6, review r1)."""
    for target in ("scieflow.experiments.cli.execute_run",
                   "scieflow.experiments.sweep.execute_run",
                   "scieflow.experiments.runner.execute_run"):
        monkeypatch.setattr(target, refuse_any_execution)
    spent = []
    monkeypatch.setattr("scieflow.core.run.actions.record_spend",
                        lambda *a, **k: spent.append((a, k)))
    return spent


def test_run_refuses_a_remote_campaign_without_running(project, monkeypatch):
    spent = spy_on_execution_and_spend(monkeypatch)
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
    assert spent == []


def test_sweep_refuses_a_remote_campaign_without_running(project, monkeypatch):
    spent = spy_on_execution_and_spend(monkeypatch)
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
    assert spent == []
