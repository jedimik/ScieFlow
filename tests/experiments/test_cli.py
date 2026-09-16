import yaml
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from scieflow.cli import main as scieflow_main
from scieflow.core import config
from scieflow.experiments.cli import experiment as main


def test_version():
    result = CliRunner().invoke(scieflow_main, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.output


def test_experiment_group_is_reachable_from_root_cli():
    result = CliRunner().invoke(scieflow_main, ["experiment", "--help"])
    assert result.exit_code == 0, result.output
    assert "sweep" in result.output


def test_sweep_requires_experiments_dir(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    result = CliRunner().invoke(main, ["sweep", "-c", str(campaign), "--direct"])
    assert result.exit_code != 0
    assert "--experiments-dir" in result.output


def test_pipelines_dir_defaults_to_repo_pipelines():
    from scieflow.experiments.cli import _default_pipelines_dir

    assert _default_pipelines_dir() == config.repo_root() / "pipelines"
    assert (_default_pipelines_dir() / "denoise").is_dir()


def test_run_command_direct(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    result = CliRunner().invoke(
        main,
        [
            "run", "-c", str(campaign), "-p", "gain=1.0", "--direct",
            "--experiments-dir", str(project / "experiments"),
            "--pipelines-dir", str(project / "pipelines"),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.strip().splitlines()[-1])
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    assert config["status"] == "completed"
    assert config["params"] == {"gain": 1.0}


def test_sweep_command_direct(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    result = CliRunner().invoke(
        main,
        [
            "sweep", "-c", str(campaign), "--direct",
            "--experiments-dir", str(project / "experiments"),
            "--pipelines-dir", str(project / "pipelines"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (project / "experiments/gain-sweep/results.json").exists()


def test_compare_and_metrics_commands(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    runner = CliRunner()
    runner.invoke(main, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(project / "experiments"),
        "--pipelines-dir", str(project / "pipelines"),
    ])
    result = runner.invoke(
        main, ["compare", str(project / "experiments/gain-sweep")]
    )
    assert result.exit_code == 0, result.output
    assert "gain-1.0" in result.output

    run_dir = next((project / "experiments/gain-sweep/runs").iterdir())
    result = runner.invoke(main, ["metrics", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert "ssim" in result.output


def test_report_command(project):
    campaign = project / "pipelines/demo/campaigns/gain-sweep.yaml"
    runner = CliRunner()
    runner.invoke(main, [
        "sweep", "-c", str(campaign), "--direct",
        "--experiments-dir", str(project / "experiments"),
        "--pipelines-dir", str(project / "pipelines"),
    ])
    result = runner.invoke(main, ["report", str(project / "experiments/gain-sweep")])
    assert result.exit_code == 0, result.output
    assert (project / "experiments/gain-sweep/report.md").exists()


def test_env_build_command(project):
    stage_dir = project / "pipelines/demo/stages/scale"
    with patch("scieflow.experiments.envbuild.subprocess.run"):
        result = CliRunner().invoke(main, ["env", "build", str(stage_dir)])
    assert result.exit_code == 0, result.output
    assert (stage_dir / "env" / "scale.def").exists()


def test_new_pipeline_and_stage_commands(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, [
        "new", "pipeline", "waves", "--pipelines-dir", str(tmp_path / "pipelines"),
    ])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, [
        "new", "stage", "filter", "--pipeline", "waves",
        "--pipelines-dir", str(tmp_path / "pipelines"),
    ])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "pipelines/waves/stages/filter/stage.yaml").exists()
