import pytest

from scieflow.experiments.stage import Stage, StageError, find_stage, sif_path

STAGE_YAML = """\
name: scale
description: multiply image by gain
script: run.py
output_file: result.png
environment:
  pip: [numpy, imageio]
"""


def make_stage(tmp_path, yaml_text=STAGE_YAML, script="run.py"):
    d = tmp_path / "pipelines" / "demo" / "stages" / "scale"
    d.mkdir(parents=True)
    (d / "stage.yaml").write_text(yaml_text)
    (d / script).write_text("print('hi')\n")
    return d


def test_load_pip_stage(tmp_path):
    stage = Stage.load(make_stage(tmp_path))
    assert stage.name == "scale"
    assert stage.output_file == "result.png"
    assert stage.environment.pip == ["numpy", "imageio"]
    assert stage.environment.conda_file is None
    assert stage.environment.base_image == "python:3.12-slim"


def test_load_conda_stage(tmp_path):
    d = make_stage(
        tmp_path,
        STAGE_YAML.replace("  pip: [numpy, imageio]", "  conda: environment.yml"),
    )
    (d / "environment.yml").write_text("name: x\ndependencies: [python=3.12]\n")
    stage = Stage.load(d)
    assert stage.environment.conda_file == (d / "environment.yml").resolve()


def test_missing_script_raises(tmp_path):
    d = make_stage(tmp_path)
    (d / "run.py").unlink()
    with pytest.raises(StageError, match="script"):
        Stage.load(d)


def test_missing_required_field_raises(tmp_path):
    d = make_stage(tmp_path, STAGE_YAML.replace("output_file: result.png\n", ""))
    with pytest.raises(StageError, match="output_file"):
        Stage.load(d)


def test_find_stage_and_sif_path(tmp_path):
    make_stage(tmp_path)
    stage = find_stage(tmp_path / "pipelines", "demo", "scale")
    assert stage.name == "scale"
    assert sif_path(stage) == stage.dir / "env" / "scale.sif"


def test_empty_stage_yaml_raises(tmp_path):
    d = tmp_path / "stages" / "empty"
    d.mkdir(parents=True)
    (d / "stage.yaml").write_text("")
    with pytest.raises(StageError, match="mapping"):
        Stage.load(d)
