"""jobs.save validates each record before it is written (M-I t2, plan 5.2)."""

import importlib
import json
import sys
from pathlib import Path

import pytest

from scieflow.core import jobs, store
from scieflow.core.project import Project
from scieflow.core.run import status

PY = sys.executable


def records():
    return importlib.import_module("scieflow.core.records")


def project_and_run(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path), ws


def make_job(project, ws, **over):
    jid = store.new_id()
    directory = jobs.jobs_dir(project, ws)
    directory.mkdir(parents=True, exist_ok=True)
    values = dict(id=jid, kind="turn", argv=["x"], cwd=str(ws), label="t", run_dir=str(ws),
                  state="queued", queued="2026-11-03T12:00:00.000+00:00",
                  log=str(directory / f"{jid}.log"), err=str(directory / f"{jid}.err"))
    values.update(over)
    return jobs.Job(**values)


def record_of(job):
    return Path(job.log).with_suffix(".json")


CHANGES = [
    (lambda j: setattr(j, "state", "paused"), "job-state"),
    (lambda j: setattr(j, "pid", True), "job-numbers"),
    (lambda j: setattr(j, "argv", [1]), "job-argv"),
    (lambda j: setattr(j, "state", "done"), "job-final"),
    (lambda j: setattr(j, "id", "../escape"), "job-id"),
    (lambda j: setattr(j, "kind", "Agent"), "job-kind"),
]


@pytest.mark.parametrize("change, rule", CHANGES)
def test_save_refuses_each_rule_and_keeps_the_record(tmp_path, change, rule):
    project, ws = project_and_run(tmp_path)
    job = make_job(project, ws)
    jobs.save(job)
    before = record_of(job).read_bytes()
    change(job)
    with pytest.raises(records().JobInvalid) as info:
        jobs.save(job)
    assert info.value.rule == rule
    assert record_of(job).read_bytes() == before


def test_save_refuses_a_forged_record_with_an_extra_key(tmp_path):
    from dataclasses import asdict

    project, ws = project_and_run(tmp_path)
    job = make_job(project, ws)
    forged = dict(asdict(job), extra=1)
    with pytest.raises(records().JobInvalid) as info:
        records().check_job(forged)
    assert info.value.rule == "job-keys"


def test_save_accepts_final_jobs_with_finished_and_remote_without_pid(tmp_path):
    project, ws = project_and_run(tmp_path)
    done = make_job(project, ws, state="done", finished="2026-11-03T12:00:02.000+00:00", exit_code=0)
    jobs.save(done)
    remote = make_job(project, ws, kind="remote", state="running", pid=None)
    jobs.save(remote)
    assert {j.id for j in jobs.list_jobs(project, ws)} == {done.id, remote.id}


def test_start_wait_and_cancel_still_save_valid_records(tmp_path):
    project, ws = project_and_run(tmp_path)
    job, proc = jobs.start(project, [PY, "-c", "print('hi')"], kind="agent", cwd=tmp_path, run_dir=ws)
    assert jobs.wait(job, proc).state == "done"
    slow, proc2 = jobs.start(project, [PY, "-c", "import time; time.sleep(30)"],
                             kind="agent", cwd=tmp_path, run_dir=ws)
    assert jobs.cancel(slow).state == "cancelled"
    proc2.wait(timeout=30)
    assert records().check_job_files(jobs.jobs_dir(project, ws)) == []


def test_reading_keeps_an_invalid_record_visible(tmp_path):
    import dataclasses

    project, ws = project_and_run(tmp_path)
    job = make_job(project, ws)
    jobs.save(job)
    raw = dict(dataclasses.asdict(job), state="paused")
    store.write_text(record_of(job), json.dumps(raw))
    assert [j.state for j in jobs.list_jobs(project, ws)] == ["paused"]
