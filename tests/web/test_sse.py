"""SSE streams for the run timeline and job output.

These endpoints poll forever until the client disconnects -- there is no
natural end to the response. `fastapi.testclient.TestClient` (and httpx's
`ASGITransport` underneath it) cannot exercise that: both fully drain the
ASGI application -- i.e. wait for the whole response body, forever, for an
endpoint that never finishes on its own -- before handing back *anything*,
including the status code. That is a real, verified limitation of the
in-process ASGI transport used by `TestClient`/`ASGITransport`, not a detail
of this app: even a trivial `while True: yield ...; await sleep(...)` FastAPI
route hangs a `client.stream(...)` call from `TestClient` forever, breaking
out of the frame loop notwithstanding, because that call never returns
control until the ASGI app coroutine itself returns -- which an infinite
generator never does on its own.

So the three tests that actually consume a stream run a real uvicorn server
over a real socket (`live`, below) instead of the in-process TestClient. A
real server supports genuine incremental delivery and lets a client's
disconnect (closing the connection without finishing reading the body)
reach and cancel the generator, exactly as it would in production. The two
tests that only check a status code (auth, unknown run) never touch the
generator and keep using the ordinary `client`/`project` fixtures.
"""

import json
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from scieflow.web.app import create_app

LIVE_TOKEN = "live-test-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(project):
    """A real uvicorn server for `project`, reached over an actual socket."""
    port = _free_port()
    config = uvicorn.Config(create_app(project, LIVE_TOKEN), host="127.0.0.1",
                            port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn did not start in time"

    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as http_client:
        http_client.get(f"/healthz?token={LIVE_TOKEN}")   # exchange token for cookies
        yield http_client

    server.should_exit = True
    thread.join(timeout=5.0)


def read_frames(live, url, want, timeout=10.0):
    """Collect `want` data frames from an SSE endpoint, then disconnect."""
    frames = []
    with live.stream("GET", url, timeout=timeout) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                frames.append(line[len("data:"):].strip())
                if len(frames) >= want:
                    break
    return frames


def test_event_stream_replays_then_follows(live, project):
    """Replays the backlog, then genuinely follows: a new event, emitted
    while the connection is still open (no reconnect), must arrive on the
    same stream. Verified by breaking the follow path (making `_events`
    `return` after its first batch) and watching this fail before
    restoring it -- see task-7-report.md, "Fix round 1"."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    existing = events.read(ws)
    with live.stream("GET", "/api/v1/runs/r1/events/stream", timeout=10.0) as response:
        assert response.status_code == 200
        lines = response.iter_lines()

        # Drain exactly the replayed backlog.
        frames = []
        while len(frames) < len(existing):
            line = next(lines)
            if line.startswith("data:"):
                frames.append(line[len("data:"):].strip())
        types = [json.loads(frame)["type"] for frame in frames]
        assert types[0] == "run.created"

        # The connection is still open -- emit a new event now, without
        # reconnecting, and require it to arrive on this same stream.
        events.emit(ws, "note.hello", "human", message="from the test")
        followed = None
        for line in lines:
            if line.startswith("data:"):
                followed = json.loads(line[len("data:"):].strip())
                break
        assert followed is not None, "no frame arrived for the event emitted mid-stream"
        assert followed["type"] == "note.hello"


def test_event_stream_since_skips_what_you_have(live, project):
    from scieflow.core import events

    ws = project.run_dir("r1")
    existing = events.read(ws)
    events.emit(ws, "note.later", "human", message="new")
    frames = read_frames(
        live, f"/api/v1/runs/r1/events/stream?since={existing[-1]['id']}", want=1)
    assert json.loads(frames[0])["type"] == "note.later"


def test_job_log_stream_sends_the_output(live, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    frames = read_frames(live, f"/api/v1/jobs/{job.id}/log/stream", want=1)
    assert frames[0] == "hello from the job"


def test_job_log_stream_buffers_a_split_line(live, project):
    """A write that lands mid-line across two polls must arrive as one frame,
    not two. The subprocess writes 'hello wor', pauses longer than `POLL_S`,
    then completes the line with 'ld\\n'."""
    from scieflow.core import jobs

    ws = project.run_dir("r1")
    script = (
        "import sys, time\n"
        "sys.stdout.write('hello wor')\n"
        "sys.stdout.flush()\n"
        "time.sleep(1.0)\n"
        "sys.stdout.write('ld\\n')\n"
    )
    job, proc = jobs.start(project, [sys.executable, "-c", script], kind="agent",
                           cwd=project.root, run_dir=ws, label="split-line")
    try:
        frames = read_frames(live, f"/api/v1/jobs/{job.id}/log/stream", want=1, timeout=10.0)
    finally:
        jobs.wait(job, proc)
    assert frames == ["hello world"]


def test_job_log_stream_flushes_unterminated_final_line(live, project):
    """A job's last line, with no trailing newline, must still be flushed
    once the job has reached a final state -- it must not be held back
    forever waiting for a newline that will never come."""
    from scieflow.core import jobs

    ws = project.run_dir("r1")
    job = jobs.run_blocking(
        project, [sys.executable, "-c", "import sys; sys.stdout.write('no trailing newline')"],
        kind="agent", cwd=project.root, run_dir=ws, label="no-newline")
    frames = read_frames(live, f"/api/v1/jobs/{job.id}/log/stream", want=1, timeout=10.0)
    assert frames == ["no trailing newline"]


def test_the_server_keeps_answering_while_a_turn_is_in_flight(live, project):
    """CRITICAL 1 (2026-09-25 review): `api.say` and `pages.say` used to be
    `async def` handlers calling `service.say`, which blocks on
    `proc.wait(timeout=...)` for as long as the dispatched agent's own
    `timeout_min` -- up to 30 minutes for claude, 180 for codex in the
    shipped registry. `serve.py` runs a single uvicorn process, so that
    blocked the *one* event loop for the whole turn: the reviewer reproduced
    `/healthz` timing out after 9s while an 18.6s turn ran, leaving the
    dashboard, every run page, both SSE streams and the Cancel button
    unreachable. A plain `def` handler runs in Starlette's threadpool
    instead, which is what this proves: `/healthz` must stay fast while a
    (here, 3-second) turn is genuinely in flight on a real socket -- a
    `TestClient` cannot exercise this, see this module's own docstring."""
    from scieflow.core.run import conversation
    from scieflow.web.auth import CSRF_COOKIE

    ws = project.run_dir("r1")
    slow = f"{sys.executable} -c 'import time; time.sleep(3)' {{prompt}}"
    agents_yml = project.root / "config" / "agents.yml"
    agents_yml.write_text(agents_yml.read_text() + (
        f'  slow:\n    cmd: "{slow}"\n    session_cmd: "{slow}"\n'
        f'    resume_cmd: "{slow}"\n    family: claude\n    enabled: true\n'
        "    timeout_min: 1\n"
    ))
    conversation.set_agent(ws, "slow")

    turn_done = threading.Event()

    def send_turn():
        live.post("/api/v1/runs/r1/conversation", data={"message": "hello"},
                  headers={"x-csrf-token": live.cookies[CSRF_COOKIE]}, timeout=30.0)
        turn_done.set()

    sender = threading.Thread(target=send_turn, daemon=True)
    sender.start()
    time.sleep(0.5)   # let the turn actually start dispatching
    assert not turn_done.is_set(), "the turn finished before the probe ran -- test is racy"

    start = time.monotonic()
    health = live.get("/healthz")
    elapsed = time.monotonic() - start

    assert health.status_code == 200
    assert elapsed < 2.0, f"/healthz took {elapsed:.1f}s -- the event loop was blocked"

    sender.join(timeout=15.0)
    assert turn_done.is_set(), "the turn never completed"


def test_streams_need_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.get("/api/v1/runs/r1/events/stream").status_code == 401


def test_unknown_run_stream_is_404(client):
    assert client.get("/api/v1/runs/nope/events/stream").status_code == 404
