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


def test_run_page_escapes_a_malicious_event_type_server_side(client, project):
    """A `note.<name>` event type is agent-controlled free-form text
    (scieflow run log <slug> note.<name>), and `actor` is validated on
    write but not re-validated on read, so a corrupt/hand-edited
    events.jsonl line controls it too. The server-rendered half of the
    timeline table relies on Jinja autoescaping; pin that it actually
    escapes markup rather than passing it through."""
    from scieflow.core import events

    payload = "note.<img src=x onerror=alert(1)>"
    events.emit(project.run_dir("r1"), payload, "human")
    body = client.get("/runs/r1").text
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_run_html_timeline_script_never_uses_innerHTML_with_event_data():
    """Source-level guard for the *client-side* half of the same table: the
    live EventSource handler must build each row with `insertCell()` +
    `textContent`, never `innerHTML` with interpolated event data — that
    was the actual XSS sink (the server-rendered half above was already
    safe via Jinja autoescaping, so a functional test alone would not have
    caught a regression here)."""
    from scieflow.web.app import WEB_DIR

    src = (WEB_DIR / "templates" / "run.html").read_text()
    script = src[src.index("<script>"):]
    assert "innerHTML" not in script
    assert "insertCell" in script and "textContent" in script


def test_job_html_log_script_uses_textContent_not_innerHTML():
    """Same audit for the job-log append script — confirm it already uses
    textContent rather than assuming it."""
    from scieflow.web.app import WEB_DIR

    src = (WEB_DIR / "templates" / "job.html").read_text()
    script = src[src.index("<script>"):]
    assert "innerHTML" not in script
    assert "textContent" in script


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


def test_job_page_is_404_for_a_job_from_another_run(client, project):
    """The URL's slug must scope the job — jobs.find() alone matches by id
    across every run's jobs/ directory, so the route must check run_dir."""
    import sys

    from scieflow.core import jobs

    other_ws = project.workspace_root / "r2"
    other_job = jobs.run_blocking(
        project, [sys.executable, "-c", "print('other run')"],
        kind="agent", cwd=project.root, run_dir=other_ws, label="other",
    )
    assert client.get(f"/runs/r1/jobs/{other_job.id}").status_code == 404


def test_run_page_shows_the_finished_job_duration(client, project):
    """Pins service.job_json: asdict(job) drops duration_s (a @property, not
    a field), and Jinja would silently fall back to "—" with no exception.
    Matches the exact table cell so a coincidental "0.0" substring elsewhere
    on the page (e.g. inside a logged event's raw data) cannot fake a pass."""
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    assert job.duration_s is not None and job.duration_s > 0
    body = client.get("/runs/r1").text
    assert f'<td class="dim">{job.duration_s:.1f}</td>' in body


def test_job_page_shows_the_finished_job_duration(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    assert job.duration_s is not None and job.duration_s > 0
    body = client.get(f"/runs/r1/jobs/{job.id}").text
    assert f"· {job.duration_s:.1f}s" in body


def test_run_page_marks_an_unsandboxed_job(client, project):
    from scieflow.core import jobs

    job = jobs.list_jobs(project, project.run_dir("r1"))[0]
    job.sandboxed = False
    jobs.save(job)
    assert "unsandboxed" in client.get("/runs/r1").text.lower()
