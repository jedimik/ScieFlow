"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
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


@router.post("/runs/{slug}/gates/{gate_id}", dependencies=MUTATE)
async def answer_gate(request: Request, slug: str, gate_id: str,
                      answer: str = Form(...), note: str = Form("")):
    try:
        service.answer_gate(_project(request), slug, gate_id, answer, note=note)
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
            raise HTTPException(status_code=400, detail=f"unknown action {action!r}")
    except service.ServiceError as exc:
        return _back(slug, str(exc))
    return _back(slug)


@router.post("/runs/{slug}/jobs/{job_id}/cancel", dependencies=MUTATE)
async def cancel_job(request: Request, slug: str, job_id: str):
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
