import json
from pathlib import Path

import click
import yaml

from scieflow.core import config

from .campaign import Campaign, RunSpec
from .compare import compare_campaign
from .envbuild import build_sif
from .report import generate_report
from .runner import execute_run, recompute_metrics
from .scaffold import new_pipeline, new_stage
from .stage import Stage, find_stage
from .sweep import run_sweep


def _default_pipelines_dir() -> Path:
    return config.repo_root() / "pipelines"


_pipelines_option = click.option(
    "--pipelines-dir",
    default=_default_pipelines_dir,
    type=click.Path(path_type=Path),
    show_default="<repo>/pipelines",
)

_common = [
    click.option(
        "--experiments-dir",
        required=True,
        type=click.Path(path_type=Path),
        help="Where runs are recorded, normally workspace/<slug>/experiments.",
    ),
    _pipelines_option,
]


def common_options(fn):
    for option in reversed(_common):
        fn = option(fn)
    return fn


def parse_params(pairs) -> dict:
    params = {}
    for item in pairs:
        key, _, value = item.partition("=")
        if not _:
            raise click.BadParameter(f"expected key=value, got '{item}'")
        params[key] = yaml.safe_load(value)
    return params


@click.group()
def experiment():
    """Computational experiments: campaigns, sweeps, metrics, reports."""


@experiment.command()
@click.option("-c", "--campaign", "campaign_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-p", "--param", "params", multiple=True,
              help="Override/set one parameter, e.g. -p sigma=1.5")
@click.option("--direct", is_flag=True,
              help="Run without a container (dev/tests only).")
@common_options
def run(campaign_path, params, direct, experiments_dir, pipelines_dir):
    """Run the campaign's stage once with explicit parameters."""
    campaign = Campaign.load(campaign_path)
    stage = find_stage(pipelines_dir, campaign.pipeline, campaign.stage)
    p = parse_params(params)
    slug = "_".join(f"{k}-{v}" for k, v in sorted(p.items())) or "default"
    spec = RunSpec(run_id=f"manual_{slug}", params=p)
    run_dir = execute_run(
        campaign, stage, spec, experiments_dir,
        mode="direct" if direct else "apptainer",
    )
    status = yaml.safe_load((run_dir / "config.yaml").read_text())["status"]
    click.echo(f"status: {status}")
    click.echo(str(run_dir))


@experiment.command()
@click.option("-c", "--campaign", "campaign_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--direct", is_flag=True,
              help="Run without containers (dev/tests only).")
@common_options
def sweep(campaign_path, direct, experiments_dir, pipelines_dir):
    """Run every parameter combination / scenario in the campaign."""
    campaign = Campaign.load(campaign_path)
    stage = find_stage(pipelines_dir, campaign.pipeline, campaign.stage)
    results_path = run_sweep(
        campaign, stage, experiments_dir, mode="direct" if direct else "apptainer"
    )
    click.echo(str(results_path))


@experiment.command()
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def compare(campaign_dir):
    """Rank a campaign's runs and check validation thresholds."""
    comparison = compare_campaign(campaign_dir)
    click.echo(f"campaign: {comparison['campaign']}  (ranked by {comparison['rank_by']})")
    for entry in comparison["ranked"]:
        flag = "PASS" if entry["validation"]["passed"] else "FAIL"
        value = entry["metrics"][comparison["rank_by"]]
        click.echo(f"  {entry['run_id']}  {comparison['rank_by']}={value:.4f}  [{flag}]")
    for run_id in comparison["failed"]:
        click.echo(f"  {run_id}  [NOT RANKED]")
    click.echo(f"best: {comparison['best']}")


@experiment.command()
@click.argument("run_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def metrics(run_dir):
    """(Re)compute metrics for a single run directory."""
    try:
        result = recompute_metrics(run_dir)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(json.dumps(result, indent=2))


@experiment.command()
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def report(campaign_dir):
    """Generate report.md + QC plots for a campaign."""
    click.echo(str(generate_report(campaign_dir)))


@experiment.group()
def env():
    """Manage stage container environments."""


@env.command("build")
@click.argument("stage_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--force", is_flag=True, help="Rebuild even if the .sif exists.")
def env_build(stage_dir, force):
    """Generate the Apptainer .def for a stage and build its .sif.

    Skips the build if the .sif already exists — use --force after changing
    the environment spec.
    """
    stage = Stage.load(stage_dir)
    click.echo(str(build_sif(stage, force=force)))


@experiment.group()
def new():
    """Scaffold new pipelines and stages."""


@new.command("pipeline")
@click.argument("name")
@_pipelines_option
def new_pipeline_cmd(name, pipelines_dir):
    """Create pipelines/NAME with a default stage and example campaign."""
    click.echo(str(new_pipeline(pipelines_dir, name)))


@new.command("stage")
@click.argument("name")
@click.option("--pipeline", required=True, help="Pipeline to add the stage to.")
@_pipelines_option
def new_stage_cmd(name, pipeline, pipelines_dir):
    """Create stages/NAME inside an existing pipeline."""
    click.echo(str(new_stage(Path(pipelines_dir) / pipeline, name)))
