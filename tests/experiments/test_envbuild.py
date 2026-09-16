from unittest.mock import patch
import shutil

import pytest
import subprocess

from scieflow.experiments.envbuild import build_sif, generate_def
from scieflow.experiments.stage import Stage


def make_stage(tmp_path, env_block):
    d = tmp_path / "pipelines" / "demo" / "stages" / "scale"
    d.mkdir(parents=True)
    (d / "stage.yaml").write_text(
        "name: scale\nscript: run.py\noutput_file: result.png\n" + env_block
    )
    (d / "run.py").write_text("print('hi')\n")
    return d


def test_generate_def_pip(tmp_path):
    d = make_stage(
        tmp_path,
        "environment:\n  pip: [numpy, imageio]\n  apt: [libgl1]\n",
    )
    text = generate_def(Stage.load(d))
    assert "From: python:3.12-slim" in text
    assert "apt-get install -y --no-install-recommends libgl1" in text
    assert "pip install --no-cache-dir numpy imageio" in text


def test_generate_def_conda(tmp_path):
    d = make_stage(tmp_path, "environment:\n  conda: environment.yml\n")
    (d / "environment.yml").write_text("name: x\ndependencies: [python=3.12]\n")
    text = generate_def(Stage.load(d))
    assert "From: mambaorg/micromamba" in text
    assert "environment.yml" in text
    assert "micromamba install" in text


def test_build_sif_writes_def_and_calls_apptainer(tmp_path):
    d = make_stage(tmp_path, "environment:\n  pip: [numpy]\n")
    stage = Stage.load(d)
    with patch("scieflow.experiments.envbuild.subprocess.run") as run:
        sif = build_sif(stage)
    assert (d / "env" / "scale.def").exists()
    assert sif == d / "env" / "scale.sif"
    cmd = run.call_args[0][0]
    assert cmd[:2] == ["apptainer", "build"]


def test_build_sif_skips_existing_without_force(tmp_path):
    d = make_stage(tmp_path, "environment:\n  pip: [numpy]\n")
    stage = Stage.load(d)
    (d / "env").mkdir()
    (d / "env" / "scale.sif").write_bytes(b"fake")
    with patch("scieflow.experiments.envbuild.subprocess.run") as run:
        build_sif(stage)
    run.assert_not_called()


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("apptainer") is None, reason="apptainer missing")
def test_real_container_build_and_exec(tmp_path):
    d = make_stage(tmp_path, "environment:\n  pip: []\n")
    stage = Stage.load(d)
    sif = build_sif(stage)
    assert sif.exists()
    out = subprocess.run(
        ["apptainer", "exec", str(sif), "python", "--version"],
        capture_output=True, text=True, check=True,
    )
    assert "Python 3" in out.stdout
