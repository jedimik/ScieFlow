"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scieflow.core import jobs as jobs_mod
from scieflow.core import service
from scieflow.web import auth
from scieflow.web.app import TEMPLATES

router = APIRouter(dependencies=[Depends(auth.require_session)])

MUTATE = [Depends(auth.csrf_protect)]


def _project(request: Request):
    return request.app.state.project


def _percent(fraction: float | None) -> int:
    """A remaining fraction as a whole percent, clamped for the CSS bar."""
    if fraction is None:
        return 0
    return max(0, min(100, round(fraction * 100)))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    project = _project(request)
    runs = service.list_runs(project)
    detail = {}
    for run in runs:
        try:
            full = service.run_detail(project, run["slug"])
        except service.ServiceError:
            continue
        detail[run["slug"]] = {
            "remaining": {dim: _percent(value)
                          for dim, value in (full["remaining"] or {}).items()},
            "stopped": (full["status"] or {}).get("stopped"),
        }
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {
        "runs": runs,
        "detail": detail,
        "gates": service.open_gates(project),
        "csrf": auth.csrf_token(request),
    })


def _start_form(defaults: dict, submitted: dict | None = None) -> dict:
    """The values the start form should show: whatever was just submitted
    and refused, falling back field-by-field to the project's defaults —
    which is also what an empty `submitted` (the first visit) resolves to."""
    submitted = submitted or {}
    return {
        "slug": submitted.get("slug", ""),
        "goal": submitted.get("goal", ""),
        "workflow": submitted.get("workflow", ""),
        "agent": submitted.get("agent", ""),
        "approval": submitted.get("approval") or defaults.get("approval", ""),
        "max_iterations": (submitted.get("max_iterations")
                           or defaults.get("max_iterations", "")),
        "max_experiment_runs": (submitted.get("max_experiment_runs")
                                or defaults.get("max_experiment_runs", "")),
        "max_wall_minutes": (submitted.get("max_wall_minutes")
                             or defaults.get("max_wall_minutes", "")),
    }


def _render_start(request: Request, project, *, error: str = "",
                  submitted: dict | None = None) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "start.html", {
        "workflows": service.workflows(),
        "agents": service.conversational_agents(project),
        "form": _start_form(project.defaults(), submitted),
        "error": error,
        "csrf": auth.csrf_token(request),
    })


@router.get("/start", response_class=HTMLResponse)
def start_page(request: Request) -> HTMLResponse:
    """Plain `def`, not `async def`: `service.workflows()`,
    `service.conversational_agents` and `project.defaults()` all read a YAML
    file straight off disk on the event loop — smaller than the blocking
    calls earlier milestones had to move off it, but the same shape, so it
    goes in the threadpool like the rest of this page's handlers.

    No `error` query parameter: a refusal is now rendered directly by
    `start_run` (see its docstring), not redirected here, so there is
    nothing left that would ever set one."""
    return _render_start(request, _project(request))


@router.post("/start", dependencies=MUTATE)
def start_run(request: Request, slug: str = Form(...), goal: str = Form(...),
              workflow: str = Form(""), agent: str = Form(""), approval: str = Form(""),
              max_iterations: int = Form(0), max_experiment_runs: int = Form(0),
              max_wall_minutes: int = Form(0)):
    """Plain `def`, not `async def` — see `say` above: creating a run writes
    several files under a lock, and that blocking work belongs in Starlette's
    threadpool, not on the event loop.

    A refusal re-renders the form in place rather than redirecting to
    `/start?error=...`: the goal is free text that can run to paragraphs,
    and round-tripping that (plus the slug, workflow, approval and three
    budget numbers) through a query string risks a URL past what a server
    or browser will accept, and leaves it sitting in the URL bar and any
    access log besides. Rendering directly costs this one path the
    post/redirect/get guarantee every other mutation here keeps — reloading
    a refused submission re-POSTs it, and the browser will ask first. A
    successful submission still redirects, so that guarantee holds for the
    common case.

    Calls `service.start_run` instead of `service.create_run` so a chosen
    agent takes the first turn as part of this same request — the agent is
    checked before anything is created, so a refusal here behaves exactly
    like the existing refusals: nothing written, form re-rendered with what
    was typed.
    """
    project = _project(request)
    submitted = {"slug": slug, "goal": goal, "workflow": workflow, "agent": agent,
                "approval": approval,
                "max_iterations": max_iterations or None,
                "max_experiment_runs": max_experiment_runs or None,
                "max_wall_minutes": max_wall_minutes or None}
    try:
        service.start_run(
            project, slug, goal, agent, workflow=workflow,
            approval=approval or None,
            max_iterations=max_iterations or None,
            max_experiment_runs=max_experiment_runs or None,
            max_wall_minutes=max_wall_minutes or None)
    except service.ServiceError as exc:
        return _render_start(request, project, error=str(exc), submitted=submitted)
    return RedirectResponse(f"/runs/{quote(slug)}", status_code=303)


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, slug: str = "", error: str = "",
                      assign: list[str] = Query(default=[])) -> HTMLResponse:
    from scieflow.core import agent_config

    project = _project(request)
    preview, problem = None, error
    if assign:
        try:
            preview = service.plan_staffing(project, assign, slug or None)
        except service.ServiceError as exc:
            problem = str(exc)
    return TEMPLATES.TemplateResponse(request, "agents.html", {
        "slug": slug,
        "settings": service.agent_settings(project, slug or None),
        "roles": list(agent_config.ROLES),
        "runs": service.list_runs(project),
        "assign": assign,
        "preview": preview,
        "error": problem,
        "csrf": auth.csrf_token(request),
    })


