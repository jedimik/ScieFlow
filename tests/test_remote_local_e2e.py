"""Local end-to-end (M-E t2, plan section 6 step 7): real subprocesses.

The `local` adapter runs the submit wrapper through bash, setsid, timeout and
rsync on this host. Nothing here touches a remote host. Each job runs in its
own session, and a teardown kills any session still alive, so no test leaks
a process into the load-flake accounting.
"""

import shutil
import subprocess
import time

import pytest

from remote import jobs, policy, remote as cli, schedulers, transport
from remote.schedulers.base import JobRef

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("bash", "setsid", "timeout", "rsync", "ps", "pkill")),
    reason="needs bash, setsid, timeout, rsync, ps and pkill",
)

POLL_SECONDS = 10.0
TERMINAL = ("done", "failed", "cancelled")


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    (root / "config" / "remotes.yml").write_text(f"""remotes:
  here:
    host: localhost
    user: local
    auth: none
    scheduler: local
    allow_experimental_scheduler: true
    allowed_dirs:
      - {allowed}
    allowed_ops: [check, submit, status, cancel, logs, fetch]
    limits:
      max_walltime: "00:10:00"
      max_cpus: 2
      max_mem_gb: 4
      max_gpus: 0
      max_scratch_gb: 0
      scratch_types: [tmp]
      queues: [default]
      max_concurrent_jobs: 4
      max_fix_attempts: 3
""")
    remote = policy.load_remote(root, "here")
    t = transport.for_remote(remote)
    sessions = []
    yield remote, t, allowed, ws, sessions
    for sid in sessions:
        subprocess.run(["pkill", "-KILL", "-s", str(sid)], check=False)


def write_script(allowed, name, body):
    path = allowed / name
    path.write_text(body + "\n")
    return name


def submit(remote, t, allowed, ws, script, *, walltime="00:01:00", sessions=None):
    rc = cli.cmd_submit(remote, t, str(allowed), script, workspace=ws, task="e2e",
                        walltime=walltime, cpus=1, mem_gb=1, gpus=0,
                        queue="default", name=None)
    assert rc == 0
    [entry] = jobs.load_jobs(ws)[-1:]
    pid = entry["job_id"].split(".")[0]
    if sessions is not None:
        sessions.append(int(pid))
    return entry["job_id"]


def status_line(remote, t, ws, job_id, capsys):
    capsys.readouterr()
    cli.cmd_status(remote, t, job_id, workspace=ws)
    return capsys.readouterr().out.strip()


def poll_until(remote, t, ws, job_id, capsys, want):
    deadline = time.monotonic() + POLL_SECONDS
    line = ""
    while time.monotonic() < deadline:
        line = status_line(remote, t, ws, job_id, capsys)
        if any(line.startswith(f"STATE: {w} ") for w in want):
            return line
        time.sleep(0.1)
    raise AssertionError(f"no terminal state within {POLL_SECONDS}s: {line}")


def session_alive(sid):
    out = subprocess.run(["ps", "-e", "-o", "sid="], capture_output=True, text=True).stdout
    return any(line.strip() == str(sid) for line in out.splitlines())


def test_done_job_logs_and_fetch(env, capsys):
    remote, t, allowed, ws, sessions = env
    write_script(allowed, "run.sh",
                 "mkdir -p results\necho hello-out\necho data > results/out.txt")
    job_id = submit(remote, t, allowed, ws, "run.sh", sessions=sessions)
    line = poll_until(remote, t, ws, job_id, capsys, TERMINAL)
    assert line == "STATE: done exit=0"

    assert cli.cmd_logs(remote, t, job_id, workspace=ws) == 0
    assert "hello-out" in capsys.readouterr().out

    dest = ws / "fetched"
    assert cli.cmd_fetch(remote, t, str(allowed / "results") + "/", str(dest), workspace=ws) == 0
    assert (dest / "out.txt").read_text() == "data\n"


def test_nonzero_exit_is_failed_with_that_code(env, capsys):
    remote, t, allowed, ws, sessions = env
    write_script(allowed, "run.sh", "exit 3")
    job_id = submit(remote, t, allowed, ws, "run.sh", sessions=sessions)
    line = poll_until(remote, t, ws, job_id, capsys, TERMINAL)
    assert line == "STATE: failed exit=3"


def test_walltime_kill_is_failed_with_124(env, capsys):
    remote, t, allowed, ws, sessions = env
    write_script(allowed, "run.sh", "sleep 30")
    job_id = submit(remote, t, allowed, ws, "run.sh", walltime="00:00:01", sessions=sessions)
    line = poll_until(remote, t, ws, job_id, capsys, TERMINAL)
    assert line == "STATE: failed exit=124"


def test_reported_pid_is_the_session_leader(env, capsys):
    remote, t, allowed, ws, sessions = env
    write_script(allowed, "run.sh", "sleep 30")
    job_id = submit(remote, t, allowed, ws, "run.sh", sessions=sessions)
    pid = job_id.split(".")[0]
    deadline = time.monotonic() + POLL_SECONDS
    sid = None
    while time.monotonic() < deadline and sid is None:
        ps = subprocess.run(["ps", "-o", "sid=", "-p", pid], capture_output=True, text=True)
        sid = ps.stdout.strip() or None
        time.sleep(0.1)
    assert sid == pid


def test_cancel_reaches_the_script_and_ends_the_session(env, capsys):
    remote, t, allowed, ws, sessions = env
    write_script(allowed, "run.sh", "sleep 30")
    job_id = submit(remote, t, allowed, ws, "run.sh", sessions=sessions)
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        if status_line(remote, t, ws, job_id, capsys) == "STATE: running exit=-":
            break
        time.sleep(0.1)
    job = JobRef(job_id=job_id, dir=str(allowed), job_name="run")
    adapter = schedulers.get("local")
    result = t.ssh(adapter.cancel_command(job))
    assert result.returncode == 0

    line = poll_until(remote, t, ws, job_id, capsys, TERMINAL)
    assert line.startswith("STATE: cancelled")

    sid = int(job_id.split(".")[0])
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline and session_alive(sid):
        time.sleep(0.1)
    assert not session_alive(sid), "the cancelled job's session is still alive"
