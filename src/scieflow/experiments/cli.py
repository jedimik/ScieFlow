import json
import os
from pathlib import Path

import click
import yaml

from scieflow.core import config, events
from scieflow.core.design import lock as design_lock
from scieflow.core.project import Project
from scieflow.core.run import actions, budget

from . import manifest
from .analysis import AnalysisError, analyse_campaign, stats_fresh, write_stats
from .campaign import Campaign, CampaignError, RunSpec, check_replicates
from .compare import compare_campaign
from .envbuild import build_sif
from .power import FAMILIES as POWER_FAMILIES
from .power import PowerError, PowerUsageError, render_json, render_text
from .power import solve as solve_power
from .report import generate_report
from .runner import execute_run, recompute_metrics
from .scaffold import new_pipeline, new_stage
from .stage import Stage, find_stage
from .stats import StatsUnavailable
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


def _check_stage_seed(campaign: Campaign, stage) -> None:
    """Replicates need a seeded stage; refused before anything runs or spends (plan 5.1)."""
    try:
        check_replicates(campaign, stage)
    except CampaignError as e:
        raise click.ClickException(str(e)) from e


def _guard_capacity(run_dir: Path | None, planned: int) -> None:
    """Refuse a campaign whose planned runs exceed the remaining experiment_runs (plan 5.2).

    No refusal (and no event) outside a run, without budget.yml, or when max_experiment_runs
    is absent, null or 0. Refusals write no checkpoint and no run directory.
    """
    if run_dir is None:
        return
    b = budget.read_budget(run_dir)
    if not b:
        return
    cap = (b.get("budgets") or {}).get("max_experiment_runs")
    if not cap:
        return
    spent = (b.get("spent") or {}).get("experiment_runs") or 0
    if spent + planned <= cap:
        return
    remaining = max(0, cap - spent)
    events.emit(run_dir, "job.refused", "system", reason="budget", rule="insufficient",
                dims=["experiment_runs"], planned=planned, remaining=remaining)
    raise click.ClickException(
        f"experiment_runs budget insufficient: this campaign needs {planned} runs, "
        f"{remaining} remain — propose a smaller campaign or ask the user to raise the budget")


@experiment.command()
@click.option("-c", "--campaign", "campaign_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-p", "--param", "params", multiple=True,
              help="Override/set one parameter, e.g. -p sigma=1.5")
@click.option("--seed", type=click.IntRange(0, 2**31 - 1), default=None,
              help="Seed for a stage that declares accepts_seed: true.")
@click.option("--direct", is_flag=True,
              help="Run without a container (dev/tests only).")
