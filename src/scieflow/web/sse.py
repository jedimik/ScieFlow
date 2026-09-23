"""Server-sent events: the timeline and job output as they happen.

Polling a file is the right tool here — the writers are separate processes
(agents, sweeps, the CLI), so there is nothing in-process to subscribe to,
and `events.jsonl` and a job log are append-only. A client that goes away is
noticed through `request.is_disconnected()`, so a closed tab stops the loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from scieflow.core import jobs, service
from scieflow.web import auth

router = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_session)])

POLL_S = 0.5
HEARTBEAT_EVERY = 20        # polls between `: ping` comments (~10s)
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def _events(request: Request, slug: str, since: str | None):
    project = request.app.state.project
    idle = 0
    while True:
        if await request.is_disconnected():
            return
        batch = service.run_events(project, slug, since=since)
        for event in batch:
            since = event["id"]
            yield f"event: run\ndata: {json.dumps(event)}\n\n"
        idle = 0 if batch else idle + 1
        if idle and idle % HEARTBEAT_EVERY == 0:
            yield ": ping\n\n"
        await asyncio.sleep(POLL_S)


async def _log(request: Request, path: Path):
    offset = 0
    idle = 0
    while True:
        if await request.is_disconnected():
            return
        chunk = ""
        if path.exists():
            with path.open("r", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        for line in chunk.splitlines():
            yield f"data: {line}\n\n"
        idle = 0 if chunk else idle + 1
        if idle and idle % HEARTBEAT_EVERY == 0:
            yield ": ping\n\n"
        await asyncio.sleep(POLL_S)


@router.get("/runs/{slug}/events/stream", tags=["runs"])
async def event_stream(request: Request, slug: str,
                       since: str | None = Query(default=None,
                                                 description="Resume after this event id.")
                       ) -> StreamingResponse:
    """The run's timeline, replayed then followed, as `event: run` frames."""
    service.run_workspace(request.app.state.project, slug)      # 404 for a bad slug
    return StreamingResponse(_events(request, slug, since),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/jobs/{job_id}/log/stream", tags=["jobs"])
async def log_stream(request: Request, job_id: str) -> StreamingResponse:
    """One frame per line of a job's stdout, as it is written."""
    job = jobs.find(request.app.state.project, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return StreamingResponse(_log(request, Path(job.log)),
                             media_type="text/event-stream", headers=SSE_HEADERS)
