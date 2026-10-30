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
from typing import NoReturn

import click
import yaml

from scieflow.core import gates, network
from scieflow.core.project import Project, ProjectError
from scieflow.core.review.claims import LeakError
from scieflow.integrations.scite import mcp as scite_mcp
from scieflow.research.journal import adopt as adopt_mod
from scieflow.research.journal import diff as diff_mod
from scieflow.research.journal import facts, profile, prompt as prompt_mod, render, sources, state
from scieflow.research.lib import http, providers

OK, REFUSED, USAGE, BOUNDARY, NO_EGRESS = 0, 1, 2, 3, 6


def _fail(message: str, code: int = REFUSED) -> NoReturn:
    click.echo(message, err=True)
    raise SystemExit(code)


def _profiles_dir(project: Project) -> Path:
    return project.root / "config" / "journals"


def _resolve(project: Project, target: str) -> tuple[Path, str]:
    """(file, slug) for a slug or a path to a profile file."""
    if target.endswith(".yml") or "/" in target:
        path = Path(target)
        return path, path.stem
    try:
        pattern = re.compile(profile.load_schema(project.root)["patterns"]["slug"])
    except profile.ProfileError as exc:
        _fail(str(exc))
    if not pattern.match(target):
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


def _refuse_invalid(project: Project, data: dict, target: str) -> None:
    """`show` prints only a profile that validates; a malformed one is refused, never rendered (M-F 5.2)."""
    errors = [p for p in _problems(project, data) if p.level == "error"]
    if errors:
        _fail(f"{target}: not a valid profile ({len(errors)} error(s)); "
              f"run `scieflow journal validate {target}` for the rules")


@click.group()
def journal():
    """Target journals: show, list and validate journal profiles."""


@journal.command()
@click.argument("target")
@click.option("--article-type", "article_type", default=None, help="Show one article type only.")
@click.option("--json", "as_json", is_flag=True, help="Print the profile as JSON.")
@click.option("--prompt", is_flag=True, help="Print the profile as a labelled data block for a prompt.")
def show(target, article_type, as_json, prompt):
    """Show a profile (TARGET: a slug or a path to a .yml profile). Refuses a profile that does not validate."""
    project = Project.discover()
    data, slug = _load(project, target)
    _refuse_invalid(project, data, target)
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


# --- M-F t3: research (facts, the gate, the fetch) and add-source ------------------------------


def _run_folder(project: Project, run: str) -> Path:
    try:
        folder = project.run_dir(run)
    except ProjectError as exc:
        _fail(f"error: {exc}")
    if not folder.is_dir():
        _fail(f"no run {run!r} (looked for {folder})")
    return folder


def _exit_for(exc: BaseException) -> NoReturn:
    """The exit codes of M-F 5.2: 1 refused, 3 a Gateway policy or leak refusal (nothing sent),
    6 a no-egress miss (nothing sent)."""
    if isinstance(exc, LeakError):
        _fail(str(exc), BOUNDARY)
    if isinstance(exc, network.NetworkMiss):
        _fail(str(exc), NO_EGRESS)
    if isinstance(exc, providers.PolicyError):
        _fail(f"refused: {exc}", BOUNDARY)
    if isinstance(exc, facts.FactsError):
        _fail(str(exc), exc.code)
    if isinstance(exc, http.HTTPFailure):
        _fail(f"failed: {exc.classification}", REFUSED)
    _fail(f"refused: {exc}", REFUSED)


