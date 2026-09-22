def test_runs_lists_the_run(client):
    body = client.get("/api/v1/runs").json()
    assert [r["slug"] for r in body] == ["r1"]


def test_run_detail_matches_the_service_layer(client, project):
    from scieflow.core import service

    body = client.get("/api/v1/runs/r1").json()
    assert body["status"]["run"] == "r1"
    assert body["remaining"]["iterations"] == 1.0
    assert body == service.run_detail(project, "r1")


def test_unknown_run_is_404_with_a_message(client):
    response = client.get("/api/v1/runs/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["error"]


def test_events_can_be_filtered_by_type(client):
    all_events = client.get("/api/v1/runs/r1/events").json()
    assert [e["type"] for e in all_events][:2] == ["run.created", "phase.started"]
    only_jobs = client.get("/api/v1/runs/r1/events", params={"type": "job.*"}).json()
    assert only_jobs and all(e["type"].startswith("job.") for e in only_jobs)


def test_events_since_returns_the_tail(client):
    all_events = client.get("/api/v1/runs/r1/events").json()
    tail = client.get("/api/v1/runs/r1/events",
                      params={"since": all_events[0]["id"]}).json()
    assert len(tail) == len(all_events) - 1


def test_jobs_lists_the_finished_job(client):
    body = client.get("/api/v1/runs/r1/jobs").json()
    assert len(body) == 1 and body[0]["state"] == "done"
    assert body[0]["duration_s"] is not None


def test_open_gates_across_runs(client):
    body = client.get("/api/v1/gates").json()
    assert len(body) == 1
    assert body[0]["slug"] == "r1" and body[0]["question"] == "Which dataset?"


def test_agents_reports_assignments(client):
    body = client.get("/api/v1/agents").json()
    assert body["assignments"]["research.outline"]["value"] == "stub"


def test_api_requires_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        assert anonymous.get("/api/v1/runs").status_code == 401


def test_openapi_schema_is_served(client):
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/runs/{slug}" in schema["paths"]
