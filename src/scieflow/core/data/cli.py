"""`scieflow data` (M-B Part A 5.10): declare, levels, pin.

Exit codes: 0 ok, 1 refused, 2 usage or not available, 3 invalid policy file.
Every write refuses inside a dispatch (the run's data is read-only to agents).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import NoReturn

import click

from scieflow.core import config, events
from scieflow.core.data import levels, policy, scan
from scieflow.core.data.dispatch import report_findings
from scieflow.core.project import Project, ProjectError

OK, REFUSED, USAGE, POLICY_INVALID = 0, 1, 2, 3
SUGGESTION = ("suggestion: set enforcement: refuse in config/data-policy.yml "
              "(ScieFlow never sets it)")


def _fail(code: int, message: str) -> NoReturn:
    click.echo(message, err=True)
    sys.exit(code)


def _project() -> Project:
    try:
        return Project.discover()
    except FileNotFoundError as exc:
        _fail(USAGE, str(exc))


def _run(project: Project, slug: str):
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        _fail(USAGE, str(exc))
    if not ws.is_dir():
        _fail(USAGE, f"no run workspace/{slug}")
    return ws


def _refuse_in_dispatch(command: str) -> None:
    from scieflow.integrations.scite.mcp import in_dispatch

    if in_dispatch():
        _fail(REFUSED, f"data {command}: refused inside a dispatch "
                       "(a run's data is read-only to agents)")


def _policy_or_fail(root) -> policy.Policy:
    try:
        return policy.load(root)
    except policy.PolicyInvalid as exc:
        _fail(POLICY_INVALID, str(exc))


def _policy_or_none(root):
    try:
        return policy.load(root)
    except policy.PolicyInvalid as exc:
        click.echo(f"note: {exc}", err=True)
        return None


def _suggestion(root, pol) -> str | None:
    if pol is not None and pol.enforcement == "warn" and levels.project_has_l2(root, pol):
        return SUGGESTION
    return None


def _emit(as_json: bool, payload: dict, lines: list[str]) -> None:
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines:
            click.echo(line)


def _assigned_agents(root, ws) -> list[str]:
    names: set[str] = set()
    for value in (config.load_run_config(ws, root).get("assignments") or {}).values():
        for name in ([value] if isinstance(value, str) else value if isinstance(value, list) else []):
            if isinstance(name, str):
                names.add(name)
    return sorted(names)


def _boundaries(project: Project, ws, pol) -> dict:
    from scieflow.core import sandbox

    agents = config.load_agents(project.root)
    try:
        grants = policy.writable_grants(project.root)
    except sandbox.SandboxError as exc:
        _fail(USAGE, str(exc))
    out = {}
    for name in _assigned_agents(project.root, ws):
        if name in agents:
            b = policy.boundary(pol, name, agents[name], root=project.root,
                                writable_areas=grants)
            out[name] = {"kind": b.kind, "endpoint": b.endpoint, "reason": b.reason}
    return out


@click.group()
def data():
    """Data-protection levels for runs: declare, inspect, pin safe agents."""


@data.command("declare")
@click.argument("slug")
@click.argument("glob", required=False)
@click.option("--level", type=click.Choice(levels.DECLARABLE), help="L0, L1 or L2.")
@click.option("--kind", type=click.Choice(levels.KINDS), default="other", show_default=True)
@click.option("--reason", default="", help="Why. Required; recorded in data.yml.")
@click.option("--default", "default_level", type=click.Choice(levels.DECLARABLE),
              help="Set the run's default level instead of an entry.")
@click.option("--as-agent", is_flag=True,
              help="Agent use: may only add an entry or raise the default.")
@click.option("--remove", is_flag=True, help="Remove the entry for GLOB (human only).")
@click.option("--json", "as_json", is_flag=True)
def declare_cmd(slug, glob, level, kind, reason, default_level, as_agent, remove, as_json):
    """Declare the level of a run's files (GLOB is run-relative)."""
    _refuse_in_dispatch("declare")
    project = _project()
    ws = _run(project, slug)
    reason = reason.strip()
    if default_level and (glob or remove or level):
        _fail(USAGE, "--default sets the run's default level; give it without GLOB, "
                     "--level or --remove")
    if not default_level:
        if not glob:
            _fail(USAGE, "declare needs GLOB (or --default LEVEL)")
        problem = levels.path_problem(glob)
        if problem:
            _fail(REFUSED, f"declare: GLOB {problem}")
        if not remove and not level:
            _fail(USAGE, "declare needs --level L0|L1|L2 (or --remove)")
    if not reason:
        _fail(REFUSED, "declare: --reason is required (it is recorded in data.yml)")

    actor = "agent" if as_agent else "human"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcome: dict = {}

    def change(df: levels.DataFile) -> None:
        if default_level:
            lowered = levels.rank(default_level) < levels.rank(df.default_level)
            if as_agent and lowered:
                raise levels.DataFileError("an agent may only raise default_level")
            df.default_level = default_level
            outcome.update(path=None, level=default_level, lowered=lowered, default=True)
            return
        matches = [i for i, e in enumerate(df.levels) if e.get("path") == glob]
        if matches and as_agent:
            raise levels.DataFileError(
                "an agent may not change or remove an existing declaration; a human must")
        if remove:
            if not matches:
                raise levels.DataFileError(f"no declaration for {glob!r} to remove")
            old = df.levels.pop(matches[0])
            outcome.update(path=glob, level=old["level"], lowered=True, default=False)
            return
        entry = {"path": glob, "level": level, "kind": kind, "reason": reason,
                 "declared_by": actor, "declared_at": now}
        lowered = False
        if matches:
            old = df.levels[matches[0]]
            lowered = levels.rank(level) < levels.rank(old["level"])
            df.levels[matches[0]] = entry
        else:
            df.levels.append(entry)
        outcome.update(path=glob, level=level, lowered=lowered, default=False)

    try:
        levels.modify(ws, change)
    except levels.DataFileError as exc:
        _fail(REFUSED, f"declare: refused: {exc}")
    events.emit(ws, "data.declared", actor, path=outcome["path"], level=outcome["level"],
                lowered=outcome["lowered"], default=outcome["default"])

    suggestion = _suggestion(project.root, _policy_or_none(project.root))
    text = (f"declared {outcome['path'] or 'default_level'} {outcome['level']} "
            f"(by {actor}{', lowered' if outcome['lowered'] else ''})")
    lines = [text] + ([suggestion] if suggestion else [])
    _emit(as_json, {"slug": slug, **outcome, "actor": actor, "suggestion": suggestion},
          lines)


