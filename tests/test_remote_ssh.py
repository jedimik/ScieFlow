"""Plain SSH adapter (M-E t2): golden strings, status parsing, cmd_* wiring.

Plain SSH is experimental (D19). It never runs against a real host in M-E.
RED first: written before scripts/remote/schedulers/ssh.py exists.
"""

import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli, schedulers, transport
from remote.errors import PolicyError
from remote.schedulers.base import JobRef, JobSpec, SubmitParseError

FIXTURES = Path(__file__).parent / "fixtures" / "scheduler" / "ssh"

# The constant wrapper W from reviews/m-e-plan.md section 5.5 (plain SSH).
WRAPPER = ('exec >"$0.o$$" 2>"$0.e$$"; timeout "$2" bash "$1"; rc=$?; '
           "printf '%s\\n' \"$rc\" >\"$0.x$$.tmp\" && mv \"$0.x$$.tmp\" \"$0.x$$\"")

REMOTE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="ssh",
    allowed_dirs=["/storage/x"],
    allowed_ops=["check", "submit", "status", "cancel", "logs"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 2, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd"], "queues": ["default"],
            "max_concurrent_jobs": 4, "max_fix_attempts": 3},
)

JOB = JobRef(job_id="4242.1761400000", dir="/storage/x/repo", job_name="run")
NOTICE = ("NOTICE: ssh scheduler does not enforce cpus/mem/gpus/queue; "
          "walltime is enforced with timeout")


def adapter():
    return schedulers.get("ssh")


def spec(**overrides):
    values = dict(name="run", dir="/storage/x/repo", script="run.sh",
                  walltime="01:00:00", cpus=1, mem_gb=4, gpus=0, queue="default",
                  scratch_type="none", scratch_gb=0, env={})
    values.update(overrides)
    return JobSpec(**values)


