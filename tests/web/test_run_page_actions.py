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
    assert post(client, "/runs/r1/act", action="delete-everything").status_code == 400


def test_cancelling_a_job_from_the_page(client, project, running_job):
    response = post(client, f"/runs/r1/jobs/{running_job.id}/cancel")
    assert response.status_code == 303
    assert "cancelled" in client.get("/runs/r1").text


def test_page_actions_need_csrf(client):
    assert client.post("/runs/r1/act", data={"action": "resume"}).status_code == 403
