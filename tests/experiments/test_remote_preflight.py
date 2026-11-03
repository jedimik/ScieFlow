"""M-D3 t3: preflight refusals of `scieflow experiment sweep` for a backend: remote campaign (plan 5.4).

Every refusal here happens before any write, spend or transport call. The fake transport records every
argv, and a refusal must leave it empty (only the local `klist` of the NO_TICKET test is allowed).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

_SUPPORT = Path(__file__).with_name("remote_support.py")
_spec = importlib.util.spec_from_file_location("remote_support", _SUPPORT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)
_SAP = Path(__file__).with_name("sap_support.py")
_sap_spec = importlib.util.spec_from_file_location("sap_support", _SAP)
sup = importlib.util.module_from_spec(_sap_spec)
_sap_spec.loader.exec_module(sup)
import pytest
import yaml
from click.testing import CliRunner

from scieflow.core.run import budget, status
from scieflow.experiments import remote_bundle, remote_campaign
from scieflow.experiments.cli import experiment

REPO = Path(__file__).resolve().parents[2]


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


def test_a_symlink_in_the_stage_folder_is_refused(fake, tmp_path):
    p = rs.make_project(tmp_path)
    stage = p.root / "pipelines" / "demo" / "stages" / "scale"
    (stage / "lib.py").symlink_to(p.root / "pipelines" / "demo" / "data" / "in.txt")
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


def _folder_input_with_link(p) -> None:
    """The reachable symlink case: the input is a folder that holds a link to a file outside it."""
    data = p.root / "pipelines" / "demo" / "data"
    indir = data / "indir"
    indir.mkdir()
    (indir / "a.txt").write_text("synthetic\n")
    (indir / "link.txt").symlink_to(data / "in.txt")
    doc = yaml.safe_load(p.campaign.read_text())
    doc["input"] = "../data/indir"
    p.campaign.write_text(yaml.safe_dump(doc))


def _with_schemas(p) -> None:
    """The design and lock code read the schemas and the reporting checklists of the project root."""
    shutil.copytree(REPO / "schemas", p.root / "schemas")
    shutil.copytree(REPO / "config" / "reporting", p.root / "config" / "reporting")


def test_a_symlink_inside_the_input_folder_is_refused_before_any_write_or_ssh(fake, tmp_path):
    p = rs.make_project(tmp_path)
    _folder_input_with_link(p)
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert_nothing_written(p, fake)
    assert not p.campaign_dir.exists()


def test_a_bundle_failure_leaves_no_campaign_folder_and_no_bundle(fake, tmp_path, monkeypatch):
    def failing_bundle(campaign, stage, runs, mode, bundle, **kwargs):
        bundle.mkdir(parents=True)  # a partial write, then a refusal
        (bundle / "partial.txt").write_text("x")
        raise remote_bundle.BundleError("bundle refused for the test")

    monkeypatch.setattr(remote_bundle, "build_bundle", failing_bundle)
    p = rs.make_project(tmp_path)
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "bundle refused for the test" in result.output
    assert not p.campaign_dir.exists()
    assert not (p.ws / "scratch" / "remote" / rs.CAMPAIGN_ID).exists()
    assert len(refusal_events(p)) == 1


@pytest.mark.parametrize("resources", [{"walltime": "99:00:00"}, {"mem_gb": 999}, {"gpus": 5}])
def test_walltime_mem_and_gpus_over_the_limit_are_refused_not_clamped(fake, tmp_path, resources):
    p = rs.make_project(tmp_path, resources=resources)
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "resources-over-limit" in result.output
    assert "CLAMPED" not in result.output
    assert_nothing_written(p, fake)


def test_scratch_on_the_ssh_adapter_is_refused(fake, tmp_path):
    remote = rs.pbs_remote(scheduler="ssh", auth="none", allow_experimental_scheduler=True,
                           allowed_ops=["check", "mkdir", "rsync", "fetch", "submit", "status"])
    p = rs.make_project(tmp_path, remote=remote,
                        resources={"scratch_type": "scratch_ssd", "scratch_gb": 5})
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "ssh scheduler has no scratch" in result.output
    assert_nothing_written(p, fake)


def test_an_experimental_scheduler_needs_the_opt_in(fake, tmp_path):
    entry = rs.local_remote(str(tmp_path / "rr"))
    entry.pop("allow_experimental_scheduler")
    p = rs.make_project(tmp_path, remote=entry,
                        campaign_remote={"name": "meta", "dir": str(tmp_path / "rr")})
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "experimental" in result.output and "allow_experimental_scheduler" in result.output
    assert_nothing_written(p, fake)


def test_an_unlocked_design_refuses_the_remote_campaign_before_any_write(fake, tmp_path):
    p = rs.make_project(tmp_path)
    _with_schemas(p)
    sup.write_design(p.ws, "rct.yml")
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "design-unlocked" in result.output
    assert refusal_events(p)[0]["data"]["reason"] == "design"
    assert_nothing_written(p, fake)


def test_a_locked_sap_refuses_a_remote_campaign_whose_alpha_conflicts(fake, tmp_path):
    p = rs.make_project(tmp_path)
    _with_schemas(p)
    doc = yaml.safe_load(p.campaign.read_text())
    doc["metrics"] = ["ssim"]
    doc["analysis"] = {"test": {"ssim": "mann-whitney"}, "alpha": 0.05}
    p.campaign.write_text(yaml.safe_dump(doc))
    sup.write_design(p.ws, "rct.yml", sup.sap_rct(method="holm", alpha=0.04))
    sup.lock_as_human(p.ws)
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "sap-alpha-mismatch" in result.output
    assert_nothing_written(p, fake)


def test_an_input_inside_another_l2_run_is_refused_for_a_remote_not_listed(fake, tmp_path):
    p = rs.make_project(tmp_path)
    other = p.root / "workspace" / "r2"
    (other / "data").mkdir(parents=True)
    (other / "data" / "in.txt").write_text("synthetic\n")
    (other / "data.yml").write_text("version: 1\ndefault_level: L2\nlevels: []\n")
    doc = yaml.safe_load(p.campaign.read_text())
    doc["input"] = "../../../workspace/r2/data/in.txt"
    p.campaign.write_text(yaml.safe_dump(doc))
    result = sweep(p, "--direct")
    assert result.exit_code == 1, result.output
    assert "data protection" in result.output and "r2" in result.output
    assert_nothing_written(p, fake)


def _build_no_run(tmp_path):
    return rs.make_project(tmp_path, in_run=False), ["--direct"]


def _build_unknown_remote(tmp_path):
    return rs.make_project(tmp_path, remote_name="other"), ["--direct"]


def _build_dir_outside(tmp_path):
    return rs.make_project(tmp_path, campaign_remote={"name": "meta", "dir": "/scratch/elsewhere"}), ["--direct"]


def _build_queue(tmp_path):
    return rs.make_project(tmp_path, resources={"queue": "gpu"}), ["--direct"]


def _build_over_limit(tmp_path):
    return rs.make_project(tmp_path, resources={"cpus": 99}), ["--direct"]


def _build_symlink(tmp_path):
    p = rs.make_project(tmp_path)
    _folder_input_with_link(p)
    return p, ["--direct"]


def _build_sif(tmp_path):
    return rs.make_project(tmp_path), []  # apptainer mode without a built .sif


@pytest.mark.parametrize("build,rule", [
    (_build_no_run, "no-run"),
    (_build_unknown_remote, "policy"),
    (_build_dir_outside, "policy"),
    (_build_queue, "policy"),
    (_build_over_limit, "resources-over-limit"),
    (_build_symlink, "symlink"),
    (_build_sif, "sif-missing"),
])
def test_each_refusal_emits_one_job_refused_with_its_rule(fake, tmp_path, build, rule):
    p, extra = build(tmp_path)
    result = sweep(p, *extra)
    assert result.exit_code == 1, result.output
    if rule == "no-run":
        assert fake.calls == [] and not p.campaign_dir.exists()
        return
    refused = refusal_events(p)
    assert len(refused) == 1, refused
    assert refused[0]["data"]["reason"] == "remote"
    assert refused[0]["data"]["rule"] == rule
    assert fake.calls == []


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