@router.post("/agents", dependencies=MUTATE)
async def apply_agents(request: Request, slug: str = Form(""),
                       assign: list[str] = Form(default=[])):
    target = "/agents" + (f"?slug={quote(slug)}" if slug else "")
    if not assign:
        return RedirectResponse(target + ("&" if slug else "?")
                                + "error=" + quote("choose a role and an agent first"),
                                status_code=303)
    try:
        service.apply_staffing(_project(request), assign, slug or None)
    except service.ServiceError as exc:
        return RedirectResponse(target + ("&" if slug else "?")
                                + "error=" + quote(str(exc)), status_code=303)
    return RedirectResponse(target, status_code=303)


@router.get("/runs/{slug}", response_class=HTMLResponse)
async def run_page(request: Request, slug: str, error: str = "") -> HTMLResponse:
    project = _project(request)
    detail = service.run_detail(project, slug)          # ServiceError -> 404
    ws = service.run_workspace(project, slug)
    status = detail["status"] or {}
    return TEMPLATES.TemplateResponse(request, "run.html", {
        "slug": slug,
        "detail": detail,
        "phases": list((status.get("phases") or {}).items()),
        "remaining": {dim: _percent(value)
                      for dim, value in (detail["remaining"] or {}).items()},
        "jobs": [service.job_json(job) for job in reversed(jobs_mod.list_jobs(project, ws))],
        "charter": service.run_charter(project, slug),
        "conversation": service.conversation_state(project, slug),
        "agents": service.conversational_agents(project),
        "error": error,
        "csrf": auth.csrf_token(request),
    })


def _owning_job(project, slug: str, job_id: str) -> jobs_mod.Job:
    """The job, if it belongs to this run. Raises `service.ServiceError`
    (-> 404) otherwise.

    One copy, because this is what stops a request reaching a job in a
    different run: two copies is how a later fix lands on one and not the
    other. `ServiceError` — rather than an `HTTPException` here — because
    the app's exception handler already maps it to the same 404 JSON body
    for any caller, GET or POST, matching `run_workspace`'s own use of
    `ServiceError` for "no such run" a line above every call site here.
    """
    ws = service.run_workspace(project, slug)            # validates the slug
    job = jobs_mod.find(project, job_id)
    if job is None or job.run_dir is None or Path(job.run_dir).resolve() != ws.resolve():
        raise service.ServiceError(f"no job {job_id} in {slug}")
    return job


def _back(slug: str, error: str = "") -> RedirectResponse:
    """Post/redirect/get: the browser lands on a fresh read of the page, so
    reloading never repeats the action."""
    target = f"/runs/{slug}"
    if error:
        target += "?error=" + quote(error)
    return RedirectResponse(target, status_code=303)


@router.post("/runs/{slug}/charter", dependencies=MUTATE)
async def edit_charter(request: Request, slug: str, action: str = Form("set"),
                       text: str = Form(""), note: str = Form(""),
                       version: int = Form(0)):
    project = _project(request)
    try:
        if action == "revert":
            service.revert_charter(project, slug, version)
        else:
            service.set_charter(project, slug, text, note=note)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/say", dependencies=MUTATE)
