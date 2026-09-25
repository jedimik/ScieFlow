from scieflow.core.run import status
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_run_page_offers_the_actions(client):
    page = client.get("/runs/r1").text
    assert 'name="csrf_token"' in page
    assert 'action="/runs/r1/act"' in page
    assert "uv run scieflow gate answer" not in page   # the CLI-only note is gone


def test_answering_a_gate_from_the_page(client, project):
    gate = client.get("/api/v1/gates").json()[0]
    response = post(client, f"/runs/r1/gates/{gate['id']}", answer="A")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/r1"
    assert client.get("/api/v1/gates").json() == []


def test_answering_the_same_gate_twice_explains_itself(client):
    """The back button, a double click, a browser retry. The second answer
    must land the user back on the page with a sentence — not a 500, and not
    a second answer recorded."""
    gate = client.get("/api/v1/gates").json()[0]
    post(client, f"/runs/r1/gates/{gate['id']}", answer="A")
    again = post(client, f"/runs/r1/gates/{gate['id']}", answer="B")
    assert again.status_code == 303
    assert again.headers["location"].startswith("/runs/r1?error=")
    assert "not open" in client.get(again.headers["location"]).text


def test_a_second_tab_sees_its_own_action(client, project):
    """Two tabs, both mutating. Neither write corrupts the store, and the
    redirect means the acting tab always re-reads rather than showing the
    state it rendered before."""
    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="running")
    post(client, "/runs/r1/act", action="phase", phase="hypothesize", state="done")
    assert "done" in client.get("/runs/r1").text
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "done"


def test_checkpoint_and_resume_from_the_page(client, project):
    post(client, "/runs/r1/act", action="checkpoint", reason="user", detail="lunch")
    assert "stopped: user" in client.get("/runs/r1").text
    post(client, "/runs/r1/act", action="resume")
    assert "stopped: user" not in client.get("/runs/r1").text


def test_an_unknown_action_is_refused(client):
    """Every other failure on this route renders the page via `_back` with
    `?error=` — 400 and a raw JSON body (FastAPI's default `HTTPException`
    handling) would be the one inconsistent failure mode on an otherwise
    post/redirect/get page. Land on the same redirect-with-error instead."""
    response = post(client, "/runs/r1/act", action="delete-everything")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert "unknown action" in client.get(response.headers["location"]).text


def test_cancelling_a_job_from_the_page(client, project, running_job):
    response = post(client, f"/runs/r1/jobs/{running_job.id}/cancel")
    assert response.status_code == 303
    assert "cancelled" in client.get("/runs/r1").text


def test_cancelling_a_job_via_a_different_runs_path_is_404(client, project):
    """A job started under r2 must not be reachable through r1's cancel
    route — `_owning_job` is what stops a request reaching a job in a
    different run. Verified behaviour already; this just puts a test on it."""
    import sys

    from scieflow.core import jobs

    r2 = project.root / "workspace" / "r2"
    r2.mkdir(parents=True)
    job, proc = jobs.start(project, [sys.executable, "-c", "import time; time.sleep(300)"],
                           kind="agent", cwd=project.root, run_dir=r2, label="other-run")
    try:
        response = post(client, f"/runs/r1/jobs/{job.id}/cancel")
        assert response.status_code == 404
    finally:
        jobs.cancel(job)
        jobs.wait(job, proc)


def test_page_actions_need_csrf(client):
    assert client.post("/runs/r1/act", data={"action": "resume"}).status_code == 403
