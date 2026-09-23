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
from scieflow.core.project import Project
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


async def _log(request: Request, project: Project, job_id: str, path: Path):
    """One frame per completed line, holding back a trailing partial line.

    A subprocess's write and our poll are not aligned, so a read can land
    mid-line (`"hello wor"`, with `"ld\\n"` arriving on the next poll).
    `splitlines()` on each chunk in isolation would treat that partial tail
    as its own complete line and tear it into two frames. So the tail after
    the last newline in what we've read is held in `pending` across polls
    and only emitted once a newline completes it -- except when the job has
    reached a final state (`jobs.FINAL`), in which case no more output is
    ever coming and the held-back remainder (which may never gain a
    trailing newline, e.g. the process's very last write) is flushed as-is.
    """
    offset = 0
    idle = 0
    pending = ""
    while True:
        if await request.is_disconnected():
            return
        chunk = ""
        if path.exists():
            with path.open("r", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        pending += chunk
        lines = pending.split("\n")
        pending = lines.pop()      # remainder after the last newline seen so far
        for line in lines:
            yield f"data: {line}\n\n"
        if pending:
            job = jobs.find(project, job_id)
            if job is None or job.state in jobs.FINAL:
                yield f"data: {pending}\n\n"
                pending = ""
        idle = 0 if (chunk or lines) else idle + 1
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
    project = request.app.state.project
    job = jobs.find(project, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return StreamingResponse(_log(request, project, job_id, Path(job.log)),
                             media_type="text/event-stream", headers=SSE_HEADERS)
