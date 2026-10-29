"""M-D2 t2: run manifest.json (plan 5.3) — fields, hashes, git state, packages, context."""

import getpass
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from scieflow.experiments import manifest, runner
from scieflow.experiments.campaign import Campaign, RunSpec
from scieflow.experiments.runner import execute_run
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("manifest_support.py")
_spec = importlib.util.spec_from_file_location("manifest_support", _SUPPORT)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

REQUIRED = {
    "version", "run_id", "campaign", "config_id", "replicate", "seed", "params", "mode",
    "status", "returncode", "started", "finished", "wall_seconds", "code", "stage", "inputs",
    "environment", "packages", "data_level", "artifacts", "metrics_sha256", "metrics_history",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(project):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    return campaign, stage


def sweep_dir(project, mode="direct"):
    campaign, stage = load(project)
    results = run_sweep(campaign, stage, project / "experiments", mode=mode)
    return results.parent


def read_manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "manifest.json").read_text())


def test_direct_sweep_manifest_fields_present_and_hashed(project):
    S.init_repo(project)
    camp = sweep_dir(project)
    results = json.loads((camp / "results.json").read_text())
    head = S.git(project, "rev-parse", "HEAD").stdout.strip()
    for entry in results["runs"]:
        run_dir = camp / "runs" / entry["run_id"]
        raw = (run_dir / "manifest.json").read_bytes()
        m = json.loads(raw)
        assert set(m) >= REQUIRED
        assert m["version"] == 1 and m["run_id"] == entry["run_id"] and m["campaign"] == "gain-sweep"
        assert m["code"]["pipeline"]["git"] == {"sha": head, "dirty": False}
        assert entry["manifest_sha256"] == sha(raw)
        data = project / "pipelines/demo/data"
        assert m["inputs"]["input"]["sha256"] == sha((data / "noisy.png").read_bytes())
        assert m["inputs"]["reference"]["sha256"] == sha((data / "truth.png").read_bytes())
        for artifact in m["artifacts"]:
            assert artifact["sha256"] == sha((run_dir / "artifacts" / artifact["path"]).read_bytes())
        assert m["metrics_sha256"] == sha((run_dir / "metrics.json").read_bytes())
        assert m["metrics_history"] == []
        assert m["status"] == "completed" and m["returncode"] == 0
        assert m["stage"]["name"] == "scale" and len(m["stage"]["script_sha256"]) == 64
        assert m["packages"]["status"] == "captured"
        assert m["seed"] is None and m["replicate"] is None and m["config_id"] == entry["run_id"]


def test_untracked_pipeline_file_does_not_make_it_dirty(project):
    S.init_repo(project)
    campaign, stage = load(project)
    camp = project / "experiments" / "gain-sweep"
    (stage.dir / "notes.txt").write_text("untracked scratch note\n")
    ctx = manifest.capture_context(campaign, stage, "direct", camp)
    assert ctx["code"]["pipeline"]["git"]["dirty"] is False


def test_edited_tracked_pipeline_file_makes_it_dirty(project):
    S.init_repo(project)
    campaign, stage = load(project)
    camp = project / "experiments" / "gain-sweep"
    run_py = stage.dir / "run.py"
    run_py.write_text(run_py.read_text() + "\n# edited after commit\n")
    ctx = manifest.capture_context(campaign, stage, "direct", camp)
    assert ctx["code"]["pipeline"]["git"]["dirty"] is True


def test_probe_not_tracked_gives_no_sha(tmp_path):
    repo = tmp_path / "repo"
    (repo / "stages" / "x").mkdir(parents=True)
    (repo / ".gitignore").write_text("stages/\n")
    (repo / "keep.txt").write_text("tracked\n")
    S.init_repo(repo)
    (repo / "stages" / "x" / "stage.yaml").write_text("name: x\n")
    state, reason = manifest.git_state(repo / "stages" / "x", repo / "stages" / "x" / "stage.yaml")
    assert state is None and reason == "not-tracked"


def test_outside_a_repository_gives_reason_not_sha(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "stage.yaml").write_text("name: x\n")
    state, reason = manifest.git_state(plain, plain / "stage.yaml")
    assert state is None and reason == "not-a-worktree"


def test_git_timeout_is_recorded_and_run_still_completes(project, monkeypatch):
    campaign, stage = load(project)
    real = S.FakeApptainer().run

    def run(cmd, **kwargs):
        if cmd[0] == "git":
            raise subprocess.TimeoutExpired(cmd, 10)
        return real(cmd, **kwargs)

    S.patch_subprocess(monkeypatch, manifest, run)
    spec = RunSpec(run_id="000_gain-1.0", params={"gain": 1.0})
    run_dir = execute_run(campaign, stage, spec, project / "experiments", mode="direct")
    m = read_manifest(run_dir)
    assert m["code"]["pipeline"]["git"] is None
    assert m["code"]["pipeline"]["reason"] == "timeout"
    assert yaml.safe_load((run_dir / "config.yaml").read_text())["status"] == "completed"


def test_directory_tree_digest_changes_on_edit_and_rename_and_skips_symlink_target(tmp_path):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("1")
    (d / "sub" / "b.txt").write_text("2")
    h0 = manifest.tree_digest(d)
    assert manifest.digest(d)["kind"] == "dir"
    (d / "a.txt").write_text("changed")
    h1 = manifest.tree_digest(d)["sha256"]
    assert h1 != h0["sha256"]
    (d / "sub" / "b.txt").rename(d / "sub" / "c.txt")
    h2 = manifest.tree_digest(d)["sha256"]
    assert h2 != h1
    target = tmp_path / "outside.txt"
    target.write_text("x")
    (d / "link").symlink_to(target)
    assert manifest.tree_digest(d)["files"] == 2  # the symlink is recorded, not counted or followed
    before = manifest.tree_digest(d)["sha256"]
    target.write_text("a different outside content")
    assert manifest.tree_digest(d)["sha256"] == before
    assert manifest.digest(tmp_path / "nope") == {"kind": "missing"}


