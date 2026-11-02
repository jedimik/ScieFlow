"""`scieflow sr` (M-H t2, plan 5.13): the systematic-review group.

t2 ships `protocol show|check` and `status`. Later tickets add search, records, screening,
kappa, conflicts, adjudication, verify, prisma, rob, grade and eval to this group.

Exit codes: 0 ok, 1 refused or invalid (SRError: the message names the rule and an id or path,
never record text or a query), 2 usage (click). Read commands take --json.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

import click

from scieflow.core import events
from scieflow.core.config import repo_root
from scieflow.core.design import lock
from scieflow.core.project import Project, ProjectError
from scieflow.research.sr import ledger, protocol
from scieflow.research.sr.errors import SRError

OK, REFUSED, USAGE = 0, 1, 2


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


def _emit(as_json: bool, payload: dict, lines: list) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    else:
        for line in lines:
            click.echo(line)


@click.group()
def sr() -> None:
    """Systematic review: protocol, search, dual screening, adjudication, PRISMA, appraisal."""


@sr.group(name="protocol")
def protocol_group() -> None:
    """The locked review protocol (read-only)."""


@protocol_group.command("show")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def protocol_show(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    try:
        doc = protocol.show(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"Protocol version {doc['version']} (sha256 {doc['content_hash'][:12]})."]
    for kind in ("include", "exclude"):
        for crit in doc["review"]["eligibility"].get(kind, []):
            lines.append(f"{kind} {crit.get('id')}: {crit.get('text')}")
    for strat in doc["review"].get("strategies", []):
        lines.append(f"strategy {strat.get('id')}: sources {', '.join(strat.get('sources', []))}")
    _emit(as_json, doc, lines)


@protocol_group.command("check")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def protocol_check(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    try:
        doc = protocol.check(project, ws)
    except SRError as exc:
        _fail(REFUSED, str(exc))
    lines = [f"strategy {row['id']}: {row['status']}" + (f" ({row['reason']})" if row["reason"] else "")
             for row in doc["strategies"]]
    lines.append("Nothing was sent: this check only reads the locked protocol.")
    _emit(as_json, doc, lines)


@sr.command("status")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def status_cmd(slug: str, as_json: bool) -> None:
    project = _project()
    ws = _run(project, slug)
    state = lock.state(project, ws)
    doc = {
        "run": slug,
        "protocol": {"status": state.status, "version": state.version, "content_hash": state.in_force_hash},
        "committed": {},
        "uncommitted": [],
        "problems": [],
    }
    kinds: Counter = Counter()
    for row in events.read(ws, types=("sr.committed",)):
        kinds[str((row.get("data") or {}).get("kind"))] += 1
    doc["committed"] = dict(sorted(kinds.items()))
    try:
        doc["uncommitted"] = list(ledger.view(ws).uncommitted_ops)
    except SRError as exc:
        doc["problems"].append(str(exc))
    lines = [f"protocol: {state.status}" + (f" (version {state.version})" if state.version else "")]
    lines.append("committed: " + (", ".join(f"{k} {v}" for k, v in doc["committed"].items()) or "none"))
    if doc["uncommitted"]:
        lines.append("uncommitted operations (not read): " + ", ".join(map(str, doc["uncommitted"])))
    lines += [f"problem: {p}" for p in doc["problems"]]
    _emit(as_json, doc, lines)
