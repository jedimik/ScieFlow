"""`scieflow journal` commands (M-F 5.2): show, list, validate. Read-only.

Profiles live in config/journals/<slug>.yml (structured) or config/journals/<slug>.md (a
hand-written legacy page such as softwarex.md). Exit codes: 0 ok (warnings allowed), 1 refused or a
problem found, 2 usage.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import click
import yaml

from scieflow.core.project import Project, ProjectError
from scieflow.research.journal import profile, render

OK, REFUSED, USAGE = 0, 1, 2
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _fail(message: str, code: int = REFUSED):
    click.echo(message, err=True)
    raise SystemExit(code)


def _profiles_dir(project: Project) -> Path:
    return project.root / "config" / "journals"


def _resolve(project: Project, target: str) -> tuple[Path, str]:
    """(file, slug) for a slug or a path to a profile file."""
    if target.endswith(".yml") or "/" in target:
        path = Path(target)
        return path, path.stem
    if not _SLUG.match(target):
        _fail(f"not a journal slug: {target!r}", USAGE)
    return _profiles_dir(project) / f"{target}.yml", target


def _load(project: Project, target: str) -> tuple[dict, str]:
    path, slug = _resolve(project, target)
    if not path.exists() and not path.is_symlink():
        _fail(f"no journal profile {target!r} (looked for {path})")
    try:
        return profile.load_profile(path), slug
    except profile.ProfileError as exc:
        _fail(str(exc))


def _run_dir(project: Project, data: dict) -> Path | None:
    prov = data.get("provenance")
    run = prov.get("run") if isinstance(prov, dict) else None
    if not isinstance(run, str):
        return None
    try:
        path = project.run_dir(run)
    except ProjectError:
        return None
    return path if path.is_dir() else None


def _problems(project: Project, data: dict, today: dt.date | None = None) -> list:
    try:
        return profile.validate(data, root=project.root, run_dir=_run_dir(project, data), today=today)
    except profile.ProfileError as exc:
        return [profile.Problem("schema", "", str(exc))]


@click.group()
def journal():
    """Target journals: show, list and validate journal profiles."""


@journal.command()
@click.argument("target")
@click.option("--article-type", "article_type", default=None, help="Show one article type only.")
@click.option("--json", "as_json", is_flag=True, help="Print the profile as JSON.")
@click.option("--prompt", is_flag=True, help="Print the profile as a labelled data block for a prompt.")
def show(target, article_type, as_json, prompt):
    """Show a profile (TARGET: a slug or a path to a .yml profile)."""
    data, slug = _load(Project.discover(), target)
    out = data
    if article_type is not None:
        matches = [a for a in data.get("article_types") or [] if isinstance(a, dict) and a.get("id") == article_type]
        if not matches:
            _fail(f"{slug}: no article type {article_type!r}")
        out = matches[0]
    if as_json:
        click.echo(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True, default=str))
    elif prompt:
        text = yaml.safe_dump(out, sort_keys=False, allow_unicode=True, default_flow_style=False)
        click.echo(render.fenced_block("JOURNAL PROFILE", text), nl=False)
    elif article_type is not None:
        click.echo(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, default_flow_style=False), nl=False)
    else:
        click.echo(render.render_md(data, slug), nl=False)


@journal.command("list")
@click.option("--json", "as_json", is_flag=True, help="Print the list as JSON.")
def list_profiles(as_json):
    """List every profile: structured (.yml) or legacy (.md only), adopted date, stale?"""
    project = Project.discover()
    folder = _profiles_dir(project)
    names = sorted({p.stem for p in folder.iterdir() if p.suffix in (".yml", ".md")}) if folder.is_dir() else []
    rows = []
    for name in names:
        yml = folder / f"{name}.yml"
        if yml.is_file() and not yml.is_symlink():
            try:
                data = profile.load_profile(yml)
            except profile.ProfileError as exc:
                rows.append({"slug": name, "kind": "invalid", "adopted_at": None, "stale": None, "error": str(exc)})
                continue
            prov = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
            stale = any(p.rule == "stale" for p in _problems(project, data))
            rows.append({"slug": name, "kind": "structured", "adopted_at": prov.get("adopted_at"), "stale": stale})
        else:
            rows.append({"slug": name, "kind": "legacy", "adopted_at": None, "stale": None})
    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        click.echo("no journal profiles in config/journals/")
    for row in rows:
        stale = "" if row["stale"] is None else (" stale" if row["stale"] else " current")
        adopted = row["adopted_at"] or "-"
        click.echo(f"{row['slug']}  {row['kind']}  adopted {adopted}{stale}")


@journal.command()
@click.argument("target")
@click.option("--json", "as_json", is_flag=True, help="Print the problems as JSON.")
def validate(target, as_json):
    """Check a profile against the format rules (TARGET: a slug or a path to a .yml profile)."""
    project = Project.discover()
    data, slug = _load(project, target)
    problems = _problems(project, data)
    if as_json:
        click.echo(json.dumps([p.to_dict() for p in problems], indent=2, ensure_ascii=False))
    else:
        for problem in problems:
            click.echo(str(problem))
        errors = [p for p in problems if p.level == "error"]
        click.echo(f"{slug}: {len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
    if any(p.level == "error" for p in problems):
        raise SystemExit(REFUSED)
    raise SystemExit(OK)
