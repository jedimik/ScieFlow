import pytest
import yaml

from scieflow.experiments.scaffold import new_pipeline, new_stage
from scieflow.experiments.stage import Stage


def test_new_pipeline_creates_loadable_layout(tmp_path):
    pipeline_dir = new_pipeline(tmp_path / "pipelines", "waves")
    assert (pipeline_dir / "README.md").exists()
    stage = Stage.load(pipeline_dir / "stages" / "process")
    assert stage.name == "process"
    campaign = yaml.safe_load((pipeline_dir / "campaigns" / "example.yaml").read_text())
    assert campaign["pipeline"] == "waves"
    assert campaign["stage"] == "process"


def test_new_stage_refuses_overwrite(tmp_path):
    pipeline_dir = new_pipeline(tmp_path / "pipelines", "waves")
    with pytest.raises(FileExistsError):
        new_stage(pipeline_dir, "process")
