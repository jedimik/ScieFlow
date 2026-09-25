"""`scieflow run` — a run's state, history and lifecycle from the command line.

Agents use these instead of editing status.yml or budget.yml by hand, so every
change is locked, validated and recorded as an event.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from scieflow.core import events
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import actions, budget, status


def _ws(slug: str) -> Path:
    try:
        ws = Project.discover().run_dir(slug)
    except ProjectError as e:
        raise click.ClickException(str(e)) from e
    if not (ws / "status.yml").exists():
        raise click.ClickException(f"no run workspace/{slug} (status.yml missing)")
    return ws


def _actor(as_agent: bool) -> str:
    return "agent" if as_agent else "human"


AGENT_FLAG = click.option("--as-agent", is_flag=True,
                          help="Record the change as made by an agent (agents must pass this).")


@click.group()
def run():
    """A run's state, history and lifecycle."""


@run.command()
@click.argument("slug")
@click.option("--goal", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--approval", type=click.Choice(["per-campaign", "autonomous"]))
@click.option("--max-iterations", type=int)
@click.option("--max-experiment-runs", type=int)
@click.option("--max-wall-minutes", type=int)
def init(slug, goal, approval, max_iterations, max_experiment_runs, max_wall_minutes):
    """Create a research-loop run workspace."""
    from scieflow.core.run.init import init_workspace

    project = Project.discover()
    try:
        ws = init_workspace(slug, goal, project.workspace_root,
                            {"approval": approval, "max_iterations": max_iterations,
                             "max_experiment_runs": max_experiment_runs,
                             "max_wall_minutes": max_wall_minutes}, project.root)
    except FileExistsError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"initialized {ws}")


@run.command()
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True)
def show(slug, as_json):
    """Status, budget and what to do next."""
    ws = _ws(slug)
    st = status.read_status(ws)
    b = budget.read_budget(ws) if (ws / "budget.yml").exists() else None
    data = {"status": st, "budget": b,
            "remaining": budget.remaining_fraction(b) if b else None}
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
        return
    click.echo(f"{slug}  id={st.get('id', '-')}  iteration {st.get('iteration', '-')}")
    for phase, state in (st.get("phases") or {}).items():
        click.echo(f"  {phase:<14} {state}")
    if st.get("stopped"):
        click.echo(f"  stopped: {st['stopped']['reason']} — {st['stopped'].get('resume', '')}")
    if b:
        for dim, frac in budget.remaining_fraction(b).items():
            click.echo(f"  budget {dim:<16} {frac:6.0%} left")


@run.command()
@click.argument("slug")
@click.argument("phase")
@click.argument("state")
@AGENT_FLAG
def mark(slug, phase, state, as_agent):
    """Set a phase's state (pending/running/done/failed)."""
    try:
        actions.mark_phase(_ws(slug), phase, state, _actor(as_agent))
    except ValueError as e:
        raise click.ClickException(str(e)) from e


@run.command()
@click.argument("slug")
@click.option("--experiment-runs", type=int, default=0, help="Runs this run has spent.")
@click.option("--wall-minutes", type=float, default=0.0,
              help="Wall time no job runner could measure (remote or manual work).")
@click.option("--iterations", type=int, default=0, help="Iterations, when not using `run advance`.")
@AGENT_FLAG
def spend(slug, experiment_runs, wall_minutes, iterations, as_agent):
    """Record spend the runner cannot see (remote jobs, manual work).

    Dispatch wall time and `scieflow experiment` runs are recorded already;
    this is for everything else. Never hand-edit budget.yml.
    """
    spent = {k: v for k, v in (("experiment_runs", experiment_runs),
                               ("wall_minutes", wall_minutes),
                               ("iterations", iterations)) if v}
    if not spent:
        raise click.ClickException("nothing to record; pass at least one dimension")
    try:
        b = actions.record_spend(_ws(slug), _actor(as_agent), **spent)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if b is None:
        raise click.ClickException(f"run {slug} has no budget.yml")
    click.echo(json.dumps(b["spent"], indent=2, default=str))


@run.command()
@click.argument("slug")
@AGENT_FLAG
def advance(slug, as_agent):
    """Start the next iteration (refused when the iteration budget is spent)."""
    try:
        st = actions.advance_iteration(_ws(slug), _actor(as_agent))
    except actions.BudgetExhausted as e:
        raise click.ClickException(f"{e} — run checkpointed") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"iteration {st['iteration']}")


@run.command()
@click.argument("slug")
@click.option("--reason", required=True,
              type=click.Choice(["low-budget", "max-iterations", "converged", "anomaly", "user"]))
