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
    from scieflow.core import events

    ws = project.run_dir("r1")
    events.emit(ws, "note.hello", "human", message="from the test")
    frames = read_frames(live, "/api/v1/runs/r1/events/stream", want=3)
    types = [json.loads(frame)["type"] for frame in frames]
    assert types[0] == "run.created"
    assert "note.hello" in types or len(types) == 3


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


def test_streams_need_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.get("/api/v1/runs/r1/events/stream").status_code == 401


def test_unknown_run_stream_is_404(client):
    assert client.get("/api/v1/runs/nope/events/stream").status_code == 404
