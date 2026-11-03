"""M-D3 t3: the core job of a remote campaign (plan 5.8): record, update, append, and the cancel refusal."""

from __future__ import annotations

import pytest

from scieflow.core import events, jobs
from scieflow.core.project import Project
from scieflow.core.run import status
from scieflow.core.service import ServiceError, cancel_job


def project_and_run(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path), ws


def remote_job(project, ws):
    return jobs.record_external(project, kind="remote", label="gain-sweep on meta",
                                run_dir=ws, argv=["scieflow", "experiment", "watch", "x"],
                                cwd=ws)


def test_record_external_is_running_without_a_pid_and_emits_queued_then_started(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    assert job.kind == "remote" and job.state == "running"
    assert job.pid is None and job.sandboxed is False
    assert job.started and job.queued
    evs = [e for e in events.read(ws) if e["data"].get("job") == job.id]
    assert [e["type"] for e in evs] == ["job.queued", "job.started"]
    assert all("pid" not in e["data"] for e in evs)
    assert jobs.find(project, job.id).state == "running"


def test_cancel_refuses_a_remote_job_and_changes_nothing(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    with pytest.raises(jobs.NotCancellable, match="cannot be cancelled from ScieFlow yet"):
        jobs.cancel(job)
    assert jobs.find(project, job.id).state == "running"
    assert not [e for e in events.read(ws) if e["type"] == "job.cancelled"]


def test_the_service_turns_the_refusal_into_a_service_error(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    with pytest.raises(ServiceError) as caught:
        cancel_job(project, job.id)
    assert "scheduler job keeps running" in str(caught.value)
    assert jobs.find(project, job.id).state == "running"


def test_reconcile_never_marks_a_remote_job_lost(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    assert jobs.reconcile(project, ws) == []
    assert jobs.find(project, job.id).state == "running"


def test_update_external_done_emits_job_finished(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    done = jobs.update_external(job, "done", exit_code=0)
    assert done.state == "done" and done.exit_code == 0 and done.finished
    finished = [e for e in events.read(ws) if e["type"] == "job.finished"]
    assert len(finished) == 1
    assert finished[0]["data"]["exit_code"] == 0 and "duration_s" in finished[0]["data"]


def test_update_external_failed_emits_job_failed(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    jobs.update_external(job, "failed", exit_code=3)
    assert [e["type"] for e in events.read(ws)].count("job.failed") == 1


def test_update_external_refuses_other_states_final_jobs_and_jobs_with_a_pid(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    with pytest.raises(ValueError):
        jobs.update_external(job, "cancelled")
    jobs.update_external(job, "done", exit_code=0)
    with pytest.raises(ValueError):
        jobs.update_external(job, "failed", exit_code=1)
    local = jobs.Job(id="p1", kind="agent", argv=[], cwd=str(ws), state="running", pid=4242,
                     log=str(tmp_path / "p1.log"), err=str(tmp_path / "p1.err"))
    jobs.save(local)
    with pytest.raises(ValueError):
        jobs.update_external(local, "done", exit_code=0)


def test_append_log_writes_one_line_to_the_job_log(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = remote_job(project, ws)
    jobs.append_log(job, "counts: pending=0 submitted=2")
    assert open(job.log).read() == "counts: pending=0 submitted=2\n"
