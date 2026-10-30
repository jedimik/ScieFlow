"""`scieflow manuscript` (M-F plan 5.14, t7 subset): `check`, `checklist-prompt`, `render`, `build`. Exit
codes: 0 ok, 1 a check failed or the run is refused, 2 usage. Package and letter come with later tickets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core.project import Project, ProjectError
from scieflow.research.journal import state
from scieflow.research.manuscript import build as build_mod
from scieflow.research.manuscript import check as check_mod
from scieflow.research.manuscript import checklist, judgement, letters as letters_mod
from scieflow.research.manuscript import package as package_mod, render as render_mod, templates, tex

OK, REFUSED, USAGE = 0, 1, 2


def _fail(message: str, code: int = REFUSED) -> NoReturn:
    click.echo(message, err=True)
    raise SystemExit(code)


def _run_folder(project: Project, run: str) -> Path:
    try:
        folder = project.run_dir(run)
    except ProjectError as exc:
        _fail(f"error: {exc}")
    if not folder.is_dir():
        _fail(f"no run {run!r} (looked for {folder})")
    return folder


@click.group()
def manuscript():
    """Manuscripts: check against the journal, render, build and package."""


@manuscript.command()
@click.argument("run")
@click.option("--journal", default=None, help="The journal slug (default: metadata.yml journal).")
@click.option("--article-type", "article_type", default=None, help="The article type id.")
@click.option("--guideline", "guidelines", multiple=True,
              help="A reporting guideline id (recorded in the report; checked by a later step).")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--json", "as_json", is_flag=True, help="Print the report document as JSON.")
@click.option("--no-write", "no_write", is_flag=True, help="Write nothing (no report, no note).")
def check(run, journal, article_type, guidelines, main_file, as_json, no_write):
    """Check the manuscript of RUN against its journal profile (M-F 5.9)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        report = check_mod.run_check(project.root, folder, journal=journal, article_type=article_type,
                                     main=main_file, guidelines=guidelines)
    except check_mod.CheckError as exc:
        _fail(str(exc))
    json_path = md_path = None
    if not no_write:
        json_path, md_path = check_mod.write_report(report, folder)
        check_mod.note_check(folder, report)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"manuscript check: {len(report.errors)} errors, {len(report.warnings)} warnings")
        for finding in report.findings:
            where = f" ({finding.where})" if finding.where else ""
            click.echo(f"  {finding.severity} {finding.rule}{where}: {finding.message}")
        if not no_write:
            click.echo(f"report: {json_path}")
            click.echo(f"report: {md_path}")
    raise SystemExit(report.exit_code)


@manuscript.command("checklist-prompt")
@click.argument("run")
@click.option("--guideline", "guideline", required=True, help="The reporting guideline id to judge.")
@click.option("--main", "main_file", default=None, help="The main TeX file (default main.tex).")
@click.option("--write", "write", is_flag=True, help="Write prompts/checklist-<id>.md (default: print it).")
def checklist_prompt(run, guideline, main_file, write):
    """Write the reviewer prompt for the checklist items that need judgement (M-F 5.10).

    Dispatch it with --role research.reviewer; the reviewer writes report/checklist/<id>.judgement.json.
    """
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        text = judgement.prompt_for(project.root, folder, guideline, main=main_file)
    except (tex.TexError, checklist.ChecklistError) as exc:
        _fail(str(exc))
    if write:
        try:
            path = judgement.write_prompt(folder, guideline, text)
        except state.StateError as exc:
            _fail(str(exc))
        click.echo(f"prompt: {path}")
    else:
        click.echo(text, nl=False)
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--class", "cls", type=click.Choice(list(templates.CLASSES)), default=None,
              help="The paper class (default: the profile's latex_class, else article).")
@click.option("--article-type", "article_type", default=None, help="The article type id (default: metadata.yml).")
@click.option("--force", is_flag=True, help="Replace a hand-edited or unmarked main.tex (kept as main.tex.bak-<sha12>).")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def render(run, cls, article_type, force, as_json):
    """Write manuscript/main.tex from metadata, the profile and the section files (M-F 5.11)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = render_mod.render_run(project.root, folder, cls=cls, article_type=article_type, force=force)
    except render_mod.RenderError as exc:
        _fail(f"render refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        state_text = "written" if result.written else "unchanged"
        click.echo(f"render: {state_text} {result.path} (class {result.cls}, sha256 {result.sha256[:12]})")
        if result.backup is not None:
            click.echo(f"render: kept the previous file as {result.backup}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--engine", default=None, type=click.Choice(["latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"]),
              help="The engine (default: the first of latexmk, pdflatex, xelatex, lualatex found; tectonic only when named).")
@click.option("--timeout", type=click.IntRange(30, 1800), default=600, show_default=True,
              help="Seconds per engine call.")
@click.option("--json", "as_json", is_flag=True, help="Print the build record as JSON.")
def build(run, engine, timeout, as_json):
    """Compile manuscript/main.tex in a scratch copy with shell escape off (M-F 5.12)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = build_mod.build_run(project.root, folder, engine=engine, timeout=timeout)
    except build_mod.BuildError as exc:
        _fail(f"build refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for note in result.notes:
            click.echo(f"note: {note}")
        if result.ok:
            click.echo(f"build: ok ({result.engine}), pdf sha256 {result.pdf_sha256[:12]}")
            click.echo(f"build: {folder / 'build' / 'main.pdf'}")
    if not result.ok:
        click.echo("build failed; last lines of the log:", err=True)
        click.echo(result.log_tail, err=True)
        _fail(f"build failed with engine {result.engine}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--json", "as_json", is_flag=True, help="Print the package record as JSON.")
def package(run, as_json):
    """Write report/submission/ for RUN once every gate holds (M-F 5.13). Uploads nothing."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        result = package_mod.package_run(project, folder)
    except package_mod.PackageError as exc:
        _fail(f"package refused [{exc.rule}]: {exc.message}")
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        click.echo(f"package: {result.folder}")
        for name in sorted(result.files):
            click.echo(f"  {name}")
    raise SystemExit(OK)


@manuscript.command()
@click.argument("run")
@click.option("--kind", type=click.Choice(["cover", "response"]), required=True,
              help="The starter letter: cover or response.")
@click.option("--force", is_flag=True, help="Replace an existing letter.")
def letter(run, kind, force):
    """Write a starter cover letter or response to reviewers into manuscript/letters/ (M-F 5.13)."""
    project = Project.discover()
    folder = _run_folder(project, run)
    try:
        path = letters_mod.write_letter(project.root, folder, kind, force=force)
    except letters_mod.LetterError as exc:
        _fail(f"letter refused [{exc.rule}]: {exc.message}")
    click.echo(f"letter: {path}")
    raise SystemExit(OK)
