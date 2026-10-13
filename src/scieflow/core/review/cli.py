"""`scieflow review` commands."""

from __future__ import annotations

import sys

import click
import yaml

_BOOLS = {"true": True, "yes": True, "on": True, "false": False, "no": False}
_STRUCTURED = ("pool", "matrix_override")


@click.group()
def review():
    """Draft review: settings, reviewer assignment, evidence and decisions."""


def _pool(raw: str):
    """`agent[:covers],…` shorthand, or a YAML/JSON list."""
    if raw.lstrip().startswith("["):
        return yaml.safe_load(raw)
    entries = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        agent, _, covers = token.partition(":")
        entries.append({"agent": agent.strip(), **({"covers": covers.strip()} if covers else {})})
    return entries


def parse_set(text: str) -> tuple[str, object]:
    """KEY=VALUE from the command line to a typed (key, value); '' clears structured keys."""
    from scieflow.core.review.settings import SettingsError

    key, sep, raw = text.partition("=")
    key = key.strip()
    if not sep or not key:
        raise SettingsError(f"--set expects KEY=VALUE, got {text!r}")
    raw = raw.strip()
    if key in _STRUCTURED:
        if raw == "":
            return key, None
        try:
            value = _pool(raw) if key == "pool" else yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise SettingsError(f"review.{key}: not valid YAML: {exc}") from exc
        return key, value
    if key == "reviews_per_draft":
        try:
            return key, int(raw)
        except ValueError:
            return key, raw
    if key.startswith("allow_"):
        return key, _BOOLS.get(raw.lower(), raw)
    return key, raw


def _show(block: dict) -> None:
    for key, value in block.items():
        shown = "(research.draft-reviewers)" if key == "pool" and value is None else value
        click.echo(f"  {key:<26} {shown}")


@review.command("configure")
@click.argument("slug")
@click.option("--set", "sets", multiple=True, metavar="KEY=VALUE",
              help="scite=off|ask|approved, reviews_per_draft=N, allow_same_family_review=BOOL, "
                   "allow_self_review=BOOL, review_decisions=human|coordinator, "
                   "pool=AGENT[:all|auto],… (empty clears), matrix_override=YAML (repeatable).")
@click.option("--as-agent", is_flag=True,
              help="Write as the coordinator agent: only scite=off and reviews_per_draft.")
@click.option("--yes", is_flag=True, help="Write without asking for confirmation.")
def configure(slug, sets, as_agent, yes):
    """Show or change workspace/<SLUG>'s review settings (the review: block only)."""
    from scieflow.core.project import Project
    from scieflow.core.review import settings

    project = Project.discover()
    try:
        current = settings.load(project, slug)
        if not sets:
            click.echo(f"Review settings of workspace/{slug}:")
            _show(current)
            return
        changes = dict(parse_set(s) for s in sets)
        actor = "agent" if as_agent else "human"
        settings.check_actor(changes, actor)
        settings.validate({k: v for k, v in changes.items() if v is not None})
        for key, value in changes.items():
            click.echo(f"  {key}: {current.get(key)!r} -> {value!r}")
        if not yes:
            if not sys.stdin.isatty():
                raise click.ClickException("not written: pass --yes to confirm non-interactively")
            if not click.confirm("Write these changes?", default=False):
                click.echo("not written")
                return
        settings.write(project, slug, changes, actor)
    except settings.SettingsError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"written: workspace/{slug}/config.yml (review:)")


def _assignment_inputs(project, slug: str):
    """(authors, pool, settings, families) for a run, from its effective config."""
    from scieflow.core import agent_config as ac
    from scieflow.core.review import settings as rs
    from scieflow.core.review.assign import AssignmentError

    effective = rs.load(project, slug)
    eff = ac.resolve(project.root, slug)
    authors = eff.value(ac.DRAFT_AUTHORS)
    authors = [authors] if isinstance(authors, str) else list(authors or [])
    pool = effective["pool"]
    if pool is None:
        names = eff.value(ac.DRAFT_REVIEWERS)
        names = [names] if isinstance(names, str) else list(names or [])
        pool = [{"agent": n} for n in names]
    registry = (ac._load_yaml(project.root / "config" / "agents.yml") or {}).get("agents") or {}
    reviewers = [e.get("agent") for e in pool] + list(effective["matrix_override"] or {})
    for name in reviewers:
        if name not in registry:
            raise AssignmentError(f"reviewer {name!r} is not in config/agents.yml")
        if not (registry[name] or {}).get("enabled", True):
            raise AssignmentError(f"reviewer {name!r} is disabled in config/agents.yml")
    families = {name: ac.model_family(cfg or {}) for name, cfg in registry.items()}
    return authors, pool, effective, families


@review.command("assign")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number (>= 1).")
@click.option("--json", "as_json", is_flag=True, help="Print the assignment as JSON.")
def assign_cmd(slug, round_, as_json):
    """Plan and record who reviews which draft in round N (review/round-N/assignment.json)."""
    from scieflow.core.project import Project
    from scieflow.core.review import assign
    from scieflow.core.review.settings import SettingsError

    project = Project.discover()
    try:
        ws = project.run_dir(slug)
        if not ws.is_dir():
            raise click.ClickException(f"no workspace {slug!r} under {project.workspace_root}")
        authors, pool, effective, families = _assignment_inputs(project, slug)
        plan = assign.plan(authors, pool, effective, families)
        path = assign.write(ws, round_, plan)
    except (assign.AssignmentError, SettingsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(path.read_text(), nl=False)
        return
    click.echo(f"Round {round_} of workspace/{slug} ({plan['source']}; "
               f"reviews_per_draft {plan['settings']['reviews_per_draft']}):")
    for author in plan["authors"]:
        name = author["agent"]
        got = [p for p in plan["pairs"] if p["author"] == name]
        marks = [p["reviewer"] + (" (self)" if p["self"] else "")
                 + (" (same family)" if p["same_family"] and not p["self"] else "")
                 for p in got]
        click.echo(f"  {name:<20} <- {', '.join(marks)}")
    for warning in plan["warnings"]:
        click.echo(f"  warning: {warning}")
    click.echo("every review is a fresh dispatch")
    click.echo(f"written: {path.relative_to(project.root)}")
