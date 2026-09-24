import pytest

from scieflow.core import events
from scieflow.core.run import status
from scieflow.web import auth
from tests.web.mutating_paths import MUTATING_PATHS, SAMPLES, concrete_path


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


def test_every_mutating_path_has_a_guard_sample():
    """The inventory (`MUTATING_PATHS`, also read by test_read_only.py) and
    the guard samples below are two different data structures; nothing but
    this assertion keeps them in lockstep. Without it, a route can be added
    to one and not the other — which is exactly what happened before (the
    inventory grew to 11 paths while the guard tests covered 7)."""
    assert set(SAMPLES) == set(MUTATING_PATHS)


@pytest.mark.parametrize("template", sorted(MUTATING_PATHS))
def test_every_mutation_needs_a_session(project, template):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        response = anonymous.post(concrete_path(template), data=SAMPLES[template])
        assert response.status_code in (401, 403)


@pytest.mark.parametrize("template", sorted(MUTATING_PATHS))
def test_every_mutation_needs_csrf(client, template):
    response = client.post(concrete_path(template), data=SAMPLES[template])
    assert response.status_code == 403


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


def test_spend_over_http_records_the_ledger(client, project):
    """The spend route has only ever had guard tests; a positive round trip
    was missing."""
    from scieflow.core.run import budget

    response = post(client, "/api/v1/runs/r1/spend",
                    experiment_runs="2", wall_minutes="1.5")
    assert response.status_code == 200
    b = budget.read_budget(project.run_dir("r1"))
    assert b["spent"]["experiment_runs"] == 2
    assert b["spent"]["wall_minutes"] == 1.5


def test_negative_spend_over_http_is_refused_and_leaves_the_ledger_alone(client, project):
    """`budget.spent.experiment_runs` must never go negative through this
    route — that is the one automatic brake on runaway agent spend."""
    from scieflow.core.run import budget

    response = post(client, "/api/v1/runs/r1/spend", experiment_runs="-5")
    assert response.status_code != 200
    assert budget.read_budget(project.run_dir("r1"))["spent"]["experiment_runs"] == 0


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
