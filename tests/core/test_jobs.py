import sys
import threading
import time

from scieflow.core import events, jobs
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
