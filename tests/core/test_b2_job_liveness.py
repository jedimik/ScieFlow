"""T-9 t1: job liveness (B2:5.6.1) and the one writer of `lost` (reconcile_archive_job).

A GET-side read (effective_job) writes nothing; only reconcile_archive_job writes, and only `lost`.
jobs.find, jobs.list_jobs and jobs.reconcile must never be called by archives.
"""

import json
import os
import signal
import subprocess

import pytest

from scieflow.core import archives as a
from scieflow.core import jobs, records
from tests.fixtures.b2_archives import (dead_pid, jobs_dir, make_project, state_dir, tree_snapshot,
                                        write_job, write_job_pointer, write_record)

SLUG = "r1"


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    # the three job-table readers of core/jobs.py must never run from archives
    for name in ("find", "list_jobs", "reconcile"):
        def boom(*args, _name=name, **kwargs):
            raise AssertionError(f"archives called jobs.{_name}")
        monkeypatch.setattr(jobs, name, boom)
    return proj


def _child_record(project, *, pid, outcome=None, reason=None, exit_code=None):
    doc = {"outcome": outcome, "reason": reason, "exit_code": exit_code, "pid": pid}
    write_record(project, SLUG, doc)


def test_archives_source_does_not_name_the_job_table_readers():
    import pathlib
    source = pathlib.Path(a.__file__).read_text(encoding="utf-8")
    for name in ("jobs.find", "jobs.list_jobs", "jobs.reconcile"):
        assert name not in source


def test_bad_job_pointer_content_is_no_job_with_a_reason(project):
    write_job_pointer(project, SLUG, "not-a-ulid")
    job = a.effective_job(project, SLUG)
    assert job.running is False and job.reason == "job-pointer-unreadable"


def test_missing_job_pointer_is_no_job_without_a_reason(project):
    job = a.effective_job(project, SLUG)
    assert job.running is False and job.reason is None and job.id is None


def test_missing_record_is_no_job(project):
    write_job_pointer(project, SLUG, "01J00000000000000000000000")
    job = a.effective_job(project, SLUG)
    assert job.running is False and job.id is None


def test_unparsable_record_is_no_job(project):
    job_id = write_job(project, SLUG, state="running", pid=os.getpid())
    (jobs_dir(project) / f"{job_id}.json").write_text("{nope")
    assert a.effective_job(project, SLUG).id is None


def test_oversized_record_is_no_job(project):
    job_id = write_job(project, SLUG, state="running", pid=os.getpid())
    path = jobs_dir(project) / f"{job_id}.json"
    path.write_text(" " * (a.JOB_RECORD_MAX_BYTES + 1) + path.read_text())
    assert a.effective_job(project, SLUG).id is None


@pytest.mark.parametrize("kind,label", [("agent", SLUG), ("archive-fetch", "other")])
def test_wrong_kind_or_label_is_no_job(project, kind, label):
    job_id = write_job(project, SLUG, kind="archive-fetch", state="running", pid=os.getpid())
    path = jobs_dir(project) / f"{job_id}.json"
    doc = json.loads(path.read_text())
    doc["kind"], doc["label"] = kind, label
    path.write_text(json.dumps(doc))
    assert a.effective_job(project, SLUG).id is None


def test_alive_running_job(project):
    job_id = write_job(project, SLUG, kind="archive-fetch", state="running", pid=os.getpid(),
                       argv=["python", "dvc_sync.py", "fetch", SLUG, "--verify-only"])
    job = a.effective_job(project, SLUG)
    assert (job.id, job.kind, job.running, job.verify_only) == (job_id, "archive-fetch", True, True)
    assert job.outcome is None


def test_dead_running_job_is_not_running_and_interrupted_without_a_child_record(project):
    write_job(project, SLUG, state="running", pid=dead_pid())
    job = a.effective_job(project, SLUG)
    assert job.running is False and job.outcome == "interrupted"


def test_dead_running_job_takes_the_pid_matched_child_record(project):
    pid = dead_pid()
    write_job(project, SLUG, state="running", pid=pid)
    _child_record(project, pid=pid, outcome="ready", exit_code=0)
    assert a.effective_job(project, SLUG).outcome == "ready"


def test_child_record_with_another_pid_is_ignored(project):
    pid = dead_pid()
    write_job(project, SLUG, state="running", pid=pid)
    _child_record(project, pid=pid + 1 if pid < 2**22 else pid - 1, outcome="ready", exit_code=0)
    assert a.effective_job(project, SLUG).outcome == "interrupted"


@pytest.mark.parametrize("state,child,expected", [
    ("done", ("ready", None, 0), "ready"),
    ("done", None, None),
    ("cancelled", None, "interrupted"),
    ("lost", None, "interrupted"),
    ("timeout", None, "interrupted"),
    ("failed", ("failed", "credentials-refused", 1), "failed:credentials-refused"),
    ("failed", None, "failed:dvc-failed"),
    ("failed", None, "failed:locked"),
])
def test_final_states_outcomes(project, state, child, expected):
    pid = dead_pid()
    exit_code = a.LOCKED_EXIT if expected == "failed:locked" else 1
    write_job(project, SLUG, state=state, pid=pid, exit_code=exit_code)
    if child:
        outcome, reason, code = child
        _child_record(project, pid=pid, outcome=outcome, reason=reason, exit_code=code)
    assert a.effective_job(project, SLUG).outcome == expected


def test_effective_job_writes_nothing(project):
    write_job(project, SLUG, state="running", pid=dead_pid())
    before = tree_snapshot(state_dir(project))
    a.effective_job(project, SLUG)
    assert tree_snapshot(state_dir(project)) == before


def test_reconcile_turns_a_dead_running_record_into_lost_and_it_passes_check_job(project):
    pid = dead_pid()
    job_id = write_job(project, SLUG, state="running", pid=pid)
    assert a.reconcile_archive_job(project, SLUG) is True
    doc = json.loads((jobs_dir(project) / f"{job_id}.json").read_text())
    assert doc["state"] == "lost" and doc["finished"]
    records.check_job(doc)


def test_reconcile_leaves_alive_and_final_records_untouched(project):
    job_id = write_job(project, SLUG, state="running", pid=os.getpid())
    path = jobs_dir(project) / f"{job_id}.json"
    before = path.read_bytes()
    assert a.reconcile_archive_job(project, SLUG) is False
    assert path.read_bytes() == before
    write_job(project, SLUG, state="done", pid=dead_pid(), job_id=job_id)
    final = path.read_bytes()
    assert a.reconcile_archive_job(project, SLUG) is False
    assert path.read_bytes() == final


def test_reconcile_never_signals_a_dead_pid(project, monkeypatch):
    write_job(project, SLUG, state="running", pid=dead_pid())
    sent = []
    real_kill = os.kill

    def spy(pid, sig):
        sent.append(sig)
        return real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", spy)
    a.reconcile_archive_job(project, SLUG)
    assert all(sig == 0 for sig in sent), sent


def test_reconcile_with_no_job_is_false_and_writes_nothing(project):
    assert a.reconcile_archive_job(project, SLUG) is False
    assert not state_dir(project).exists() or not any(state_dir(project).rglob("*.json"))
