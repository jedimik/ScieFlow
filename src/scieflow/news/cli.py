from __future__ import annotations

import os
from pathlib import Path

import click

from scieflow.core import config as config_mod
from scieflow.core import legacy

from . import agents
from .config import EXAMPLE_CONFIG, VALID_AGENTS, ConfigError, load_config
from .db import Database
from .report import assemble_report
from .runner import run_queue
from .templates import TEMPLATES

def default_config_path() -> Path:
    return config_mod.repo_root() / "config" / "news.yml"


CONFIG_OPT = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=default_config_path,
    show_default="<repo>/config/news.yml",
    help="Path to the news interests config file.",
)
DB_OPT = click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Database file path (defaults to $SCIEFLOW_NEWS_DB or <repo>/workspace/news/news.json).",
)


def resolve_db_path(db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path
    env = legacy.env("SCIEFLOW_NEWS_DB")  # WHATSNEW_DB still honoured
    return Path(env) if env else config_mod.repo_root() / "workspace" / "news" / "news.json"


def open_db(db_path: Path | None) -> Database:
    return Database(resolve_db_path(db_path))


@click.group()
def news():
    """Track what changed in the tools and topics you follow."""


@news.command()
@CONFIG_OPT
def init(config_path: Path):
    """Scaffold an example news interests config."""
    if config_path.exists():
        raise click.ClickException(f"{config_path} already exists")
    config_path.write_text(EXAMPLE_CONFIG)
    click.echo(f"wrote {config_path}")


@news.command()
@CONFIG_OPT
@DB_OPT
@click.option("--interest", "interests", multiple=True, help="Run a subset (repeatable).")
@click.option("--group", "groups", multiple=True, help="Run a group's interests (repeatable).")
@click.option(
    "--agent",
    type=click.Choice(list(VALID_AGENTS)),
    default=None,
    help="Override the config's default agent for this run.",
)
@click.option("--model", default=None, help="Override the model passed to the agent CLI.")
@click.option(
    "--reasoning",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Reasoning effort where the backend supports it.",
)
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Research window start date (YYYY-MM-DD); exclusive with --days.",
)
@click.option(
    "--days",
    type=click.IntRange(min=1),
    default=None,
    help="Research window length in days from today; exclusive with --since.",
)
def run(config_path, db_path, interests, groups, agent, model, reasoning, since, days):
    """Run the research queue and print the report."""
    if since is not None and days is not None:
        raise click.UsageError("--since and --days are mutually exclusive")
    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    db = open_db(db_path)

    def show(p):
        line = f"[{p.status}] {p.interest}"
        if p.detail:
            line += f" — {p.detail}"
        click.echo(line, err=True)

    try:
        run_id = run_queue(
            config,
            db,
            agent=agent,
            only=list(interests) or None,
            groups=list(groups) or None,
            model=model,
            reasoning=reasoning,
            since=since.date() if since else None,
            days=days,
            progress=show,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    run_doc = db.get_run(run_id)
    results = db.results_for_run(run_id)
    click.echo(assemble_report(run_doc, results))
    if run_doc.get("failures") and not results:
        raise SystemExit(1)


@news.command()
@CONFIG_OPT
@DB_OPT
def status(config_path, db_path):
    """List interests with last-checked dates."""
    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    db = open_db(db_path)
    for interest in config.interests:
        last = db.get_last_checked(interest.name)
        shown = last.isoformat() if last else "never"
        click.echo(f"{interest.name}: last checked {shown}")


@news.command()
@CONFIG_OPT
@DB_OPT
@click.option(
    "--run", "run_id", type=int, default=None, help="Export a specific run by id."
)
@click.option("--latest", is_flag=True, help="Export the most recent run.")
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write the report file into (default: <repo>/workspace/news/reports).",
)
def export(config_path, db_path, run_id, latest, output_dir):
    """Generate a markdown report file from a stored run."""
    if latest == (run_id is not None):
        raise click.UsageError("provide exactly one of --run or --latest")
    db = open_db(db_path)
    if latest:
        run_id = db.latest_run_id()
        if run_id is None:
            raise click.ClickException("no runs in the database yet")
    run_doc = db.get_run(run_id)
    if run_doc is None:
        raise click.ClickException(f"no run with id {run_id}")
    if output_dir is None:
        output_dir = config_mod.repo_root() / "workspace" / "news" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    day = run_doc["timestamp"][:10]
    path = output_dir / f"{day}-news.md"
    path.write_text(assemble_report(run_doc, db.results_for_run(run_id)))
    click.echo(f"wrote {path}")


@news.command()
@CONFIG_OPT
@DB_OPT
@click.option("--port", type=int, default=8080, help="Port for the local web GUI.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser automatically.")
def gui(config_path: Path, db_path, port: int, no_browser: bool):
    """Start the local web GUI (localhost only)."""
    try:
        from .gui.app import start_gui
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("nicegui"):
            raise
        raise click.ClickException(
            "'scieflow news gui' needs the 'news-gui' extra. "
            "Install it with: uv sync --extra news-gui"
        ) from exc

    start_gui(
        config_path,
        resolve_db_path(db_path),
        port=port,
        open_browser_on_start=not no_browser,
    )


@news.command()
@DB_OPT
@click.option(
    "--agent",
    "agents_opt",
    multiple=True,
    type=click.Choice(list(VALID_AGENTS)),
    help="Limit to specific agents.",
)
@click.option("--refresh", is_flag=True, help="Re-query the agent CLIs instead of using the cache.")
def models(db_path, agents_opt, refresh):
    """List available models per agent (cached; --refresh to re-check)."""
    db = open_db(db_path)
    for agent in agents_opt or VALID_AGENTS:
        cached = db.get_models(agent)
        if refresh or cached is None:
            found = agents.discover_models(agent)
            db.set_models(agent, found)
            cached = db.get_models(agent)
        names = cached["models"]
        click.echo(f"{agent}: {', '.join(names) if names else '(none found — try --refresh)'}")


@news.command()
def templates():
    """List available research templates."""
    for template in TEMPLATES.values():
        click.echo(f"{template.key} — {template.label}")
        click.echo(f"    {template.description}")
        click.echo(f"    sections: {', '.join(template.sections)}")