@click.option("--detail", default="")
@AGENT_FLAG
def checkpoint(slug, reason, detail, as_agent):
    """Stop the run gracefully with resume instructions."""
    st = actions.checkpoint_run(_ws(slug), reason, detail, _actor(as_agent))
    click.echo(st["stopped"]["resume"])


@run.command()
@click.argument("slug")
@AGENT_FLAG
def resume(slug, as_agent):
    """Clear a stop so the run can continue."""
    actions.resume(_ws(slug), _actor(as_agent))
    click.echo("resumed")


@run.command("charter")
@click.argument("slug")
@click.option("--set", "text", default=None, help="Replace the charter with TEXT.")
@click.option("--note", default="", help="Why you are changing it (only with --set).")
@click.option("--revert", "version", type=int, default=None,
              help="Make version N current again, by appending a copy of it.")
@AGENT_FLAG
def charter_cmd(slug, text, note, version, as_agent):
    """Show or change what this run has agreed to do.

    The charter is put at the top of every prompt this run sends an agent, so
    a long conversation cannot drift away from the goal it started with. A
    revert appends a copy of the old version rather than rewinding, so the
    history of how the goal moved is never lost.

    Only a human writes or reverts it directly. An agent that wants the
    charter changed opens a `charter-adoption` gate instead (rule 14) and
    lets a human adopt the proposal — `--as-agent` here is refused together
    with `--set`/`--revert` for exactly that reason.
    """
    from scieflow.core import service

    project = Project.discover()
    if text is not None and version is not None:
        raise click.ClickException("--set and --revert are mutually exclusive")
    if as_agent and (text is not None or version is not None):
        raise click.ClickException(
            "an agent cannot set or revert the charter directly — open a "
            "charter-adoption gate and let a human adopt it "
            "(`scieflow gate open <slug> --kind charter-adoption --question ... "
            "--option adopt --option decline --file <proposal>`)")
    if text is not None and not text.strip():
        raise click.ClickException("--set needs non-empty text")
    if note and text is None:
        raise click.ClickException("--note only makes sense together with --set")
    actor = _actor(as_agent)
    try:
        if version is not None:
            result = service.revert_charter(project, slug, version, actor)
            click.echo(f"restored version {version} as v{result['n']}")
            return
        if text is not None:
            result = service.set_charter(project, slug, text, actor, note)
            click.echo(f"charter v{result['n']} written")
            return
        doc = service.run_charter(project, slug)
        if not doc["text"]:
            click.echo("no charter yet; set one with --set")
            return
        click.echo(doc["text"])
        click.echo(f"\n(version {doc['current']} of {len(doc['versions'])})")
    except service.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc


@run.command("events")
@click.argument("slug")
@click.option("--since", help="Only events after this event id.")
@click.option("--type", "types", multiple=True, help="Filter by type; 'job.*' matches a prefix.")
@click.option("--follow", is_flag=True, help="Keep printing new events (Ctrl-C to stop).")
@click.option("--json", "as_json", is_flag=True, help="One JSON object per line.")
def events_cmd(slug, since, types, follow, as_json):
    """The run's history."""
    ws = _ws(slug)

    def show_one(e):
        if as_json:
            click.echo(json.dumps(e, default=str))
        else:
            detail = " ".join(f"{k}={v}" for k, v in e["data"].items())
            click.echo(f"{e['ts'][:19]}  {e['actor']:<6} {e['type']:<20} {detail}")

    stream = events.follow(ws, since=since) if follow else events.read(ws, since=since)
    try:
        for e in stream:
            if not types or events._matches(e["type"], types):
                show_one(e)
    except KeyboardInterrupt:
        pass


@run.command("log")
@click.argument("slug")
@click.argument("type_")
@click.option("--message", default="")
@click.option("--data", "pairs", multiple=True, metavar="KEY=VALUE")
def log_cmd(slug, type_, message, pairs):
    """Record a free-form 'note.<name>' event (structured alternative to log.md)."""
    if not type_.startswith("note."):
        raise click.ClickException("agents log free-form events as 'note.<name>'")
    data = dict(p.split("=", 1) for p in pairs if "=" in p)
    if message:
        data["message"] = message
    events.emit(_ws(slug), type_, "agent", **data)


@run.command("list")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(as_json):
    """Every run with kind, phase and state."""
    from scieflow.core import service

    runs = service.list_runs(Project.discover())
    if as_json:
        click.echo(json.dumps(runs, indent=2, default=str))
        return
    for r in runs:
        phase = f"{r['phase']} ({r['phase_state']})" if r.get("phase") else ""
        click.echo(f"{(r.get('updated_at') or '')[:16]:<16}  {r['kind']:<13} {r['slug']:<48} {phase}")
