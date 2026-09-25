"""/api/v1 — the service layer as JSON.

Routes translate HTTP into a service call and back; they hold no logic of
their own, so the browser, the CLI and other apps cannot see different
answers. FastAPI generates the OpenAPI schema at /api/v1/openapi.json, which
is the integration surface other tools use.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request

from scieflow.core import jobs, service
from scieflow.web import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_session)])

MUTATE = [Depends(auth.csrf_protect)]


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
    return [service.job_json(job) for job in jobs.list_jobs(project, ws)]


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
    project = _project(request)
    if slug is not None:
        service.run_workspace(project, slug)  # raises ServiceError -> 404
    return service.agent_settings(project, slug)


@router.post("/runs/{slug}/phase", dependencies=MUTATE, tags=["runs"])
async def set_phase(request: Request, slug: str,
                    phase: str = Form(...), state: str = Form(...)) -> dict:
    """Set a phase's state (pending/running/done/failed)."""
    return service.mark_phase(_project(request), slug, phase, state)


@router.post("/runs/{slug}/advance", dependencies=MUTATE, tags=["runs"])
async def advance(request: Request, slug: str) -> dict:
    """Start the next iteration; refused when the iteration budget is spent."""
    return service.advance_run(_project(request), slug)


@router.post("/runs/{slug}/checkpoint", dependencies=MUTATE, tags=["runs"])
async def checkpoint(request: Request, slug: str, reason: str = Form(...),
                     detail: str = Form("")) -> dict:
    """Stop the run gracefully with resume instructions."""
    return service.checkpoint_run(_project(request), slug, reason, detail)


@router.post("/runs/{slug}/resume", dependencies=MUTATE, tags=["runs"])
async def resume(request: Request, slug: str) -> dict:
    """Clear a stop so the run can continue."""
    return service.resume_run(_project(request), slug)


@router.post("/runs/{slug}/spend", dependencies=MUTATE, tags=["runs"])
async def spend(request: Request, slug: str,
                experiment_runs: int = Form(0), wall_minutes: float = Form(0.0),
                iterations: int = Form(0)) -> dict:
    """Record spend the runner cannot measure."""
    recorded = {k: v for k, v in (("experiment_runs", experiment_runs),
                                  ("wall_minutes", wall_minutes),
                                  ("iterations", iterations)) if v}
    return service.record_spend(_project(request), slug, **recorded)


@router.get("/runs/{slug}/charter", tags=["runs"])
async def run_charter(request: Request, slug: str) -> dict:
    """The run's agreed plan, with its version history."""
    return service.run_charter(_project(request), slug)


@router.post("/runs/{slug}/charter", dependencies=MUTATE, tags=["runs"])
async def set_charter(request: Request, slug: str,
                      text: str = Form(...), note: str = Form("")) -> dict:
    """Replace the charter, keeping the previous version in the history."""
    return service.set_charter(_project(request), slug, text, note=note)


@router.post("/runs/{slug}/charter/revert", dependencies=MUTATE, tags=["runs"])
async def revert_charter(request: Request, slug: str,
                         version: int = Form(...)) -> dict:
    """Make an earlier version current again, by appending a copy of it."""
    return service.revert_charter(_project(request), slug, version)


@router.post("/runs/{slug}/gates/{gate_id}/answer", dependencies=MUTATE, tags=["gates"])
async def answer(request: Request, slug: str, gate_id: str,
                 answer: str = Form(...), note: str = Form(""),
                 proposal_digest: str = Form("")) -> dict:
    """Answer an open gate as the human. `proposal_digest`, if given, must
    match the proposal file's current sha256 or the answer is refused —
    it is how a caller proves it is adopting what it actually read."""
    return service.answer_gate(_project(request), slug, gate_id, answer, note=note,
                               proposal_digest=proposal_digest or None)


@router.post("/jobs/{job_id}/cancel", dependencies=MUTATE, tags=["jobs"])
async def cancel(request: Request, job_id: str) -> dict:
    """Cancel a running job and its whole process group."""
    return service.cancel_job(_project(request), job_id)


@router.get("/runs/{slug}/conversation", tags=["runs"])
async def conversation(request: Request, slug: str) -> dict:
    """The run's conversation: agent, session state and every turn."""
    return service.conversation_state(_project(request), slug)


@router.post("/runs/{slug}/conversation", dependencies=MUTATE, tags=["runs"])
async def say(request: Request, slug: str, message: str = Form(...)) -> dict:
    """Send one message; the reply is a sandboxed job that resumes the session."""
    return service.say(_project(request), slug, message)


@router.post("/runs/{slug}/conversation/agent", dependencies=MUTATE, tags=["runs"])
async def set_conversation_agent(request: Request, slug: str,
                                 agent: str = Form(...)) -> dict:
    """Hand the conversation to a different agent; the next turn starts fresh."""
    return service.set_conversation_agent(_project(request), slug, agent)