def say(request: Request, slug: str, action: str = Form("say"),
        message: str = Form(""), agent: str = Form("")):
    """Plain `def`, not `async def` — see `scieflow.web.api.say` for why:
    `service.say` blocks for up to the agent's `timeout_min`, and an `async
    def` handler doing that would stall the one event loop this app runs on
    for the whole turn."""
    project = _project(request)
    try:
        if action == "agent":
            service.set_conversation_agent(project, slug, agent)
        else:
            service.say(project, slug, message)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/gates/{gate_id}", dependencies=MUTATE)
async def answer_gate(request: Request, slug: str, gate_id: str,
                      answer: str = Form(...), note: str = Form(""),
                      proposal_digest: str = Form("")):
    try:
        service.answer_gate(_project(request), slug, gate_id, answer, note=note,
                            proposal_digest=proposal_digest or None)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/act", dependencies=MUTATE)
async def act(request: Request, slug: str, action: str = Form(...),
              phase: str = Form(""), state: str = Form(""),
              reason: str = Form(""), detail: str = Form(""),
              experiment_runs: int = Form(0), wall_minutes: float = Form(0.0),
              iterations: int = Form(0)):
    project = _project(request)
    try:
        if action == "phase":
            service.mark_phase(project, slug, phase, state)
        elif action == "advance":
            service.advance_run(project, slug)
        elif action == "checkpoint":
            service.checkpoint_run(project, slug, reason or "user", detail)
        elif action == "resume":
            service.resume_run(project, slug)
        elif action == "spend":
            recorded = {k: v for k, v in (("experiment_runs", experiment_runs),
                                          ("wall_minutes", wall_minutes),
                                          ("iterations", iterations)) if v}
            service.record_spend(project, slug, **recorded)
        else:
            return _back(slug, f"unknown action {action!r}")
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/jobs/{job_id}/cancel", dependencies=MUTATE)
def cancel_job(request: Request, slug: str, job_id: str):
    """Plain `def`, not `async def` — see `say` above: `service.cancel_job`
    -> `jobs.cancel` -> `_kill_group` polls with `time.sleep(0.1)` for up to
    `KILL_GRACE` (10s) against a process that ignores `SIGTERM`, blocking
    the one event loop for the whole grace period if run here directly."""
    project = _project(request)
    _owning_job(project, slug, job_id)
    try:
        service.cancel_job(project, job_id)
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.get("/runs/{slug}/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, slug: str, job_id: str) -> HTMLResponse:
    project = _project(request)
    job = _owning_job(project, slug, job_id)

    def _read(path: str) -> str:
        try:
            return Path(path).read_text(errors="replace")[-200_000:]
        except OSError:
            return ""

    return TEMPLATES.TemplateResponse(request, "job.html", {
        "slug": slug,
        "job": service.job_json(job),
        "out": _read(job.log),
        "err": _read(job.err),
        "live": job.state == "running",
    })


@router.get("/runs/{slug}/files", response_class=HTMLResponse)
async def files_page(request: Request, slug: str, path: str = "") -> HTMLResponse:
    from fastapi import HTTPException

    from scieflow.web import files as files_mod

    ws = service.run_workspace(_project(request), slug)
    try:
        entries = files_mod.listing(ws, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TEMPLATES.TemplateResponse(request, "files.html",
                                      {"slug": slug, "path": path, "entries": entries})


@router.get("/runs/{slug}/file")
async def file_view(request: Request, slug: str, path: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from scieflow.web import files as files_mod

    ws = service.run_workspace(_project(request), slug)
    try:
        target = files_mod.resolve(ws, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such file: {path}") from exc
    if target.is_dir():
        raise HTTPException(status_code=400, detail="that is a directory")
    try:
        if files_mod.is_text(target) and target.stat().st_size <= files_mod.MAX_INLINE:
            return TEMPLATES.TemplateResponse(request, "file.html", {
                "slug": slug, "path": path,
                "text": target.read_text(errors="replace"),
            })
    except OSError as exc:
        # Gone (or unreadable) between resolve() and here — e.g. an agent
        # deleted it. Not a security escape, just no longer there.
        raise HTTPException(status_code=404, detail=f"no such file: {path}") from exc
    media_type, download = files_mod.download_type(target)
    return FileResponse(
        target,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
        # Only the inline-safe allowlist skips a forced download; everything
        # else — HTML, JS, SVG, anything unrecognised — is served as an
        # attachment so it can never execute in this app's origin.
        filename=target.name if download else None,
    )
