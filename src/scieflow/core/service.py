"""The service layer: every ScieFlow action exists once, here.

The CLI, the TUI menu, the agent skill JSON and (M2) the HTTP API are thin
callers. Everything returns plain JSON-ready data and raises `ServiceError`
for anything a caller should show the user.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path

from scieflow.core import agent_config, events, gates, jobs, sandbox, workspace
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import budget, status

RECENT_JOBS = 20
RECENT_EVENTS = 50


class ServiceError(Exception):
    """A request the service cannot fulfil, with a message for the user."""


def _ws(project: Project, slug: str) -> Path:
    try:
        ws = project.run_dir(slug)
    except ProjectError as e:
        raise ServiceError(str(e)) from e
    if not ws.is_dir():
        raise ServiceError(f"no run workspace/{slug}")
    return ws


def run_workspace(project: Project, slug: str) -> Path:
    """The run's directory, or ServiceError — the public form of `_ws`."""
    return _ws(project, slug)


def job_json(job: jobs.Job) -> dict:
    """`asdict(job)` plus `duration_s`, a property `asdict` drops (not a field)."""
    return {**asdict(job), "duration_s": job.duration_s}


def list_runs(project: Project) -> list[dict]:
    return [asdict(r) for r in workspace.list_runs(project.root)]


def run_detail(project: Project, slug: str) -> dict:
    ws = _ws(project, slug)
    st = status.read_status(ws) if (ws / "status.yml").exists() else None
    b = budget.read_budget(ws) if (ws / "budget.yml").exists() else None
    return {
        "run": asdict(workspace.describe(ws)),
        "status": st,
        "budget": b,
        "remaining": budget.remaining_fraction(b) if b else None,
        "gates": gates.list_gates(ws, "open"),
        "jobs": [job_json(j) for j in jobs.list_jobs(project, ws)][-RECENT_JOBS:][::-1],
        "events": events.read(ws)[-RECENT_EVENTS:],
    }


def run_events(project: Project, slug: str, since: str | None = None,
               types: tuple[str, ...] = ()) -> list[dict]:
    return events.read(_ws(project, slug), since=since, types=types)


def dispatch_agent(project: Project, agent: str, prompt_file: Path, transcript: Path, *,
                   cwd: Path | None = None, role: str | None = None,
                   detach: bool = False) -> dict:
    from scieflow.core.agent_run import DispatchError, prepare
    from scieflow.core.run import actions

    try:
        d = prepare(project, agent, Path(prompt_file), cwd, role)
    except (DispatchError, sandbox.SandboxError) as e:
        raise ServiceError(str(e)) from e
    if d.run_dir is not None:
        try:
            actions.guard_budget(d.run_dir, ("wall_minutes",))
        except actions.BudgetExhausted as e:
            raise ServiceError(f"{e} — run checkpointed") from e
    if d.writable is None:
        if d.run_dir is not None:
            events.emit(d.run_dir, "sandbox.disabled", "human", agent=d.agent,
                        why="config.yml sandbox: off")
    else:
        try:
            sandbox.verify(d.writable, d.cwd)
        except sandbox.SandboxError as exc:
            if d.run_dir is not None:
                events.emit(d.run_dir, "job.refused", "system", reason="sandbox",
                            detail=str(exc))
            raise ServiceError(str(exc)) from exc
    job, proc = jobs.start(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                           label=agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text,
                           sandbox_writable=d.writable)

    def finish() -> jobs.Job:
        done = jobs.wait(job, proc)
        if d.run_dir is not None and done.duration_s is not None:
            actions.record_spend(d.run_dir, wall_minutes=round(done.duration_s / 60, 3))
        out = Path(done.log).read_text()
        note = f"\n{agent}: timed out after {d.timeout_s:.0f}s\n" if done.state == "timeout" else ""
        Path(transcript).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript).write_text(out + note)
        return done

    if detach:
        threading.Thread(target=finish, daemon=True).start()
        return asdict(job)
    return asdict(finish())


def cancel_job(project: Project, job_id: str) -> dict:
    job = jobs.find(project, job_id)
    if job is None:
        raise ServiceError(f"no job {job_id}")
    return asdict(jobs.cancel(job))


def open_gates(project: Project, slug: str | None = None) -> list[dict]:
    slugs = [slug] if slug else [r["slug"] for r in list_runs(project)]
    out = []
    for name in slugs:
        for g in gates.list_gates(_ws(project, name), "open"):
            out.append({**g, "slug": name})
    return out


def answer_gate(project: Project, slug: str, gate_id: str, answer: str,
                actor: str = "human", rationale: str = "", note: str = "") -> dict:
    try:
        return gates.answer(project, _ws(project, slug), gate_id, answer, actor, rationale, note)
    except gates.GateError as e:
        raise ServiceError(str(e)) from e


def agent_settings(project: Project, slug: str | None = None) -> dict:
    return agent_config.resolve(project.root, slug).to_json()