@journal.command()
@click.argument("run")
@click.option("--journal", "journal_name", default=None, help="The journal's name.")
@click.option("--issn", default=None, help="The journal's ISSN (preferred: it names one source).")
@click.option("--url", "urls", multiple=True, help="An author-guideline page (https; at most 10).")
@click.option("--slug", default=None, help="The profile slug (default: from the name).")
@click.option("--as-of", "as_of", default=None, help="Reference date YYYY-MM-DD (default: today, UTC).")
@click.option("--restart", is_flag=True, help="Remove the working files first (never adopted/).")
@click.option("--retry-failed", is_flag=True, help="Re-request only the pages that failed.")
@click.option("--refresh-of", "refresh_of", default=None,
              help="Pre-fill name, ISSN-L and gateway URLs from an adopted profile (a new gate is still required).")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def research(run, journal_name, issn, urls, slug, as_of, restart, retry_failed, refresh_of, as_json):
    """Journal facts, then the guideline pages a human approves, then the saved pages (RUN: a run)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    if refresh_of is not None:
        data, refresh_slug = _load(project, refresh_of)
        identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
        provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
        journal_name = journal_name or identity.get("name")
        issn = issn or identity.get("issn_l")
        slug = slug or refresh_slug
        gateway = [row.get("url") for row in provenance.get("sources") or []
                   if isinstance(row, dict) and row.get("retrieved_by") == "gateway"]
        urls = tuple(dict.fromkeys([*urls, *(u for u in gateway if isinstance(u, str))]))
    if not journal_name:
        _fail("give --journal NAME, or --refresh-of SLUG for an adopted profile", USAGE)
    try:
        day = dt.date.fromisoformat(as_of) if as_of else dt.datetime.now(dt.timezone.utc).date()
    except ValueError:
        _fail("--as-of takes YYYY-MM-DD", USAGE)
    try:
        chosen = state.check_slug(slug or state.slugify(journal_name))
        policy = providers.load(project.root)

        def make_gateway(run_hosts=None):
            return providers.Gateway(folder, policy, run_hosts=run_hosts)

        result = sources.research(project, folder, journal=journal_name, slug=chosen, issn=issn,
                                  extra_urls=list(urls), as_of=day, restart=restart,
                                  retry_failed=retry_failed, policy=policy,
                                  make_gateway=make_gateway)
    except (LeakError, providers.PolicyError, network.NetworkMiss, facts.FactsError,
            http.HTTPFailure, gates.GateError, state.StateError, ValueError) as exc:
        _exit_for(exc)
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    else:
        for line in result.lines:
            click.echo(line)
    raise SystemExit(result.exit_code)


@journal.command("add-source")
@click.argument("run")
@click.option("--file", "file_path", required=True, type=click.Path(), help="A saved page.")
@click.option("--url", required=True, help="The https address the page came from.")
@click.option("--note", default="", help="A short note (kept with the row).")
def add_source(run, file_path, url, note):
    """Add a guideline page the human saved (.html, .htm, .txt or .md; at most 5 MB)."""
    try:
        scite_mcp.refuse_in_dispatch()
    except providers.PolicyError as exc:
        _fail(f"refused: {exc}", BOUNDARY)
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        row = sources.add_source(state.journal_dir(folder), Path(file_path), url, note)
    except (sources.SourcesError, state.StateError, ValueError) as exc:
        _fail(str(exc), REFUSED)
    click.echo(f"added {row['id']} from {row['host']} ({row['chars']} characters)")


# --- M-F t4: prompt, adopt, diff, style ---------------------------------------------------------


@journal.command("prompt")
@click.argument("run")
@click.option("--for", "agent", default=None, help="The agent this prompt is for (a plain slug).")
@click.option("--cross-check", "cross_check", is_flag=True,
              help="Ask the primary agent to verify every draft-*.yml against the sources.")
@click.option("--write", is_flag=True, help="Write the prompt to prompts/ instead of printing it.")
def prompt_cmd(run, agent, cross_check, write):
    """The sub-agent prompt that drafts this run's journal profile (RUN: a run)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    if agent is not None and not prompt_mod.AGENT.match(agent):
        _fail(f"not an agent name: {agent!r} (lowercase letters, digits and hyphens)", USAGE)
    try:
        text, _out_name, file_name = prompt_mod.build(project, folder, agent=agent, cross_check=cross_check)
    except (state.StateError, profile.ProfileError) as exc:
        _fail(str(exc))
    if write:
        target = folder / "prompts" / file_name
        state.write_text(target, text)
        click.echo(f"wrote {target}")
    else:
        click.echo(text, nl=False)


@journal.command("adopt")
@click.argument("run")
@click.option("--draft", "draft_path", type=click.Path(), default=None,
              help="The draft to adopt (default: journal/profile.draft.yml).")
@click.option("--replace", is_flag=True, help="Replace a different adopted profile of the same slug.")
@click.option("--as-agent", "as_agent", is_flag=True, help="Record the adoption as made by an agent.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def adopt_cmd(run, draft_path, replace, as_agent, as_json):
    """Validate the draft against the run's sources and adopt it (RUN: a run)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = adopt_mod.adopt(project, folder, draft_path=Path(draft_path) if draft_path else None,
                                 replace=replace, as_agent=as_agent)
    except (state.StateError, profile.ProfileError, ValueError) as exc:
        _fail(str(exc))
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    else:
        for line in result.lines:
            click.echo(line)


def _without_provenance(data: dict) -> dict:
    """Provenance is written by adoption, never by the draft: it is left out of the comparison."""
    return {key: value for key, value in data.items() if key != "provenance"}


@journal.command("diff")
@click.argument("slug")
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the differences as JSON.")
def diff_cmd(slug, run, as_json):
    """Field-level difference between the adopted profile SLUG and RUN's draft.

    The draft is compared as adoption would write it: its facts blocks come from facts.json, and
    provenance is left out on both sides (review t4 finding 3)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    adopted_path, name = _resolve(project, slug)
    if not adopted_path.is_file():
        _fail(f"no adopted profile {slug!r} (looked for {adopted_path})")
    jdir = state.journal_dir(folder)
    try:
        adopted = profile.load_profile(adopted_path)
        draft = profile.load_profile(jdir / "profile.draft.yml")
    except profile.ProfileError as exc:
        _fail(str(exc))
    try:
        facts_data = state.read_json(facts.facts_path(jdir))
    except state.StateError as exc:
        _fail(str(exc))
    if isinstance(facts_data, dict):
        draft = {**draft, **adopt_mod.facts_blocks(facts_data, name)}
    rows = diff_mod.compare(_without_provenance(adopted), _without_provenance(draft))
    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True, default=str))
    else:
        for row in rows:
            click.echo(diff_mod.line(row))
    raise SystemExit(OK)


@journal.command("style")
@click.argument("run")
def style_cmd(run):
    """Write the profile's figures block as an M-D2 style file (RUN: a run)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        target = diff_mod.write_style(project, folder)
    except (state.StateError, profile.ProfileError) as exc:
        _fail(str(exc))
    click.echo(f"wrote {target}")
