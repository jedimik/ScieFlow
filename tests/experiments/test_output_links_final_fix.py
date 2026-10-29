"""M-D2 final review r1, finding 1: every M-D2 writer refuses a pre-planted symlink.

A symlink at <name> or at <name>.tmp must never be followed: the victim keeps its bytes, nothing
else is written and the writer refuses (plan 5.12, "symlinked outputs refused"). Ordinary paths
stay byte-identical.
"""

import importlib.util
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.experiments import analysis, manifest, runner
from scieflow.experiments.campaign import Campaign
from scieflow.experiments.cli import experiment
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

_SUPPORT = Path(__file__).with_name("stats_support.py")
_spec = importlib.util.spec_from_file_location("stats_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

NEW = {"ssim": 0.123, "mse": 4.5}


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    return victim


def _snapshot(paths):
    return {p: p.read_bytes() for p in paths if p.is_file()}


def _sweep(project) -> Path:
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    return run_sweep(campaign, stage, project / "experiments", mode="direct").parent


def test_atomic_write_refuses_a_symlinked_tmp_and_writes_nothing(tmp_path):
    victim = _victim(tmp_path)
    target = tmp_path / "out.json"
    (tmp_path / "out.json.tmp").symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        manifest.atomic_write(target, b"new\n")
    assert victim.read_text() == "keep me\n"
    assert not target.exists()


def test_atomic_write_refuses_a_symlinked_final_path(tmp_path):
    victim = _victim(tmp_path)
    target = tmp_path / "out.json"
    target.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        manifest.atomic_write(target, b"new\n")
    assert victim.read_text() == "keep me\n"


def test_atomic_write_refuses_a_directory_at_the_path(tmp_path):
    (tmp_path / "out.json").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        manifest.atomic_write(tmp_path / "out.json", b"x")


def test_atomic_write_is_byte_exact_and_replaces_a_stale_regular_tmp(tmp_path):
    target = tmp_path / "out.json"
    target.write_bytes(b"old")
    (tmp_path / "out.json.tmp").write_bytes(b"stale")
    manifest.atomic_write(target, b"new\n")
    assert target.read_bytes() == b"new\n"
    assert not (tmp_path / "out.json.tmp").exists()


@pytest.mark.parametrize("name", ["stats.json.tmp", "results.csv.tmp"])
def test_stats_refuses_a_symlinked_output_and_writes_nothing(tmp_path, name):
    cdir = support.write_campaign(tmp_path, mode="direct")
    victim = _victim(tmp_path)
    (cdir / name).symlink_to(victim)
    watched = [cdir / "stats.json", cdir / "results.csv"]
    before = _snapshot(watched)
    doc = analysis.analyse_campaign(cdir, include_direct=True)
    with pytest.raises(analysis.AnalysisError, match="symlink"):
        analysis.write_stats(cdir, doc)
    assert victim.read_text() == "keep me\n"
    assert _snapshot(watched) == before


@pytest.mark.parametrize("where, name", [
    ("run", "metrics.json.tmp"),
    ("run", "manifest.json.tmp"),
    ("camp", "results.json.tmp"),
    ("camp", "results.csv.tmp"),
])
def test_metrics_recompute_refuses_a_symlinked_output_and_changes_nothing(
        project, monkeypatch, tmp_path, where, name):
    camp = _sweep(project)
    run_dir = camp / "runs" / "001_gain-1.0"
    monkeypatch.setattr(runner, "compute_metrics", lambda names, out, ref: dict(NEW))
    folder = run_dir if where == "run" else camp
    victim = _victim(tmp_path)
    (folder / name).symlink_to(victim)
    watched = [run_dir / "metrics.json", run_dir / "manifest.json",
               camp / "results.json", camp / "results.csv"]
    before = _snapshot(watched)
    result = CliRunner().invoke(experiment, ["metrics", str(run_dir)])
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert victim.read_text() == "keep me\n"
    assert _snapshot(watched) == before


@pytest.mark.parametrize("name", ["results.json.tmp", "results.csv.tmp"])
def test_sweep_refuses_a_symlinked_output_before_running_anything(project, tmp_path, name):
    campaign = Campaign.load(project / "pipelines/demo/campaigns/gain-sweep.yaml")
    stage = find_stage(project / "pipelines", "demo", "scale")
    campaign_dir = project / "experiments" / campaign.name
    campaign_dir.mkdir(parents=True)
    victim = _victim(tmp_path)
    (campaign_dir / name).symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        run_sweep(campaign, stage, project / "experiments", mode="direct")
    assert victim.read_text() == "keep me\n"
    assert not (campaign_dir / "runs").exists()


@pytest.mark.parametrize("mode", ["folder", "file"])
def test_package_capture_refuses_a_symlinked_packages_output(tmp_path, monkeypatch, mode):
    camp = tmp_path / "camp"
    camp.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    victim = _victim(tmp_path)
    sha = "a" * 64
    if mode == "folder":
        (camp / "packages").symlink_to(elsewhere, target_is_directory=True)
    else:
        (camp / "packages").mkdir()
        (camp / "packages" / f"{sha[:12]}.txt.tmp").symlink_to(victim)
    monkeypatch.setattr(manifest, "packages", lambda m, s: {
        "status": "captured", "source": "apptainer", "count": 1, "sha256": "0" * 64,
        "list": ["numpy 1.0"]})
    with pytest.raises(ValueError, match="symlink"):
        manifest._package_block("apptainer", tmp_path / "x.sif", sha, camp)
    assert list(elsewhere.iterdir()) == []
    assert victim.read_text() == "keep me\n"
