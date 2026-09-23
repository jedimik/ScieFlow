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


def test_run_page_shows_status_jobs_gates_and_timeline(client):
    response = client.get("/runs/r1")
    assert response.status_code == 200
    body = response.text
    assert "hypothesize" in body                 # phases table
    assert "run.created" in body                 # timeline
    assert "done" in body                        # the finished job
    assert "Which dataset?" in body              # the open gate
    assert "answer" in body.lower()              # tells you how to answer it


def test_run_page_links_each_job_to_its_log(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    body = client.get("/runs/r1").text
    assert f"/runs/r1/jobs/{job.id}" in body


def test_unknown_run_page_is_404(client):
    assert client.get("/runs/nope").status_code == 404


def test_job_log_page_shows_the_output(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    body = client.get(f"/runs/r1/jobs/{job.id}").text
    assert "hello from the job" in body
