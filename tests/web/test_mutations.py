import pytest

from scieflow.core import events
from scieflow.core.run import status
from scieflow.web import auth


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


MUTATIONS = [
    ("/api/v1/runs/r1/phase", {"phase": "hypothesize", "state": "running"}),
    ("/api/v1/runs/r1/advance", {}),
    ("/api/v1/runs/r1/checkpoint", {"reason": "user"}),
    ("/api/v1/runs/r1/resume", {}),
    ("/api/v1/runs/r1/spend", {"experiment_runs": "1"}),
]


@pytest.mark.parametrize("path, form", MUTATIONS)
def test_every_mutation_needs_a_session(project, path, form):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.post(path, data=form).status_code in (401, 403)


@pytest.mark.parametrize("path, form", MUTATIONS)
def test_every_mutation_needs_csrf(client, path, form):
    assert client.post(path, data=form).status_code == 403


def test_mark_phase_over_http(client, project):
    assert post(client, "/api/v1/runs/r1/phase",
                phase="hypothesize", state="running").status_code == 200
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "running"


def test_checkpoint_and_resume_over_http(client, project):
    assert post(client, "/api/v1/runs/r1/checkpoint", reason="user").status_code == 200
    assert status.read_status(project.run_dir("r1"))["stopped"]["reason"] == "user"
    assert post(client, "/api/v1/runs/r1/resume").status_code == 200
    assert not status.read_status(project.run_dir("r1")).get("stopped")


def test_answer_a_gate_over_http(client, project):
    gate = client.get("/api/v1/gates").json()[0]
    assert post(client, f"/api/v1/runs/r1/gates/{gate['id']}/answer",
                answer="A").status_code == 200
    assert client.get("/api/v1/gates").json() == []
    assert "gate.answered" in [e["type"] for e in events.read(project.run_dir("r1"))]


def test_a_mutation_on_an_unknown_run_is_404(client):
    """A stale tab posting to a deleted slug gets a clean error, not a traceback."""
    assert post(client, "/api/v1/runs/nope/advance").status_code == 404


def test_a_refused_page_post_renders_html_not_a_json_blob(client):
    """A form posted after the session expired must tell the person how to
    get back in. The CSRF check happens in middleware, before routing, so it
    answers directly and never reaches the exception handler that renders the
    page — the middleware has to render it itself."""
    response = client.post("/runs/r1", headers={"Accept": "text/html"})
    assert response.status_code == 403
    assert "scieflow serve" in response.text
    assert "Not signed in" in response.text


def test_an_api_post_still_gets_json(client):
    """Only page paths get HTML; /api stays a JSON surface for other tools."""
    response = client.post("/api/v1/runs/r1/advance",
                           headers={"Accept": "text/html"})
    assert response.status_code == 403
    assert response.json()["error"]


def test_the_route_still_sees_its_form_after_the_middleware_read_it(client, project):
    """The CSRF middleware reads the request body to find the form field, and
    it runs before routing. Starlette's BaseHTTPMiddleware replays a body that
    was already read (`_CachedRequest.wrapped_receive`), so the route's own
    Form(...) parameters still arrive — but only because the middleware calls
    `await request.body()` first. This test fails loudly if that ordering is
    lost or the framework stops replaying, instead of the route silently
    receiving an empty form."""
    from scieflow.core.run import status

    response = post(client, "/api/v1/runs/r1/phase",
                    phase="experiment", state="running")
    assert response.status_code == 200, response.text
    assert status.read_status(project.run_dir("r1"))["phases"]["experiment"] == "running"
