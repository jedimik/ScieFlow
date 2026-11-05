"""T-12 t4: the JSON counterparts of the Agents page (`/api/v1/agents`).

`GET /api/v1/agents` without `scope` is unchanged (it still returns command
templates, T12-D8); with `scope` it returns the browser view, which never
includes template text. Synthetic temporary projects only.
"""

import re

import yaml

from scieflow.web import auth


def _agents_yml(project):
    return project.root / "config" / "agents.yml"


def _csrf(client):
    return client.cookies[auth.CSRF_COOKIE]


def _post(client, route, fields):
    grouped: dict[str, list] = {}
    for name, value in [*fields, (auth.CSRF_FIELD, _csrf(client))]:
        grouped.setdefault(name, []).append(value)
    body = {name: values[0] if len(values) == 1 else values for name, values in grouped.items()}
    return client.post(route, data=body, follow_redirects=False)


def _preview_api(client, fields, scope="project", slug=None):
    base = [("scope", scope)] + ([("slug", slug)] if slug else [])
    return _post(client, "/api/v1/agents/preview", base + list(fields))


def test_unscoped_agents_is_unchanged_and_still_carries_templates(client):
    response = client.get("/api/v1/agents")
    assert response.status_code == 200
    body = response.json()
    assert body["assignments"]["research.outline"]["value"] == "stub"
    assert "stub_agent" in response.text


def test_scoped_view_never_carries_command_text(client):
    response = client.get("/api/v1/agents", params={"scope": "project"})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "project"
    assert body["agents"]["stub"]["cmd"] == {"configured": True, "cli_only": True}
    assert "stub_agent" not in response.text


def test_scoped_view_of_an_unknown_run_is_a_400_error(client):
    response = client.get("/api/v1/agents", params={"scope": "workspace", "slug": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["error"]


def test_news_view_reports_when_news_is_absent(client):
    body = client.get("/api/v1/agents", params={"scope": "news"}).json()
    assert body["available"] is False


def test_api_preview_returns_the_plan_as_json(client):
    response = _preview_api(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")])
    assert response.status_code == 200
    body = response.json()
    assert re.fullmatch(r"[0-9a-f]{64}", body["revision"])
    assert body["changes"] == ["set stub.timeout_min=7"]
    assert any(row["key"].endswith("timeout_min") for row in body["structured"])


def test_api_preview_refuses_a_forged_command_with_400(client):
    response = _preview_api(client, [("agent.stub.cmd", "echo hi")])
    assert response.status_code == 400
    assert "CLI-only" in response.json()["error"]


def test_api_apply_writes_and_reports_the_written_files(client, project):
    plan = _preview_api(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")]).json()
    response = _post(client, "/api/v1/agents/apply", [
        ("scope", "project"), ("revision", plan["revision"]), ("change", plan["changes"][0])])
    assert response.status_code == 200
    assert response.json()["written"] == ["config/agents.yml"]
    assert yaml.safe_load(_agents_yml(project).read_text())["agents"]["stub"]["timeout_min"] == 7


def test_api_apply_of_a_stale_preview_is_a_409(client, project):
    plan = _preview_api(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")]).json()
    _agents_yml(project).write_text(_agents_yml(project).read_text() + "# elsewhere\n")
    response = _post(client, "/api/v1/agents/apply", [
        ("scope", "project"), ("revision", plan["revision"]), ("change", plan["changes"][0])])
    assert response.status_code == 409
    assert "changed since it was previewed" in response.json()["error"]


def test_api_apply_without_a_revision_is_a_400(client):
    response = _post(client, "/api/v1/agents/apply", [
        ("scope", "project"), ("change", "set stub.timeout_min=7")])
    assert response.status_code == 400
    assert "preview the change first" in response.json()["error"]


def test_api_mutations_need_the_csrf_token(client):
    response = client.post("/api/v1/agents/preview", data={"scope": "project"})
    assert response.status_code == 403


def test_api_mutations_need_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app
    with TestClient(create_app(project, "test-token")) as anonymous:
        # A matching CSRF pair, but no session: the session guard refuses (401).
        anonymous.cookies.set(auth.CSRF_COOKIE, "matching")
        response = anonymous.post("/api/v1/agents/apply", data={
            auth.CSRF_FIELD: "matching", "scope": "project"})
    assert response.status_code == 401
    assert "error" in response.json()  # a JSON refusal, not a page
