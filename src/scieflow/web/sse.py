"""Server-sent events: the timeline and job output as they happen.

Polling a file is the right tool here — the writers are separate processes
(agents, sweeps, the CLI), so there is nothing in-process to subscribe to,
and `events.jsonl` and a job log are append-only. A client that goes away is
noticed through `request.is_disconnected()`, so a closed tab stops the loop.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from pathlib import Path

import anyio

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


def _wire_frame(text):
    """Cap actual encoded framing AFTER sanitation, never cut a scalar."""
    limit = 16384 - len(b'data: \n\n')
    raw = text.encode('utf-8')
    if len(raw) > limit:
        notice = '[Output truncated: wire-limit; raw artifact retained]'
        available = limit - len(notice.encode('utf-8'))
        text = raw[:available].decode('utf-8', 'ignore') + notice
    return ('data: ' + text + '\n\n').encode('utf-8')


class _LogRecords:
    """Raw partial <=16KiB; shared sanitizer also sees discarded terminators."""
    def __init__(self):
        from scieflow.core.turn_output import DisplaySanitizer

        self.pending = b''
        self.discard = False
        self.sanitizer = DisplaySanitizer()

    def feed(self, chunk):
        start = 0
        while start < len(chunk):
            end = chunk.find(b'\n', start)
            complete = end >= 0
            end = end if complete else len(chunk)
            part = chunk[start:end]
            if self.discard:
                self.sanitizer.feed(part)
            elif len(self.pending) + len(part) > 16384:
                self.sanitizer.feed(self.pending)
                self.sanitizer.feed(part)
                self.pending, self.discard = b'', True
            else:
                self.pending += part
            if complete:
                if self.discard:
                    self.sanitizer.feed(b'\n')
                    yield _wire_frame('[Output omitted: raw-record-limit; raw artifact retained]')
                else:
                    text = self.sanitizer.feed(self.pending + b'\n')
                    # LF in an OSC payload is consumed, not finalization.
                    if text.endswith('\n'):
                        text = text[:-1]
                    if text or not self.pending:
                        yield _wire_frame(text)
                self.pending, self.discard = b'', False
            start = end + (1 if complete else 0)

    def finish(self):
        if self.discard:
            self.sanitizer.feed(b'', final=True)
            yield _wire_frame('[Output omitted: raw-record-limit; raw artifact retained]')
        else:
            text = self.sanitizer.feed(self.pending, final=True)
            if text:
                yield _wire_frame(text)
        self.pending = b''


async def _log(request: Request, location, source, owner):
    offset, idle, records = 0, 0, _LogRecords()
    try:
        while True:
            if await request.is_disconnected():
                return
            chunk = await anyio.to_thread.run_sync(source.read_available, offset, 65536)
            offset += len(chunk)
            for frame in records.feed(chunk):
                if await request.is_disconnected():
                    return
                yield frame
                await asyncio.sleep(0)  # cooperative under sustained output
            extent = await anyio.to_thread.run_sync(source.extent)
            if offset >= extent:
                state = await anyio.to_thread.run_sync(location.peek_state)
                # Reobserve after the selected record; final state alone is
                # not EOF while already-observed log bytes remain unread.
                if state in jobs.FINAL and offset >= await anyio.to_thread.run_sync(source.extent):
                    for frame in records.finish():
                        yield frame
                    return
            idle = 0 if chunk else idle + 1
            if idle and idle % HEARTBEAT_EVERY == 0:
                yield b': ping\n\n'
            await asyncio.sleep(0 if offset < extent else POLL_S)
    except (jobs.JobArtifactError, ValueError, OSError):
        yield _wire_frame('[Display stopped: source invalidated; raw artifact retained]')
    finally:
        owner.close()


class _OwnedLogResponse(StreamingResponse):
    """Close even if cancellation precedes the generator's first iteration."""
    def __init__(self, content, owner):
        self.owner = owner
        super().__init__(content, media_type='text/event-stream', headers=SSE_HEADERS)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.owner.close()


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
    owner = ExitStack()
    def acquire():
        location = owner.enter_context(jobs.locate_artifacts(request.app.state.project, job_id))
        source = owner.enter_context(location.open_live_log())
        source.extent()
        return location, source
    try:
        location, source = await anyio.to_thread.run_sync(acquire)
        return _OwnedLogResponse(_log(request, location, source, owner), owner)
    except (jobs.JobArtifactError, ValueError, OSError) as exc:
        owner.close()
        raise HTTPException(status_code=404, detail='job log unavailable') from exc
    except BaseException:
        owner.close()
        raise
