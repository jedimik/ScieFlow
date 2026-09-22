"""/api/v1 — the service layer as JSON.

Routes translate HTTP into a service call and back; they hold no logic of
their own, so the browser, the CLI and other apps cannot see different
answers. FastAPI generates the OpenAPI schema at /api/v1/openapi.json, which
is the integration surface other tools use.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request

from scieflow.core import jobs, service
from scieflow.web import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_session)])


def _project(request: Request):
    return request.app.state.project


@router.get("/runs", tags=["runs"])
async def list_runs(request: Request) -> list[dict]:
    """Every run: slug, kind, phase, state, last activity."""
    return service.list_runs(_project(request))


@router.get("/runs/{slug}", tags=["runs"])
async def run_detail(request: Request, slug: str) -> dict:
    """Status, budget, remaining fractions, open gates, recent jobs and events."""
    return service.run_detail(_project(request), slug)


@router.get("/runs/{slug}/events", tags=["runs"])
async def run_events(request: Request, slug: str,
                     since: str | None = Query(default=None,
                                               description="Only events after this id."),
                     type: list[str] = Query(default=[],  # noqa: A002 - the query name
                                             description="Type filter; 'job.*' matches a prefix.")
                     ) -> list[dict]:
    """The run's history, oldest first."""
    return service.run_events(_project(request), slug, since=since, types=tuple(type))


@router.get("/runs/{slug}/jobs", tags=["runs"])
async def run_jobs(request: Request, slug: str) -> list[dict]:
    """Every job this run started, newest last."""
    project = _project(request)
    ws = service.run_workspace(project, slug)  # raises ServiceError -> 404
    # duration_s is a computed property, not a dataclass field, so plain
    # asdict() drops it — add it back explicitly.
    return [{**asdict(job), "duration_s": job.duration_s}
            for job in jobs.list_jobs(project, ws)]


@router.get("/gates", tags=["gates"])
async def open_gates(request: Request,
                     slug: str | None = Query(default=None,
                                              description="Limit to one run.")
                     ) -> list[dict]:
    """Gates still waiting for an answer, across every run."""
    return service.open_gates(_project(request), slug)


@router.get("/agents", tags=["agents"])
async def agent_settings(request: Request,
                         slug: str | None = Query(default=None,
                                                  description="Resolve for this run.")
                         ) -> dict:
    """Effective role assignments and agent settings, with their sources."""
    return service.agent_settings(_project(request), slug)
