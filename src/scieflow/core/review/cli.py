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