def test_direct_packages_are_sorted_unique_and_include_numpy(tmp_path):
    pk = manifest.packages("direct", None)
    assert pk["status"] == "captured"
    lines = pk["list"]
    assert lines == sorted(set(lines))
    assert f"numpy=={importlib.metadata.version('numpy')}" in lines
    assert pk["count"] == len(lines)
    assert pk["sha256"] == sha(("\n".join(lines) + "\n").encode())


def test_apptainer_sweep_captures_packages_once_and_writes_one_file(project, monkeypatch):
    sif = S.make_sif(project)
    fake = S.FakeApptainer()
    S.patch_subprocess(monkeypatch, runner, fake.run)
    S.patch_subprocess(monkeypatch, manifest, fake.run)
    camp = sweep_dir(project, mode="apptainer")
    pip_calls = [c for c in fake.calls if "pip" in c]
    assert len(pip_calls) == 1
    pkg_files = sorted((camp / "packages").glob("*.txt"))
    assert [p.name for p in pkg_files] == [f"{sha(sif.read_bytes())[:12]}.txt"]
    text = pkg_files[0].read_bytes()
    assert b"numpy==2.2.1" in text
    results = json.loads((camp / "results.json").read_text())
    for entry in results["runs"]:
        assert entry["status"] == "completed"
        m = read_manifest(camp / "runs" / entry["run_id"])
        assert m["packages"]["status"] == "captured"
        assert m["packages"]["file"] == f"packages/{pkg_files[0].name}"
        assert m["packages"]["sha256"] == sha(text)
        assert "list" not in m["packages"]


@pytest.mark.parametrize("behaviour,reason", [
    ("exit", "exit-1"),
    ("missing", "missing-binary"),
    ("timeout", "timeout"),
])
def test_apptainer_capture_failures_are_recorded_not_raised(tmp_path, monkeypatch, behaviour, reason):
    sif = tmp_path / "x.sif"
    sif.write_bytes(b"s")
    if behaviour == "exit":
        fake = S.FakeApptainer(pip_rc=1)
    elif behaviour == "missing":
        fake = S.FakeApptainer(pip_exc=FileNotFoundError("apptainer"))
    else:
        fake = S.FakeApptainer(pip_exc=subprocess.TimeoutExpired(["apptainer"], 120))
    S.patch_subprocess(monkeypatch, manifest, fake.run)
    pk = manifest.packages("apptainer", sif)
    assert pk["status"] == "not-captured"
    assert pk["reason"] == reason


def test_packages_capture_failure_never_fails_a_sweep(project, monkeypatch):
    S.make_sif(project)
    fake = S.FakeApptainer(pip_rc=1)
    S.patch_subprocess(monkeypatch, runner, fake.run)
    S.patch_subprocess(monkeypatch, manifest, fake.run)
    camp = sweep_dir(project, mode="apptainer")
    results = json.loads((camp / "results.json").read_text())
    assert [e["status"] for e in results["runs"]] == ["completed", "completed"]
    assert read_manifest(camp / "runs" / results["runs"][0]["run_id"])["packages"]["status"] == "not-captured"


def test_manifest_has_no_host_user_or_environment_details(project):
    S.init_repo(project)
    camp = sweep_dir(project)
    results = json.loads((camp / "results.json").read_text())
    user = getpass.getuser()
    for entry in results["runs"]:
        text = (camp / "runs" / entry["run_id"] / "manifest.json").read_text()
        assert socket.gethostname() not in text
        assert user not in text
        assert os.environ.get("HOME", "/no-home") not in text


def test_context_is_computed_once_per_sweep(project, monkeypatch):
    S.init_repo(project)
    campaign, stage = load(project)
    seen = {"git": 0, "digest": []}
    real_git, real_digest = manifest.git_state, manifest.digest

    def spy_git(path, probe):
        seen["git"] += 1
        return real_git(path, probe)

    def spy_digest(path):
        seen["digest"].append(Path(path))
        return real_digest(path)

    monkeypatch.setattr(manifest, "git_state", spy_git)
    monkeypatch.setattr(manifest, "digest", spy_digest)
    run_sweep(campaign, stage, project / "experiments", mode="direct")
    assert seen["git"] == 2  # scieflow package + pipeline, once for the whole sweep
    assert seen["digest"].count(campaign.input) == 1
    assert seen["digest"].count(campaign.reference) == 1


def test_failed_run_still_gets_a_manifest(project):
    campaign, stage = load(project)
    run_dir = execute_run(
        campaign, stage, RunSpec(run_id="000_bad", params={"gain": "not-a-float"}),
        project / "experiments", mode="direct")
    m = read_manifest(run_dir)
    assert m["status"] == "failed"
    assert m["returncode"] != 0
    assert m["metrics_sha256"] is None


def test_seed_replicate_and_config_id_are_recorded(project):
    campaign, stage = load(project)
    spec = RunSpec(run_id="000_gain-1.0_r2", params={"gain": 1.0},
                   config_id="000_gain-1.0", replicate=2, seed=7)
    run_dir = execute_run(campaign, stage, spec, project / "experiments", mode="direct")
    m = read_manifest(run_dir)
    assert (m["seed"], m["replicate"], m["config_id"]) == (7, 2, "000_gain-1.0")