@data.command("levels")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True)
def levels_cmd(slug, as_json):
    """Show a run's level, its declarations, policy and each agent's boundary."""
    project = _project()
    ws = _run(project, slug)
    pol = _policy_or_fail(project.root)
    info = levels.level_info(ws)
    got = levels.read(ws)
    live = isinstance(got, levels.DataFile)
    declarations = got.levels if live else []
    boundaries = _boundaries(project, ws, pol)
    suggestion = _suggestion(project.root, pol)
    counts = {"pending_releases": len(got.pending_releases) if live else 0,
              "released": len(got.released) if live else 0,
              "imports": len(got.imports) if live else 0}
    payload = {"slug": slug, "run_level": info.level, "reason": info.reason,
               "default_level": got.default_level if live else None,
               "declarations": declarations, "enforcement": pol.enforcement,
               "policy": policy.POLICY_FILE, "policy_present": pol.present,
               "boundaries": boundaries, "suggestion": suggestion, **counts}
    lines = [f"run {slug}: level {info.level}" + (f" ({info.reason})" if info.reason else ""),
             f"default level: {payload['default_level']}",
             f"enforcement: {pol.enforcement}"
             + ("" if pol.present else f" ({policy.POLICY_FILE} absent: defaults)")]
    for d in declarations:
        lines.append(f"  {d['path']}  {d['level']}  {d.get('kind', 'other')}  "
                     f"by {d['declared_by']}  {d['reason']}")
    for name, b in boundaries.items():
        lines.append(f"  agent {name}: {b['kind']} ({b['reason']})")
    lines.append(f"pending releases {counts['pending_releases']}, released "
                 f"{counts['released']}, imports {counts['imports']}")
    if suggestion:
        lines.append(suggestion)
    _emit(as_json, payload, lines)


@data.command("pin")
@click.argument("agent")
@click.option("--json", "as_json", is_flag=True)
def pin_cmd(agent, as_json):
    """Print the sha256 that makes AGENT safe. Writes nothing."""
    project = _project()
    agents = config.load_agents(project.root)
    if agent not in agents:
        _fail(USAGE, f"unknown agent {agent!r}")
    sha = policy.pin_sha(agents[agent])
    _emit(as_json, {"agent": agent, "templates_sha256": sha},
          [f"templates_sha256 for {agent}: {sha}",
           f"paste under safe_agents.{agent} in {policy.POLICY_FILE}:",
           f'    templates_sha256: "{sha}"'])


@data.command("scan")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True)
def scan_cmd(slug, as_json):
    """Scan a run for undeclared data that looks like patient data. Warnings only; no value
    is ever printed or recorded. Allowed inside a dispatch."""
    project = _project()
    ws = _run(project, slug)
    pol = _policy_or_fail(project.root)
    found = scan.scan(ws, pol)
    report_findings(ws, found.findings, enforcement="warn")
    rows = [{"path": f.path, "rule": f.rule, "detail": f.detail} for f in found.findings]
    payload = {"slug": slug, "files_seen": found.files_seen, "truncated": found.truncated,
               "findings": rows}
    lines = [f"scanned {found.files_seen} file(s) in run {slug}"
             + (" (stopped at a scan limit)" if found.truncated else "")]
    lines += [f"  {row['path']}  {row['rule']}  {row['detail']}" for row in rows]
    if not rows:
        lines.append("no findings")
    _emit(as_json, payload, lines)

