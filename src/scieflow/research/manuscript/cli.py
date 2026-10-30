"""`scieflow manuscript` (M-F plan 5.14, t5 subset): `check`. Exit codes: 0 ok, 1 a check failed or
the run is refused, 2 usage. Render, build, package and letter come with later tickets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core.project import Project, ProjectError
from scieflow.research.manuscript import check as check_mod
from scieflow.research.manuscript import checklist, judgement, tex

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
        click.echo(f"prompt: {judgement.write_prompt(folder, guideline, text)}")
    else:
        click.echo(text, nl=False)
    raise SystemExit(OK)
