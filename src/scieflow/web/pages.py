"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from scieflow.core import jobs as jobs_mod
from scieflow.core import service
from scieflow.web import auth
from scieflow.web.app import TEMPLATES

router = APIRouter(dependencies=[Depends(auth.require_session)])


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
    })


@router.get("/runs/{slug}", response_class=HTMLResponse)
async def run_page(request: Request, slug: str) -> HTMLResponse:
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
    })


@router.get("/runs/{slug}/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, slug: str, job_id: str) -> HTMLResponse:
    project = _project(request)
    ws = service.run_workspace(project, slug)           # validates the slug
    job = jobs_mod.find(project, job_id)
    if job is None or job.run_dir is None or Path(job.run_dir).resolve() != ws.resolve():
        raise service.ServiceError(f"no job {job_id} in {slug}")

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
