"""M-D3 t3: a remote campaign's core job in the run page (plan 5.8 web item). No web code changes."""

from __future__ import annotations

from scieflow.core import jobs
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form}, follow_redirects=False)


def test_the_job_page_of_a_remote_job_renders_its_log(client, project):
    ws = project.root / "workspace" / "r1"
    job = jobs.record_external(project, kind="remote", label="gain-sweep on meta",
                               run_dir=ws, argv=["scieflow", "experiment", "watch", "x"], cwd=ws)
    jobs.append_log(job, "counts: pending=0 submitted=2")
    body = client.get(f"/runs/r1/jobs/{job.id}").text
    assert "counts: pending=0 submitted=2" in body


def test_remote_job_row_shows_running_and_cancel_says_why(client, project):
    ws = project.root / "workspace" / "r1"
    job = jobs.record_external(project, kind="remote", label="gain-sweep on meta",
                               run_dir=ws, argv=["scieflow", "experiment", "watch", "x"], cwd=ws)
    page = client.get("/runs/r1").text
    assert "gain-sweep on meta" in page
    response = post(client, f"/runs/r1/jobs/{job.id}/cancel")
    assert response.status_code == 303
    message = client.get(response.headers["location"]).text
    assert "cannot be cancelled from ScieFlow" in message
    assert jobs.find(project, job.id).state == "running"
