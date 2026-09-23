"""One pass through the app the way a browser makes it: token in, dashboard,
run page, job output, artifact, live stream.

The task-8 brief's own version of this test ends with a
`fastapi.testclient.TestClient.stream(...)` call against
`/api/v1/runs/r1/events/stream`. That hangs forever: Starlette's
`TestClient` (and httpx's `ASGITransport` underneath it) fully drains an
ASGI response before returning anything, and this endpoint's generator only
ends when the client disconnects -- it never finishes on its own. See
`tests/web/test_sse.py`'s module docstring, which discovered and documented
this in an earlier task of this milestone.

So the streaming step here reuses that file's fix rather than repeating the
brief's hanging call: a real uvicorn server on a loopback socket, reached
with a real `httpx.Client`, which supports genuine incremental delivery and
returns control before the stream ends. Everything else in the walkthrough
(landing redirect, dashboard, run page, job output, artifact) is an
ordinary request/response and is exercised with the in-process
`TestClient`, as the brief has it.
"""

import json
import socket
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient

from scieflow.core import jobs
from scieflow.web.app import create_app

TOKEN = "smoke-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_browser_session_end_to_end(project):
    (project.run_dir("r1") / "notes.md").write_text("# findings\n")
    with TestClient(create_app(project, TOKEN)) as client:
        landing = client.get(f"/?token={TOKEN}", follow_redirects=False)
        assert landing.status_code == 303 and "token" not in landing.headers["location"]

        dashboard = client.get("/")
        assert dashboard.status_code == 200 and "r1" in dashboard.text

        run_page = client.get("/runs/r1")
        assert "Which dataset?" in run_page.text

        job = jobs.list_jobs(project, project.run_dir("r1"))[0]
        assert "hello from the job" in client.get(f"/runs/r1/jobs/{job.id}").text

        assert "# findings" in client.get("/runs/r1/file",
                                          params={"path": "notes.md"}).text

        assert client.get("/api/v1/runs").json()[0]["slug"] == "r1"

    # Live stream, over a real socket (see module docstring above).
    port = _free_port()
    config = uvicorn.Config(create_app(project, TOKEN), host="127.0.0.1",
                            port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5.0
        while not server.started and time.time() < deadline:
            time.sleep(0.01)
        assert server.started, "uvicorn did not start in time"

        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as live:
            live.get(f"/healthz?token={TOKEN}")   # exchange token for cookies
            with live.stream("GET", "/api/v1/runs/r1/events/stream",
                             timeout=10.0) as stream:
                first = next(line for line in stream.iter_lines()
                            if line.startswith("data:"))
        assert json.loads(first[len("data:"):])["type"] == "run.created"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