@common_options
def run(campaign_path, params, seed, direct, experiments_dir, pipelines_dir):
    """Run the campaign's stage once with explicit parameters."""
    campaign = Campaign.load(campaign_path)
    stage = find_stage(pipelines_dir, campaign.pipeline, campaign.stage)
    _check_stage_seed(campaign, stage)
    if seed is not None and not stage.accepts_seed:
        raise click.ClickException(
            f"stage {stage.name} does not declare accepts_seed: true; --seed would have no "
            "effect (nothing was run)")
    p = parse_params(params)
    slug = "_".join(f"{k}-{v}" for k, v in sorted(p.items())) or "default"
    spec = RunSpec(run_id=f"manual_{slug}", params=p, seed=seed)
    budget_run = _budget_run(experiments_dir)
    _guard(budget_run)
    _design_guard(budget_run, campaign)
    _guard_capacity(budget_run, 1)
    mode = "direct" if direct else "apptainer"
    context = manifest.capture_context(campaign, stage, mode,
                                       Path(experiments_dir) / campaign.name)
    run_dir = execute_run(
        campaign, stage, spec, experiments_dir, mode=mode, context=context,
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
    _check_stage_seed(campaign, stage)
    budget_run = _budget_run(experiments_dir)
    _guard(budget_run)
    _design_guard(budget_run, campaign)
    _guard_capacity(budget_run, len(campaign.expand()))
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
    for c in comparison.get("configs", []):
        mean = "n/a" if c["mean"] is None else f"{c['mean']:.4f}"
        click.echo(f"  config {c['config_id']}  mean {comparison['rank_by']}={mean}  "
                   f"n={c['n']} failed={c['n_failed']}")
    if "configs" in comparison:
        click.echo(f"best config: {comparison['best_config']}")
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


def _analysis_note(campaign_dir: Path, kind: str, **data) -> None:
    """A value-free note in the owning run's events, when the campaign folder lies inside a run."""
    ws = actions.run_for_path(Path(campaign_dir))
    if ws is None:
        return
    events.emit(ws, "note.experiment_analysis", "system", kind=kind,
                campaign=Path(campaign_dir).name, **data)


@experiment.command()
@click.argument("run_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def metrics(run_dir):
    """(Re)compute metrics for a single run directory."""
    try:
        result = recompute_metrics(run_dir)
    except ValueError as e:
        raise click.ClickException(str(e))
    run_dir = Path(run_dir)
    _analysis_note(run_dir.parent.parent, "metrics-recompute", run_id=run_dir.name,
                   sha256=manifest.file_sha256(run_dir / "metrics.json"))
    click.echo(json.dumps(result, indent=2))


@experiment.command("verify")
@click.option("--json", "as_json", is_flag=True,
              help="Print the deterministic JSON document.")
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def verify_cmd(campaign_dir, as_json):
    """Re-check every recorded hash of a campaign's runs (manifests, artifacts, metrics)."""
    report = manifest.verify_campaign(campaign_dir)
    doc = report.to_dict()
    if as_json:
        click.echo(json.dumps(doc, indent=2, sort_keys=True))
    else:
        for n in doc["notes"]:
            click.echo(f"note {n['rule']} {n['run_id']} {n['path']}")
        for p in doc["problems"]:
            click.echo(f"problem {p['rule']} {p['run_id']} {p['path']}")
        click.echo("ok" if report.ok else "failed")
    _analysis_note(Path(campaign_dir), "verify", problems=len(doc["problems"]))
    if not report.ok:
        raise SystemExit(1)


def _print_stats(doc: dict) -> None:
    for row in doc["configs"]:
        for metric, cell in row["metrics"].items():
            mean = "n/a" if cell["mean"] is None else f"{cell['mean']:.4f}"
            ci = ("n/a" if cell["ci"] is None else
                  f"[{cell['ci']['low']:.4f}, {cell['ci']['high']:.4f}]")
            click.echo(f"  config {row['config_id']}  {metric}  n={cell['n']} mean={mean} "
                       f"ci={ci}")
    for t in doc["tests"]:
        if t["computed"]:
            adjusted = "n/a" if t["p_adjusted"] is None else f"{t['p_adjusted']:.4g}"
            state = f"p={t['p']:.4g} p_adj={adjusted} reject={t['reject']}"
        else:
            state = f"not computed ({t['reason']})"
        click.echo(f"  test {t['id']}  {t['test']}  {state}")


@experiment.command("stats")
@click.option("--include-direct", is_flag=True,
              help="Analyse --direct (development) runs too.")
@click.option("--json", "as_json", is_flag=True, help="Print the stats.json document.")
@click.argument("campaign_dir",
                type=click.Path(exists=True, file_okay=False, path_type=Path))
def stats_cmd(campaign_dir, include_direct, as_json):
    """Compute descriptives and the campaign's tests into stats.json (M-D2 plan 5.4)."""
    try:
        doc = analyse_campaign(campaign_dir, include_direct=include_direct)
        path = write_stats(campaign_dir, doc)
    except (AnalysisError, StatsUnavailable) as e:
        raise click.ClickException(str(e)) from e
    if as_json:
        click.echo(json.dumps(doc, sort_keys=True, indent=1, allow_nan=False))
    else:
        _print_stats(doc)
    _analysis_note(Path(campaign_dir), "stats", tests=len(doc["tests"]),
                   sha256=manifest.file_sha256(path))


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
