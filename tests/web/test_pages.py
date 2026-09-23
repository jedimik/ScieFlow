def test_dashboard_lists_runs_and_open_gates(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "r1" in body
    assert "Which dataset?" in body
    assert '<a href="/runs/r1"' in body


def test_dashboard_shows_budget_remaining(client):
    body = client.get("/").text
    # 0 of 3 iterations spent -> a full bar, labelled
    assert "iterations" in body and "100%" in body
    # Bar markup must be present: class="bar" div containing span with style="width: 100%"
    assert 'class="bar' in body  # catches both class="bar" and class="bar "
    assert 'style="width: 100%"' in body


def test_dashboard_needs_a_session(project):
    from fastapi.testclient import TestClient

    from scieflow.web.app import create_app

    with TestClient(create_app(project, "tok")) as anonymous:
        response = anonymous.get("/", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert "scieflow serve" in response.text


def test_dashboard_with_no_runs_says_so(tmp_path):
    from fastapi.testclient import TestClient

    from scieflow.core.project import Project
    from scieflow.web.app import create_app

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    with TestClient(create_app(Project(tmp_path), "tok")) as client:
        client.get("/healthz?token=tok")
        body = client.get("/").text
    assert "No runs yet" in body
