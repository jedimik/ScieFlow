"""Which routes may legitimately be `async def`.

`serve.py` runs one uvicorn process for the whole app. FastAPI runs a plain
`def` handler in Starlette's threadpool; an `async def` one runs directly on
that process's single event loop. A blocking call inside an `async def`
handler — a file read, `store.locked()` (`events.emit` routes through it, and
every mutating route emits at least one event onto the run's own
`events.jsonl`, the hottest file in a run), `subprocess`/job wait — freezes
every other request for as long as that call takes: `/healthz`, both SSE
streams below, and the Cancel button included. `docs/runs.md` and the
handlers themselves (`pages.say`, `pages.cancel_job`, `api.say`, `api.cancel`)
say more about why.

Two routes were fixed for exactly this reason earlier in this branch; the
rest of the app's mutating routes, and several of its GETs, had the same
defect. `tests/web/test_async_routes.py` is what keeps it from being
rediscovered a fourth time: it walks every registered route and fails if an
`async def` handler appears that isn't declared here, with a reason.

Keyed by (path, method), the path exactly as FastAPI resolves it (including
any router `prefix`) — e.g. `"/api/v1/runs/{slug}/events/stream"`, not a
concrete example the way `tests/web/mutating_paths.py`'s `PLACEHOLDERS` fills
one in, since this test never calls a route, only inspects its handler.
"""

ASYNC_ALLOWED = {
    ("/api/v1/runs/{slug}/events/stream", "GET"):
        "a Server-Sent Events stream (scieflow.web.sse.event_stream): it "
        "must `await request.is_disconnected()` and `asyncio.sleep` between "
        "polls, cooperatively, for as long as a client keeps the tab open — "
        "the opposite of the bounded, one-shot work every other handler "
        "does, and exactly the kind of waiting the event loop exists for.",
    ("/api/v1/jobs/{job_id}/log/stream", "GET"):
        "the job-log SSE stream (scieflow.web.sse.log_stream) — same reason "
        "as the run's own event stream above.",
}