def recording(replies=()):
    calls = []
    queue = list(replies)

    def runner(argv):
        calls.append(argv)
        rc, out, err = queue.pop(0) if queue else (0, "", "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return transport.Transport(REMOTE, runner=runner), calls


def read(name):
    return (FIXTURES / name).read_text()


def submit(tmp_path, t, **overrides):
    kwargs = dict(workspace=tmp_path, task="run", walltime="01:00:00", cpus=1,
                  mem_gb=4, gpus=0, queue="default", name=None)
    kwargs.update(overrides)
    return cli.cmd_submit(REMOTE, t, "/storage/x/repo", "run.sh", **kwargs)


def seed(tmp_path, state="queued"):
    jobs.save_jobs(tmp_path, [{
        "task": "run", "job_id": "4242.1761400000", "remote": "meta",
        "dir": "/storage/x/repo", "script": "run.sh", "job_name": "run",
        "resources": {}, "environment": {}, "scheduler": "ssh",
        "state": state, "attempt": 1, "submitted": "2026-10-25T00:00:00+00:00",
    }])


def launch(prefix=""):
    """The exact submit line: a grouped background launch, so $! is the wrapper."""
    return ("cd /storage/x/repo && { " + prefix + "setsid nohup bash -c "
            + shlex.quote(WRAPPER) + " run run.sh 3600 </dev/null >/dev/null 2>&1 & "
            "echo \"$!.$(date +%s)\"; }")


# --- registration and golden command strings --------------------------------

def test_ssh_is_registered_as_experimental_scheduler_without_aliases():
    a = adapter()
    assert a.name == "ssh"
    assert a.maturity == "experimental"
    assert a.transport == "ssh"
    assert a.needs_kerberos is True
    assert a.array_support is False
    assert a.concrete_ops == {}
    assert a.labels == {"submit": "SUBMIT_FAILED", "status": "STATUS_FAILED"}
    assert a.detail_fields == ()


def test_submit_default_command_golden():
    assert adapter().submit_command(spec()) == launch()


def test_submit_with_env_prefixes_env_assignments():
    assert adapter().submit_command(spec(env={"A": "1", "B": "x"})) == launch(
        "env A=1 B=x ")


def test_walltime_is_converted_to_seconds_for_timeout():
    assert adapter().submit_command(spec(walltime="00:02:05")).count(" run run.sh 125 ") == 1


def test_notice_is_printed_and_scratch_is_refused():
    a = adapter()
    assert a.notices(spec()) == [NOTICE]
    with pytest.raises(PolicyError, match="ssh scheduler has no scratch"):
        a.resource_args(spec(scratch_type="scratch_ssd", scratch_gb=5))


@pytest.mark.parametrize("stdout,job_id", [
    ("4242.1761400000\n", "4242.1761400000"),
    ("  4242.1761400000 \n", "4242.1761400000"),
])
def test_parse_submit_accepts_pid_dot_epoch(stdout, job_id):
    assert adapter().parse_submit(stdout) == job_id


@pytest.mark.parametrize("stdout", ["", "12345\n", "4242.x\n", "garbage\n"])
def test_parse_submit_refuses_anything_but_pid_dot_epoch(stdout):
    with pytest.raises(SubmitParseError):
        adapter().parse_submit(stdout)


def test_status_command_golden():
    assert adapter().status_command(JOB) == (
        "cd /storage/x/repo && if test -f run.x4242; then "
        "printf 'EXIT %s\\n' \"$(cat run.x4242)\"; "
        "elif kill -0 4242 2>/dev/null; then echo RUNNING; "
        "elif test -f run.c4242; then echo CANCELLED; "
        "else echo GONE; fi"
    )


def test_cancel_and_logs_golden():
    a = adapter()
    assert a.cancel_command(JOB) == (
        "cd /storage/x/repo && : > run.c4242 && pkill -TERM -s 4242"
    )
    assert a.log_paths(JOB) == ("run.o4242", "run.e4242")
    assert a.logs_command(JOB) == "cd /storage/x/repo && cat run.o4242 run.e4242 2>/dev/null"


# --- status parsing over the synthetic fixtures -----------------------------

STATUS_CASES = [
    ("exit-0.txt", "done", "0"),
    ("exit-3.txt", "failed", "3"),
    ("exit-124.txt", "failed", "124"),
    ("running.txt", "running", "-"),
    ("cancelled.txt", "cancelled", "-"),
    ("gone.txt", "failed", "-"),
    ("garbage.txt", None, "-"),
]


@pytest.mark.parametrize("fixture,state,code", STATUS_CASES)
def test_parse_status_over_every_fixture(fixture, state, code):
    status = adapter().parse_status(read(fixture), JOB)
    assert (status.state, status.exit_code, status.details) == (state, code, {})
    assert status.parse_error is (state is None)


# --- cmd_* wiring ------------------------------------------------------------

def test_cmd_submit_records_ssh_and_prints_notice(tmp_path, capsys):
    t, calls = recording([(0, "4242.1761400000\n", "")])
    assert submit(tmp_path, t) == 0
    assert calls[0][-1] == launch()
    out = capsys.readouterr().out
    assert NOTICE in out
    assert out.endswith("SUBMITTED: 4242.1761400000 attempt=1\n")
    [entry] = jobs.load_jobs(tmp_path)
    assert entry["scheduler"] == "ssh"
    assert entry["dir"] == "/storage/x/repo"
    assert entry["job_name"] == "run"


def test_ssh_submit_failure_uses_submit_failed_label(tmp_path, capsys):
    t, _ = recording([(255, "", "connection refused")])
    assert submit(tmp_path, t) == 1
    assert "SUBMIT_FAILED: connection refused" in capsys.readouterr().err
    assert jobs.load_jobs(tmp_path) == []


def test_status_running_then_done_updates_ledger(tmp_path, capsys):
    seed(tmp_path)
    t, calls = recording([(0, read("running.txt"), ""), (0, read("exit-0.txt"), "")])
    assert cli.cmd_status(REMOTE, t, "4242.1761400000", workspace=tmp_path) == 0
    assert jobs.load_jobs(tmp_path)[0]["state"] == "running"
    assert cli.cmd_status(REMOTE, t, "4242.1761400000", workspace=tmp_path) == 0
    assert jobs.load_jobs(tmp_path)[0]["state"] == "done"
    assert calls[0][-1] == adapter().status_command(JOB)
    assert capsys.readouterr().out == "STATE: running exit=-\nSTATE: done exit=0\n"


def test_status_without_ledger_dir_is_refused_before_ssh(tmp_path):
    jobs.save_jobs(tmp_path, [{
        "task": "run", "job_id": "4242.1761400000", "remote": "meta",
        "scheduler": "ssh", "state": "queued", "attempt": 1,
    }])
    t, calls = recording()
    with pytest.raises(PolicyError, match="ledger entry lacks dir/job_name"):
        cli.cmd_status(REMOTE, t, "4242.1761400000", workspace=tmp_path)
    assert calls == []


def test_cmd_logs_runs_the_ssh_logs_command(tmp_path, capsys):
    seed(tmp_path)
    t, calls = recording([(0, "hello\n", "")])
    assert cli.cmd_logs(REMOTE, t, "4242.1761400000", workspace=tmp_path) == 0
    assert calls[0][-1] == "cd /storage/x/repo && cat run.o4242 run.e4242 2>/dev/null"
    assert capsys.readouterr().out == "hello\n"
