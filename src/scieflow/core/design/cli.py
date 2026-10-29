"""`scieflow design` (M-D1): study design (design.yml), reporting checklists and power.

Commands: guidelines (read-only); init and render (write only inside the run's design/ directory);
check and show (read-only); lock and amend (write pending.json, the amendment diff and design.md).
The lock ledger and locked copies change only when a human answers the design-lock gate.

Exit codes: 0 ok, 1 problems found or refused, 2 usage.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core.config import repo_root
from scieflow.core.design import lock, model, reporting, render, templates
from scieflow.core.design import schema as design_schema
from scieflow.core.design.model import Absent, Invalid, Problem, Report
from scieflow.core.project import Project, ProjectError

OK, REFUSED, USAGE = 0, 1, 2
PROBLEMS = REFUSED


def _fail(code: int, message: str) -> NoReturn:
    click.echo(message, err=True)
    sys.exit(code)


def _root() -> Path:
    return repo_root()


def _project() -> Project:
    try:
        return Project(_root().resolve())
    except FileNotFoundError as exc:
        _fail(USAGE, str(exc))


def _run(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        _fail(USAGE, str(exc))
    if not ws.is_dir():
        _fail(USAGE, f"no run workspace/{slug}")
    return ws


def _study_types(project: Project) -> tuple:
    try:
        return design_schema.study_types(project.root)
    except design_schema.SchemaError as exc:
        _fail(USAGE, str(exc))


def _write(path: Path, text: str) -> None:
    """Write through a temporary file, never following a symlink (O_NOFOLLOW)."""
    if path.is_symlink():
        _fail(REFUSED, f"{path.name} is a symlink; refusing to write through it")
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    except OSError:
        _fail(REFUSED, f"{tmp.name} cannot be written safely (is it a symlink?)")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _emit(as_json: bool, payload: dict, lines: list) -> None:
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines:
            click.echo(line)


def _loaded(ws: Path):
    got = model.load(ws)
    if isinstance(got, Absent):
        _fail(USAGE, "no design/design.yml; create one with: scieflow design init <slug> --type <type>")
    return got


def _report(project: Project, ws: Path, got) -> Report:
    if isinstance(got, Invalid):
        return Report(errors=[Problem("invalid", "design", got.reason)])
    try:
        return model.check(project, ws, got)
    except design_schema.SchemaError as exc:
        _fail(USAGE, str(exc))


def _guideline_status(project: Project, got: dict) -> dict:
    guides = got.get("reporting_guidelines")
    if not isinstance(guides, list):
        return {}
    try:
        known = {c.id: c.verification.get("status", "unknown") for c in reporting.registry(project.root)}
    except reporting.ReportingError:
        return {}
    return {g: known[g] for g in guides if isinstance(g, str) and g in known}


@click.group("design")
def design() -> None:
    """Study design: design.yml (init, check, render, show), reporting checklists, power."""


@design.command("guidelines")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option("--check", is_flag=True, help="Validate every checklist file (exit 1 on problems).")
def guidelines(as_json: bool, check: bool) -> None:
    """List the reporting checklists (CONSORT, STROBE, ...) and their verification status."""
    root = _root()
    try:
        if check:
            problems = reporting.validate_all(root)
            for problem in problems:
                click.echo(str(problem))
            if problems:
                sys.exit(PROBLEMS)
            click.echo(f"ok: {len(reporting.registry(root))} reporting checklists validate")
            return
        checklists = reporting.registry(root)
    except reporting.ReportingError as exc:
        click.echo(f"design guidelines: {exc}", err=True)
        sys.exit(PROBLEMS)
    rows = [
        {"id": c.id, "name": c.name, "applies_to": list(c.applies_to),
         "items": len(c.items), "verification": c.verification["status"]}
        for c in checklists
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        click.echo(f"{row['id']:<14} {row['name']:<14} {','.join(row['applies_to']):<22} "
                   f"{row['items']:>3} items  {row['verification']}")


@design.command("init")
@click.argument("slug")
@click.option("--type", "study_type", required=True,
              help="A study type from schemas/design.yml (rct, cohort, computational, ...).")
@click.option("--title", default=None, help="Title (default: the run slug).")
@click.option("--force", is_flag=True, help="Replace an existing design.yml (never when locked).")
def init_cmd(slug: str, study_type: str, title: str | None, force: bool) -> None:
    """Write a commented design.yml template; every TODO must be filled before check passes."""
    project = _project()
    ws = _run(project, slug)
    if study_type not in _study_types(project):
        _fail(USAGE, "unknown study type; choose one listed in schemas/design.yml enums.study_types")
    directory = ws / model.DESIGN_DIR
    if directory.is_symlink():
        _fail(REFUSED, "design/ is a symlink; refusing to write")
    if directory.exists() and not directory.is_dir():
        _fail(REFUSED, "design is not a directory; refusing to write")
    ledger = directory / "lock.jsonl"
    if ledger.exists() or ledger.is_symlink():
        _fail(REFUSED, "design/lock.jsonl exists: the design is locked and init will not replace it")
    target = model.design_path(ws)
    if (target.exists() or target.is_symlink()) and not force:
        _fail(REFUSED, "design/design.yml exists; use --force to replace it")
    directory.mkdir(exist_ok=True)
    _write(target, templates.template(study_type, title or slug))
    click.echo(f"wrote design/design.yml (type {study_type}); fill every TODO, "
               f"then run: scieflow design check {slug}")


@design.command("check")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def check_cmd(slug: str, as_json: bool) -> None:
    """Check design/design.yml: errors block a lock; warnings are advice."""
    project = _project()
    ws = _run(project, slug)
    got = _loaded(ws)
    report = _report(project, ws, got)
    payload = {"slug": slug, "status": "invalid" if isinstance(got, Invalid) else "draft",
               **report.to_dict()}
    lines = [f"design {slug}: {len(report.errors)} error(s), {len(report.warnings)} warning(s)"]
    lines += [f"error {problem}" for problem in report.errors]
    lines += [f"warning {problem}" for problem in report.warnings]
    _emit(as_json, payload, lines)
    if report.errors:
        sys.exit(REFUSED)


@design.command("render")
@click.argument("slug")
def render_cmd(slug: str) -> None:
    """Write design/design.md, generated from design.yml (never edit it by hand)."""
    project = _project()
    ws = _run(project, slug)
    got = _loaded(ws)
    if isinstance(got, Invalid):
        _fail(REFUSED, f"design.yml is invalid ({got.reason}); fix it, then render")
    report = _report(project, ws, got)
    if not model.is_plain_json(report):
        _fail(REFUSED, "design.yml holds a value that is not plain JSON (rule json-value); "
                       "fix it, then render")
    digest = model.content_hash(got)
    text = render.render(got, report, digest, power=model.power_document(ws, got),
                         guidelines=_guideline_status(project, got))
    _write(ws / model.DESIGN_DIR / "design.md", text)
    click.echo(f"wrote design/design.md (design sha256 {digest[:12]})")


@design.command("lock")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def lock_cmd(slug: str, as_json: bool) -> None:
    """Lock the design: opens a design-lock gate that only a human answers (approve or reject)."""
    project = _project()
    ws = _run(project, slug)
    try:
        gate = lock.lock(project, ws)
    except lock.LockError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, gate, [f"opened design-lock gate {gate['id']} for run {slug}",
                          f"a human answers it: scieflow gate answer {slug} {gate['id']} approve|reject"])


@design.command("amend")
@click.argument("slug")
@click.option("--summary", required=True, help="What changes, in one line.")
@click.option("--rationale", required=True, help="Why it changes.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def amend_cmd(slug: str, summary: str, rationale: str, as_json: bool) -> None:
    """Amend a locked design: opens a design-lock gate with the diff; a human approves version N+1."""
    project = _project()
    ws = _run(project, slug)
    try:
        gate = lock.amend(project, ws, summary, rationale)
    except lock.LockError as exc:
        _fail(REFUSED, str(exc))
    _emit(as_json, gate, [f"opened design-lock gate {gate['id']} (amendment) for run {slug}",
                          f"a human answers it: scieflow gate answer {slug} {gate['id']} approve|reject"])


@design.command("show")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def show_cmd(slug: str, as_json: bool) -> None:
    """Show the design's lock state, hashes and SAP metric names (read-only)."""
    project = _project()
    ws = _run(project, slug)
    st = lock.state(project, ws)
    types = _study_types(project)
    study = st.study_type if st.study_type in types else None
    if st.status == "none":
        payload = {"slug": slug, "status": "none"}
        lines = [f"run {slug}: no design (status none)"]
    elif st.status == "invalid":
        got = model.load(ws)
        if isinstance(got, Invalid):
            reason = got.reason
            payload = {"slug": slug, "status": "invalid", "reason": reason}
        else:
            reason = "a value is not plain JSON (rule json-value); run design check"
            payload = {"slug": slug, "status": "invalid", "errors": len(st.report.errors),
                       "warnings": len(st.report.warnings), "reason": reason}
        lines = [f"run {slug}: design invalid ({reason})"]
    else:
        report = st.report or Report()
        payload = {"slug": slug, "status": st.status, "study_type": study,
                   "content_hash": st.design_hash, "errors": len(report.errors),
                   "warnings": len(report.warnings), "sap_metrics": st.sap_metrics,
                   "version": st.version, "in_force_hash": st.in_force_hash,
                   "current_hash": st.current_hash, "pending_gate": st.pending_gate,
                   "trail": st.trail, "problem": st.problem}
        shown = (st.current_hash or st.design_hash or "-")[:12]
        lines = [f"run {slug}: {st.status} ({study or 'unknown'}), version {st.version or '-'}, "
                 f"{len(report.errors)} error(s), {len(report.warnings)} warning(s), sha256 {shown}"]
        if st.problem:
            lines.append(f"ledger: {st.problem}")
        if st.pending_gate:
            lines.append(f"pending gate: {st.pending_gate}")
    _emit(as_json, payload, lines)
