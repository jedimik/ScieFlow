"""Gates: every approval a protocol requires, stored as data.

A gate is `workspace/<slug>/gates/<id>.json`. Agents open it and wait; the human
answers from the terminal or the browser. In an autonomous run the coordinator
may answer a gate itself — only a kind that does not require a human, only one
it opened as in-scope, and only with a rationale — and that answer is logged
as the agent's, never passed off as the user's.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from scieflow.core import events, store
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import status as status_mod

GATES_DIR = "gates"


class GateError(ValueError):
    """A gate operation the rules do not allow."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kinds(project: Project) -> dict:
    return project.schema("gates")["kinds"]


def _path(ws: Path, gate_id: str) -> Path:
    return Path(ws) / GATES_DIR / f"{gate_id}.json"


def _save(ws: Path, gate: dict) -> None:
    store.write_text(_path(ws, gate["id"]), json.dumps(gate, indent=2))


def get(ws: Path, gate_id: str) -> dict:
    path = _path(ws, gate_id)
    if not path.exists():
        raise GateError(f"no gate {gate_id} in {Path(ws).name}")
    return json.loads(path.read_text())


def list_gates(ws: Path, state: str | None = None) -> list[dict]:
    directory = Path(ws) / GATES_DIR
    found = [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))] \
        if directory.is_dir() else []
    return [g for g in found if state is None or g["state"] == state]


def open_gate(project: Project, ws: Path, kind: str, question: str, options=(),
              files=(), in_scope: bool = False, actor: str = "agent") -> dict:
    table = kinds(project)
    if kind not in table:
        raise GateError(f"unknown gate kind {kind!r} (known: {', '.join(table)})")
    gate = {
        "id": store.new_id(), "kind": kind, "question": question,
        "options": list(options), "files": [str(f) for f in files],
        "in_scope": bool(in_scope), "requires_human": bool(table[kind]["requires_human"]),
        "state": "open", "opened": _now(), "opened_by": actor,
        "answer": None, "answered": None, "answered_by": None, "rationale": "", "note": "",
    }
    _save(ws, gate)
    events.emit(ws, "gate.opened", actor, gate=gate["id"], kind=kind, question=question)
    return gate


def _check_agent_may_answer(ws: Path, gate: dict, rationale: str) -> None:
    if gate["requires_human"]:
        raise GateError(f"{gate['kind']} gates need a human answer, in every mode")
    approval = (status_mod.read_status(ws) or {}).get("approval")
    if approval != "autonomous":
        raise GateError("agents answer gates only in autonomous runs")
    if not gate["in_scope"]:
        raise GateError("only gates opened as in-scope can be answered by the agent")
    if not rationale.strip():
        raise GateError("an agent's answer needs a rationale")


def answer(project: Project, ws: Path, gate_id: str, answer: str, actor: str = "human",
           rationale: str = "", note: str = "") -> dict:
    with store.locked(_path(ws, gate_id)):
        gate = get(ws, gate_id)
        if gate["state"] != "open":
            raise GateError(f"gate {gate_id} is not open ({gate['state']})")
        if gate["options"] and answer not in gate["options"]:
            raise GateError(f"answer must be one of: {', '.join(gate['options'])}")
        if actor == "agent":
            _check_agent_may_answer(ws, gate, rationale)
        gate.update(state="answered", answer=answer, answered=_now(), answered_by=actor,
                    rationale=rationale, note=note)
        path = _path(ws, gate_id)
        tmp = path.with_suffix(".json.new")
        tmp.write_text(json.dumps(gate, indent=2))
        tmp.replace(path)
    events.emit(ws, "gate.answered", actor, gate=gate_id, kind=gate["kind"], answer=answer,
                rationale=rationale)
    return gate


def wait(ws: Path, gate_id: str, timeout: float | None = None, poll: float = 2.0) -> dict:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        gate = get(ws, gate_id)
        if gate["state"] != "open":
            return gate
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"gate {gate_id} still open")
        time.sleep(poll)


# -- CLI ------------------------------------------------------------------
def _ws(slug: str) -> tuple[Project, Path]:
    project = Project.discover()
    try:
        ws = project.run_dir(slug)
    except ProjectError as e:
        raise click.ClickException(str(e)) from e
    if not ws.is_dir():
        raise click.ClickException(f"no run workspace/{slug}")
    return project, ws


@click.group()
def gate():
    """Approvals the protocols require, answered from terminal or browser."""


@gate.command("open")
@click.argument("slug")
@click.option("--kind", required=True)
@click.option("--question", required=True)
@click.option("--option", "options", multiple=True)
@click.option("--file", "files", multiple=True, type=click.Path(path_type=Path))
@click.option("--in-scope", is_flag=True, help="Inside the approved goal, scope and budget.")
@click.option("--json", "as_json", is_flag=True)
def open_cmd(slug, kind, question, options, files, in_scope, as_json):
    """Open a gate (agents call this, then `gate wait`)."""
    project, ws = _ws(slug)
    try:
        g = open_gate(project, ws, kind, question, options, files, in_scope, actor="agent")
    except GateError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(g, indent=2) if as_json else g["id"])


@gate.command("list")
@click.argument("slug")
@click.option("--open", "only_open", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def list_cmd(slug, only_open, as_json):
    """Gates of a run."""
    _, ws = _ws(slug)
    found = list_gates(ws, "open" if only_open else None)
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    for g in found:
        flag = " [human]" if g["requires_human"] else ""
        click.echo(f"{g['id']}  {g['state']:<9} {g['kind']:<20}{flag}  {g['question']}")


@gate.command("show")
@click.argument("slug")
@click.argument("gate_id")
def show_cmd(slug, gate_id):
    """One gate, in full."""
    _, ws = _ws(slug)
    try:
        click.echo(json.dumps(get(ws, gate_id), indent=2))
    except GateError as e:
        raise click.ClickException(str(e)) from e


@gate.command("answer")
@click.argument("slug")
@click.argument("gate_id")
@click.argument("answer_text")
@click.option("--note", default="")
@click.option("--as-agent", is_flag=True, help="Answer as the coordinator (autonomous runs only).")
@click.option("--rationale", default="")
def answer_cmd(slug, gate_id, answer_text, note, as_agent, rationale):
    """Answer an open gate."""
    project, ws = _ws(slug)
    try:
        g = answer(project, ws, gate_id, answer_text, "agent" if as_agent else "human",
                   rationale, note)
    except GateError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{g['id']}: {g['answer']}")


@gate.command("wait")
@click.argument("slug")
@click.argument("gate_id")
@click.option("--timeout", type=float, default=None, help="Seconds; default waits forever.")
def wait_cmd(slug, gate_id, timeout):
    """Block until the gate is answered; print it as JSON (exit 2 on timeout)."""
    _, ws = _ws(slug)
    try:
        click.echo(json.dumps(wait(ws, gate_id, timeout), indent=2))
    except TimeoutError as e:
        click.echo(str(e), err=True)
        raise SystemExit(2) from e
