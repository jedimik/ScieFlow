import shutil
import sys
import threading
import time

import pytest

from scieflow.core import events, jobs, sandbox
from scieflow.core.project import Project
from scieflow.core.run import status

PY_EXE = sys.executable


def project_and_run(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path), ws


def test_output_streams_to_disk_while_running(tmp_path):
    project, ws = project_and_run(tmp_path)
    code = "import time,sys\nprint('first', flush=True)\ntime.sleep(2)\nprint('second')"
    job, proc = jobs.start(project, [PY_EXE, "-c", code], kind="agent", cwd=tmp_path, run_dir=ws)
    deadline = time.time() + 5
    while "first" not in open(job.log).read() and time.time() < deadline:
        time.sleep(0.05)
    assert "first" in open(job.log).read()          # visible before exit
    done = jobs.wait(job, proc)
    assert done.state == "done" and done.exit_code == 0
    assert "second" in open(job.log).read()
    types = [e["type"] for e in events.read(ws)]
    assert types == ["job.queued", "job.started", "job.finished"]


def test_failure_keeps_stderr_and_exit_code(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"],
                            kind="agent", cwd=tmp_path, run_dir=ws)
    assert job.state == "failed" and job.exit_code == 3
    assert open(job.err).read() == "bad"


def test_timeout_keeps_partial_output(tmp_path):
    project, ws = project_and_run(tmp_path)
    code = "import time\nprint('partial', flush=True)\ntime.sleep(60)"
    job = jobs.run_blocking(project, [PY_EXE, "-c", code], kind="agent", cwd=tmp_path,
                            run_dir=ws, timeout_s=1)
    assert job.state == "timeout" and job.exit_code == jobs.TIMEOUT_EXIT
    assert "partial" in open(job.log).read()


def test_cancel_kills_the_whole_process_group(tmp_path):
    project, ws = project_and_run(tmp_path)
    marker = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)")
    job, proc = jobs.start(project, [PY_EXE, "-c", code], kind="agent", cwd=tmp_path, run_dir=ws)
    waiter = threading.Thread(target=jobs.wait, args=(job, proc))
    waiter.start()
    deadline = time.time() + 5
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    jobs.cancel(job)
    waiter.join(20)
    grandchild = int(marker.read_text())
    time.sleep(0.3)
    assert not jobs._alive(grandchild)
    assert jobs.find(project, job.id).state == "cancelled"


def test_stdin_is_fed_without_blocking(tmp_path):
    project, ws = project_and_run(tmp_path)
    big = "x" * 300_000   # larger than a pipe buffer
    job = jobs.run_blocking(project, [PY_EXE, "-c", "import sys; print(len(sys.stdin.read()))"],
                            kind="agent", cwd=tmp_path, run_dir=ws, stdin_text=big, timeout_s=30)
    assert open(job.log).read().strip() == "300000"


def test_jobs_outside_a_run_go_to_the_state_dir(tmp_path, monkeypatch):
    project, _ = project_and_run(tmp_path)
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "state"))
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent", cwd=tmp_path)
    assert job.log.startswith(str(tmp_path / "state" / "jobs"))
    assert [j.id for j in jobs.list_jobs(project)] == [job.id]


def test_reconcile_marks_dead_running_jobs_lost(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent", cwd=tmp_path, run_dir=ws)
    job.state, job.pid = "running", 999_999_999    # a pid that does not exist
    jobs.save(job)
    lost = jobs.reconcile(project, ws)
    assert [j.id for j in lost] == [job.id]
    assert jobs.find(project, job.id).state == "lost"


HAVE_BWRAP = shutil.which("bwrap") is not None


def test_a_job_without_a_writable_set_is_not_sandboxed(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent",
                            cwd=tmp_path, run_dir=ws)
    assert job.sandboxed is False
    assert job.state == "done"


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
def test_a_sandboxed_job_writes_inside_its_run_and_nowhere_else(tmp_path):
    project, ws = project_and_run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    code = (f"open({str(ws / 'inside.txt')!r}, 'w').write('ok')\n"
            f"try:\n"
            f"    open({str(outside / 'escaped.txt')!r}, 'w').write('bad')\n"
            f"except OSError:\n"
            f"    pass\n")
    job = jobs.run_blocking(project, [PY_EXE, "-c", code], kind="agent", cwd=ws,
                            run_dir=ws, sandbox_writable=[ws])
    assert job.sandboxed is True
    assert (ws / "inside.txt").exists()
    assert not (outside / "escaped.txt").exists()


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
def test_the_recorded_argv_is_the_command_not_the_wrapper(tmp_path):
    project, ws = project_and_run(tmp_path)
    job = jobs.run_blocking(project, [PY_EXE, "-c", "print(1)"], kind="agent",
                            cwd=ws, run_dir=ws, sandbox_writable=[ws])
    assert job.argv[0] == PY_EXE            # what was asked for
    assert "bwrap" not in " ".join(job.argv)  # not the plumbing


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not installed")
def test_sandbox_failure_is_recorded_as_failed_job(tmp_path):
    """Sandbox errors must be caught and recorded; they should not leave no trace."""
    project, ws = project_and_run(tmp_path)
    # Create a file (not a directory) to use as a writable path — this will fail in sandbox.wrap()
    bad_writable = tmp_path / "not_a_dir.txt"
    bad_writable.write_text("I am a file")

    # jobs.start should raise SandboxError
    with pytest.raises(sandbox.SandboxError):
        jobs.start(project, [PY_EXE, "-c", "print(1)"], kind="agent",
                   cwd=ws, run_dir=ws, sandbox_writable=[bad_writable])

    # But the job should have been recorded as failed
    all_jobs = jobs.list_jobs(project, ws)
    assert len(all_jobs) == 1
    failed_job = all_jobs[0]
    assert failed_job.state == "failed"
    assert failed_job.sandboxed is True  # sandboxing was requested

    # The error should be in the .err file
    err_content = open(failed_job.err).read()
    assert "directory" in err_content or "file" in err_content

    # A job.failed event should have been emitted
    emitted = events.read(ws)
    event_types = [e["type"] for e in emitted]
    assert "job.queued" in event_types
    assert "job.failed" in event_types
