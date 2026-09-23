"""HTML pages — server-rendered, no build step, no CDN.

Every page reads through the service layer and renders a Jinja template.
Live regions (job output, the timeline) are the browser's own EventSource
against the SSE routes; nothing here needs JavaScript to be useful.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

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
