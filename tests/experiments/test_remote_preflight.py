"""M-D3 t3: preflight refusals of `scieflow experiment sweep` for a backend: remote campaign (plan 5.4).

Every refusal here happens before any write, spend or transport call. The fake transport records every
argv, and a refusal must leave it empty (only the local `klist` of the NO_TICKET test is allowed).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SUPPORT = Path(__file__).with_name("remote_support.py")
_spec = importlib.util.spec_from_file_location("remote_support", _SUPPORT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)
import pytest
from click.testing import CliRunner

from scieflow.core.run import budget, status
from scieflow.experiments import remote_campaign
from scieflow.experiments.cli import experiment


@pytest.fixture
def fake(monkeypatch):
    f = rs.FakeRemote()
    monkeypatch.setattr(remote_campaign, "RUNNER", f)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    return f


def sweep(p, *extra):
    args = ["sweep", "-c", str(p.campaign), "--experiments-dir", str(p.experiments),
            "--pipelines-dir", str(p.root / "pipelines"), *extra]
    return CliRunner().invoke(experiment, args)


def refusal_events(p) -> list[dict]:
    return [e for e in rs.read_events(p.ws) if e["type"] == "job.refused"]


def assert_nothing_written(p, fake) -> None:
    assert fake.calls == []
    assert not p.campaign_dir.exists()
    assert not (p.ws / "remote" / "jobs.yml").exists()
    assert not (p.ws / "scratch" / "remote").exists()
    assert budget.read_budget(p.ws)["spent"]["experiment_runs"] == 0


def test_refused_outside_a_run(fake, tmp_path):
    p = rs.make_project(tmp_path, in_run=False)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "no-run" in result.output
    assert fake.calls == []
    assert not p.campaign_dir.exists()


def test_refused_when_the_campaign_folder_exists(fake, tmp_path):
    p = rs.make_project(tmp_path)
    p.campaign_dir.mkdir(parents=True)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "campaign-exists" in result.output
    assert fake.calls == []
    assert [e["data"]["reason"] for e in refusal_events(p)] == ["remote"]
    assert refusal_events(p)[0]["data"]["rule"] == "campaign-exists"


def test_refused_without_config_remotes_yml(fake, tmp_path):
    p = rs.make_project(tmp_path, remotes_file=False)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "POLICY:" in result.output and "remotes.yml" in result.output
    assert_nothing_written(p, fake)


def test_refused_for_an_unknown_remote(fake, tmp_path):
    p = rs.make_project(tmp_path, remote_name="other")
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "POLICY:" in result.output and "'other' not defined" in result.output
    assert_nothing_written(p, fake)


def test_every_missing_op_is_listed_in_one_message(fake, tmp_path):
    p = rs.make_project(tmp_path, remote=rs.pbs_remote(allowed_ops=["check", "qsub", "qstat"]))
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    for op in ("mkdir", "rsync", "fetch"):
        assert f"'{op}'" in result.output
    assert_nothing_written(p, fake)


def test_refused_for_a_dir_outside_allowed_dirs(fake, tmp_path):
    p = rs.make_project(tmp_path, campaign_remote={"name": "meta", "dir": "/scratch/elsewhere"})
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "outside allowed_dirs" in result.output
    assert_nothing_written(p, fake)


def test_refused_for_a_queue_not_allowed(fake, tmp_path):
    p = rs.make_project(tmp_path, resources={"queue": "gpu"})
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "queue 'gpu' not allowed" in result.output
    assert_nothing_written(p, fake)


def test_over_limit_resources_are_refused_not_clamped(fake, tmp_path):
    p = rs.make_project(tmp_path, resources={"cpus": 99})
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "resources-over-limit" in result.output
    assert "cpus 99 clamped to 16" in result.output
    assert "CLAMPED" not in result.output
    assert_nothing_written(p, fake)


def test_exhausted_budget_refuses_and_checkpoints(fake, tmp_path):
    p = rs.make_project(tmp_path, cap=2, spent=2)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "exhausted" in result.output
    assert status.read_status(p.ws).get("stopped")
    assert fake.calls == [] and not p.campaign_dir.exists()


def test_insufficient_budget_refuses_with_a_job_refused_event(fake, tmp_path):
    p = rs.make_project(tmp_path, cap=3, n_gains=5)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "insufficient" in result.output
    refused = refusal_events(p)
    assert refused and refused[-1]["data"]["reason"] == "budget"
    assert fake.calls == [] and not p.campaign_dir.exists()
    assert budget.read_budget(p.ws)["spent"]["experiment_runs"] == 0


def test_an_l2_run_is_refused_unless_the_remote_is_listed(fake, tmp_path):
    p = rs.make_project(tmp_path)
    (p.ws / "data.yml").write_text("version: 1\ndefault_level: L2\nlevels: []\n")
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "data protection" in result.output
    assert_nothing_written(p, fake)


def test_a_symlink_in_the_input_is_refused(fake, tmp_path):
    p = rs.make_project(tmp_path)
    data = p.root / "pipelines" / "demo" / "data"
    (data / "real.txt").write_text("synthetic\n")
    (data / "in.txt").unlink()
    (data / "in.txt").symlink_to(data / "real.txt")
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "symlink" in result.output
    assert_nothing_written(p, fake)


def test_apptainer_without_a_built_sif_is_refused(fake, tmp_path):
    p = rs.make_project(tmp_path)
    result = sweep(p)  # no --direct: the container mode needs the .sif
    assert result.exit_code == 1
    assert "not built" in result.output
    assert_nothing_written(p, fake)


def test_no_ticket_stops_before_any_write_or_ssh(tmp_path, monkeypatch):
    fake = rs.FakeRemote(klist_rc=1)
    monkeypatch.setattr(remote_campaign, "RUNNER", fake)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    p = rs.make_project(tmp_path)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert "kinit" in result.output
    assert fake.calls == [["klist", "-s"]]
    assert fake.ssh_commands() == []
    assert not p.campaign_dir.exists()
    assert budget.read_budget(p.ws)["spent"]["experiment_runs"] == 0
