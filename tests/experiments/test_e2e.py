import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scieflow.experiments.campaign import Campaign
from scieflow.experiments.envbuild import build_sif
from scieflow.experiments.metrics import compute_metrics
from scieflow.experiments.report import generate_report
from scieflow.experiments.stage import find_stage
from scieflow.experiments.sweep import run_sweep

REPO = Path(__file__).resolve().parents[2]
PIPELINE = REPO / "pipelines" / "denoise"


@pytest.fixture
def denoise_project(tmp_path):
    shutil.copytree(PIPELINE, tmp_path / "pipelines" / "denoise")
    subprocess.run(
        [
            sys.executable,
            str(tmp_path / "pipelines/denoise/generate_data.py"),
            "--out-dir", str(tmp_path / "pipelines/denoise/data"),
            "--seed", "42",
        ],
        check=True,
    )
    return tmp_path


def test_denoise_pipeline_end_to_end_direct(denoise_project):
    root = denoise_project
    campaign = Campaign.load(
        root / "pipelines/denoise/campaigns/sigma-sweep.yaml"
    )
    stage = find_stage(root / "pipelines", "denoise", "denoise")
    run_sweep(campaign, stage, root / "experiments", mode="direct")

    results = json.loads(
        (root / "experiments/denoise-sigma-sweep/results.json").read_text()
    )
    assert all(r["status"] == "completed" for r in results["runs"])
    # denoising must beat the raw noisy input for at least one sigma
    data_dir = root / "pipelines/denoise/data"
    baseline = compute_metrics(
        ["ssim"], data_dir / "noisy.png", data_dir / "truth.png"
    )["ssim"]
    ssims = [r["metrics"]["ssim"] for r in results["runs"]]
    assert max(ssims) > baseline

    report = generate_report(root / "experiments/denoise-sigma-sweep")
    assert report.exists()


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("apptainer") is None, reason="apptainer missing")
def test_denoise_pipeline_containerized(denoise_project):
    root = denoise_project
    campaign = Campaign.load(
        root / "pipelines/denoise/campaigns/sigma-sweep.yaml"
    )
    stage = find_stage(root / "pipelines", "denoise", "denoise")
    build_sif(stage)
    run_dir = None
    from scieflow.experiments.campaign import RunSpec
    from scieflow.experiments.runner import execute_run

    run_dir = execute_run(
        campaign, stage, RunSpec("000_sigma-1.0", {"sigma": 1.0}),
        root / "experiments", mode="apptainer",
    )
    env = json.loads((run_dir / "environment.json").read_text())
    assert env["mode"] == "apptainer"
    assert len(env["sif_sha256"]) == 64
