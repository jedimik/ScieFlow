import json
import os
from pathlib import Path

import click
import yaml

from scieflow.core import config, events
from scieflow.core.design import lock as design_lock
from scieflow.core.project import Project
from scieflow.core.run import actions

from .campaign import Campaign, RunSpec
from .compare import compare_campaign
from .envbuild import build_sif
from .power import FAMILIES as POWER_FAMILIES
from .power import PowerError, PowerUsageError, render_json, render_text
from .power import solve as solve_power
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


def _budget_run(experiments_dir: Path) -> Path | None:
    """The run whose budget these runs spend, if experiments_dir lives in one."""
    existing = next(p for p in [Path(experiments_dir), *Path(experiments_dir).parents]
                    if p.exists())
    return actions.run_for_path(existing)


def _guard(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    try:
        actions.guard_budget(run_dir, ("experiment_runs",))
    except actions.BudgetExhausted as e:
        raise click.ClickException(f"{e} — run checkpointed; raise the budget to continue") from e


def _design_guard(run_dir: Path | None, campaign: Campaign) -> None:
    """Refuse or warn under the run's study design (M-D1 plan 5.6), before anything runs or spends."""
    if run_dir is None:
        return
    verdict = design_lock.campaign_verdict(Project(run_dir.parent.parent), run_dir, campaign.metrics)
    for w in verdict.warnings:
        click.echo(f"warning: design: [{w.rule}] {w.message}", err=True)
        events.emit(run_dir, "note.design_warning", "system", rule=w.rule, campaign=campaign.name)
    if verdict.refused:
        events.emit(run_dir, "job.refused", "system", reason="design", rule=verdict.rule,
                    campaign=campaign.name)
        raise click.ClickException(
            f"[{verdict.rule}] {verdict.message} (campaign {campaign.name}; nothing was run)")


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
    budget_run = _budget_run(experiments_dir)
    _guard(budget_run)
    _design_guard(budget_run, campaign)
    run_dir = execute_run(
        campaign, stage, spec, experiments_dir,
        mode="direct" if direct else "apptainer",
    )
    if budget_run is not None:
        actions.record_spend(budget_run, experiment_runs=1)
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
    budget_run = _budget_run(experiments_dir)
    _guard(budget_run)
    _design_guard(budget_run, campaign)
    results_path = run_sweep(
        campaign, stage, experiments_dir, mode="direct" if direct else "apptainer"
    )
    if direct:
        click.echo(
            "note: --direct runs are for development; they are recorded but not ranked "
            "by compare/report (pass --include-direct to rank them).",
            err=True,
        )
    if budget_run is not None:
        actions.record_spend(budget_run, experiment_runs=len(campaign.expand()))
    click.echo(str(results_path))


@experiment.command()
@click.option("--include-direct", is_flag=True,
              help="Rank --direct (development) runs too.")
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def compare(campaign_dir, include_direct):
    """Rank a campaign's runs and check validation thresholds."""
    comparison = compare_campaign(campaign_dir, include_direct=include_direct)
    click.echo(f"campaign: {comparison['campaign']}  (ranked by {comparison['rank_by']})")
    for entry in comparison["ranked"]:
        flag = "PASS" if entry["validation"]["passed"] else "FAIL"
        value = entry["metrics"][comparison["rank_by"]]
        click.echo(f"  {entry['run_id']}  {comparison['rank_by']}={value:.4f}  [{flag}]")
    for entry in comparison["direct"]:
        click.echo(f"  {entry['run_id']}  [DIRECT, NOT RANKED]")
    for run_id in comparison["failed"]:
        click.echo(f"  {run_id}  [NOT RANKED]")
    click.echo(f"best: {comparison['best']}")
    if comparison["direct"]:
        click.echo("note: --direct runs are for development; pass --include-direct to rank them.")


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
@click.option("--include-direct", is_flag=True,
              help="Rank --direct (development) runs too.")
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def report(campaign_dir, include_direct):
    """Generate report.md + QC plots for a campaign."""
    click.echo(str(generate_report(campaign_dir, include_direct=include_direct)))


def _write_power_out(path: Path, text: str, force: bool) -> None:
    """Write a power document atomically; never through a symlink."""
    if path.is_symlink():
        raise click.ClickException(f"refusing to write through a symlink: {path}")
    if path.exists() and not force:
        raise click.ClickException(f"{path} exists; pass --force to replace it")
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.unlink(missing_ok=True)
        with open(tmp, "x", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as e:
        raise click.ClickException(f"cannot write {path}: {e.strerror}") from e


@experiment.command("power")
@click.argument("family", type=click.Choice(POWER_FAMILIES))
@click.option("--alpha", type=float, default=0.05, show_default=True,
              help="Significance level (0 < alpha <= 0.5).")
@click.option("--power", "target_power", type=float,
              help="Target power, e.g. 0.8. Solved for when omitted.")
@click.option("--n", type=float,
              help="Sample size: first group (t-ind, proportions), subjects or pairs "
                   "(t-paired, t-one, correlation), total (anova). Solved for when omitted.")
@click.option("--events", type=float,
              help="Survival: number of events. Solved for when omitted.")
@click.option("--effect-size", type=float,
              help="Cohen's d (t), f (anova) or h (proportions). Solved for when omitted.")
@click.option("--p1", type=float, help="Proportions: first proportion (use with --p2).")
@click.option("--p2", type=float, help="Proportions: second proportion (use with --p1).")
@click.option("--r", type=float, help="Correlation coefficient.")
@click.option("--hazard-ratio", type=float, help="Survival: hazard ratio.")
@click.option("--groups", type=int, help="ANOVA: number of groups (at least 2).")
@click.option("--ratio", type=float,
              help="t-ind, proportions: second group divided by the first (default 1).")
@click.option("--allocation", type=float,
              help="Survival: share of subjects in group 1 (default 0.5).")
@click.option("--event-probability", type=float,
              help="Survival: probability of an event per subject; then --n is solved "
                   "as the subject count.")
@click.option("--alternative", type=click.Choice(["two-sided", "larger", "smaller"]),
              default=None, help="Default two-sided. Use larger for one-sided tests.")
@click.option("--json", "as_json", is_flag=True,
              help="Print the deterministic JSON document.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Write the JSON document to this file.")
@click.option("--force", is_flag=True,
              help="Replace an existing --out file. A symlink is never written.")
def power_cmd(family, alpha, target_power, n, events, effect_size, p1, p2, r, hazard_ratio,
              groups, ratio, allocation, event_probability, alternative, as_json, out, force):
    """Sample size, power or effect size for a planned study.

    Give every input you know and leave exactly one unknown: the sample size
    (--n, or --events for survival), --power, or --effect-size. Statistics only;
    no data are read. Families: t-ind, t-paired, t-one, anova, proportions,
    correlation, survival.
    """
    try:
        doc = solve_power(
            family, alpha=alpha, power=target_power, n=n, events=events,
            effect_size=effect_size, p1=p1, p2=p2, r=r, hazard_ratio=hazard_ratio,
            groups=groups, ratio=ratio, allocation=allocation,
            event_probability=event_probability, alternative=alternative,
        )
    except PowerUsageError as e:
        raise click.UsageError(str(e)) from e
    except PowerError as e:
        raise click.ClickException(str(e)) from e
    if out is not None:
        _write_power_out(out, render_json(doc), force)
        click.echo(str(out))
        return
    click.echo(render_json(doc) if as_json else render_text(doc), nl=False)


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
