"""Contract suite over all four scheduler adapters (M-E t2, plan section 6 step 5-6).

Each adapter gets the same behaviour checks: submit then status walks to
done, failed, cancelled and garbage; metacharacters are refused before the
first transport call; the concurrency and attempt ceilings are identical;
LocalTransport and cmd_check local behave as designed.
RED first: written before the slurm, ssh and local adapters, LocalTransport
and for_remote exist.
"""

import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli, schedulers, transport
from remote.errors import PolicyError
from remote.schedulers.base import JobRef

FIXTURES = Path(__file__).parent / "fixtures" / "scheduler"

LIMITS = {"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
          "max_gpus": 2, "max_scratch_gb": 100,
          "scratch_types": ["tmp", "scratch_ssd"], "queues": ["default"],
          "max_concurrent_jobs": 4, "max_fix_attempts": 3}

# Per adapter: the remote's transport identity, the submit stdout, the job id,
# the ordered status fixtures and their expected states, and the garbage case.
ADAPTERS = {
    "pbs": dict(
        host="h", user="u", auth="kerberos", job_id="101.meta-pbs",
        submit="101.meta-pbs\n",
        sequence=[("pbs/qstat-Q.txt", "queued"), ("pbs/qstat-R.txt", "running"),
                  ("pbs/qstat-F-0.txt", "done")],
        failure=("pbs/qstat-F-1.txt", "failed"),
        cancelled=None,
        garbage="pbs/garbage.txt"),
    "slurm": dict(
        host="h", user="u", auth="kerberos", job_id="12345", submit="12345\n",
        sequence=[("slurm/pending-squeue-only.txt", "queued"),
                  ("slurm/running.txt", "running"),
                  ("slurm/completed-0.txt", "done")],
        failure=("slurm/failed.txt", "failed"),
        cancelled=("slurm/cancelled-by.txt", "cancelled"),
        garbage="slurm/missing-marker.txt"),
    "ssh": dict(
        host="h", user="u", auth="kerberos", job_id="4242.1761400000",
        submit="4242.1761400000\n",
        sequence=[("ssh/running.txt", "running"), ("ssh/exit-0.txt", "done")],
        failure=("ssh/exit-3.txt", "failed"),
        cancelled=("ssh/cancelled.txt", "cancelled"),
        garbage="ssh/garbage.txt"),
    "local": dict(
        host="localhost", user="local", auth="none", job_id="4242.1761400000",
        submit="4242.1761400000\n",
        sequence=[("ssh/running.txt", "running"), ("ssh/exit-0.txt", "done")],
        failure=("ssh/exit-3.txt", "failed"),
        cancelled=("ssh/cancelled.txt", "cancelled"),
        garbage="ssh/garbage.txt"),
}


def remote_for(name, **overrides):
    cfg = ADAPTERS[name]
    values = dict(name="meta", host=cfg["host"], user=cfg["user"], auth=cfg["auth"],
                  scheduler=name, allowed_dirs=["/storage/x"],
                  allowed_ops=["check", "submit", "status", "cancel", "logs", "fetch"],
                  limits=dict(LIMITS))
    values.update(overrides)
    return policy.Remote(**values)


def make(name, replies=(), remote=None):
    remote = remote or remote_for(name)
    calls = []
    queue = list(replies)

    def runner(argv):
        calls.append(argv)
        rc, out, err = queue.pop(0) if queue else (0, "", "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return remote, transport.for_remote(remote, runner=runner), calls


def read(name):
    return (FIXTURES / name).read_text()


def submit(tmp_path, remote, t, **overrides):
    kwargs = dict(workspace=tmp_path, task="run", walltime="01:00:00", cpus=1,
                  mem_gb=4, gpus=0, queue="default", name=None)
    kwargs.update(overrides)
    return cli.cmd_submit(remote, t, "/storage/x/repo", "run.sh", **kwargs)


def ledger_state(tmp_path):
    return jobs.load_jobs(tmp_path)[0]["state"]


# --- lifecycle --------------------------------------------------------------

@pytest.mark.parametrize("name", list(ADAPTERS))
def test_submit_then_status_walks_to_done(tmp_path, name, capsys):
    cfg = ADAPTERS[name]
    replies = [(0, cfg["submit"], "")] + [(0, read(f), "") for f, _ in cfg["sequence"]]
    remote, t, calls = make(name, replies)
    assert submit(tmp_path, remote, t) == 0
    for _, expected in cfg["sequence"]:
        assert cli.cmd_status(remote, t, cfg["job_id"], workspace=tmp_path) == 0
        assert ledger_state(tmp_path) == expected
    assert len(calls) == 1 + len(cfg["sequence"])
    assert jobs.load_jobs(tmp_path)[0]["job_id"] == cfg["job_id"]


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_failed_terminal_state(tmp_path, name, capsys):
    cfg = ADAPTERS[name]
    fixture, expected = cfg["failure"]
    remote, t, _ = make(name, [(0, cfg["submit"], ""), (0, read(fixture), "")])
    assert submit(tmp_path, remote, t) == 0
    assert cli.cmd_status(remote, t, cfg["job_id"], workspace=tmp_path) == 0
    assert ledger_state(tmp_path) == expected


@pytest.mark.parametrize("name", [n for n in ADAPTERS if ADAPTERS[n]["cancelled"]])
def test_cancelled_terminal_state(tmp_path, name, capsys):
    cfg = ADAPTERS[name]
    fixture, expected = cfg["cancelled"]
    remote, t, _ = make(name, [(0, cfg["submit"], ""), (0, read(fixture), "")])
    assert submit(tmp_path, remote, t) == 0
    assert cli.cmd_status(remote, t, cfg["job_id"], workspace=tmp_path) == 0
    assert ledger_state(tmp_path) == expected


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_malformed_output_is_unparseable_and_never_done(tmp_path, name, capsys):
    cfg = ADAPTERS[name]
    remote, t, _ = make(name, [(0, cfg["submit"], ""), (0, read(cfg["garbage"]), "")])
    assert submit(tmp_path, remote, t) == 0
    assert cli.cmd_status(remote, t, cfg["job_id"], workspace=tmp_path) == 1
    assert "UNPARSEABLE" in capsys.readouterr().err
    assert ledger_state(tmp_path) == "queued"


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_job_not_in_ledger_is_unknown_job_with_no_transport(tmp_path, name, capsys):
    cfg = ADAPTERS[name]
    remote, t, calls = make(name)
    assert cli.cmd_status(remote, t, cfg["job_id"], workspace=tmp_path) == 1
    assert "UNKNOWN_JOB" in capsys.readouterr().err
    assert calls == []


# --- authorisation and quoting before any transport call -------------------

@pytest.mark.parametrize("name", list(ADAPTERS))
@pytest.mark.parametrize("field", ["dir", "script", "task", "name", "env"])
def test_metacharacters_are_refused_before_the_first_transport_call(tmp_path, name, field):
    remote, t, calls = make(name)
    kwargs = {}
    dir_, script = "/storage/x/repo", "run.sh"
    if field == "dir":
        dir_ = "/storage/x/repo;rm -rf x"
    elif field == "script":
        script = "run.sh;rm"
    elif field == "task":
        kwargs["task"] = "t;x"
    elif field == "name":
        kwargs["name"] = "n;x"
    elif field == "env":
        kwargs["environment_assignments"] = ["A=1;rm"]
    kwargs.setdefault("workspace", tmp_path)
    kwargs.setdefault("task", "run")
    with pytest.raises(PolicyError):
        cli.cmd_submit(remote, t, dir_, script, walltime="01:00:00", cpus=1, mem_gb=4,
                       gpus=0, queue="default", name=kwargs.pop("name", None), **kwargs)
    assert calls == []


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_unsafe_job_id_is_refused_before_the_first_transport_call(tmp_path, name):
    remote, t, calls = make(name)
    with pytest.raises(PolicyError):
        cli.cmd_status(remote, t, "1;rm", workspace=tmp_path)
    assert calls == []


# --- ceilings are identical for every adapter ------------------------------

@pytest.mark.parametrize("name", list(ADAPTERS))
def test_concurrency_ceiling_refused_before_any_ssh(tmp_path, name, capsys):
    remote = remote_for(name, limits={**LIMITS, "max_concurrent_jobs": 1})
    jobs.save_jobs(tmp_path, [{"task": "other", "job_id": "9.x", "remote": "meta",
                               "state": "running", "attempt": 1}])
    remote, t, calls = make(name, remote=remote)
    assert submit(tmp_path, remote, t) == 4
    assert "LIMIT" in capsys.readouterr().out
    assert calls == []


@pytest.mark.parametrize("name", list(ADAPTERS))
def test_attempt_ceiling_refused_before_any_ssh(tmp_path, name, capsys):
    remote = remote_for(name, limits={**LIMITS, "max_fix_attempts": 0})
    jobs.save_jobs(tmp_path, [{"task": "run", "job_id": "1.x", "remote": "meta",
                               "state": "done", "attempt": 1}])
    remote, t, calls = make(name, remote=remote)
    assert submit(tmp_path, remote, t) == 4
    assert "LIMIT" in capsys.readouterr().out
    assert calls == []


# --- command text ------------------------------------------------------------

@pytest.mark.parametrize("name", list(ADAPTERS))
def test_cancel_and_log_names_are_token_safe(name):
    adapter = schedulers.get(name)
    job = JobRef(job_id=ADAPTERS[name]["job_id"], dir="/storage/x/repo", job_name="run")
    cancel = adapter.cancel_command(job)
    # Adapters address the job by its id's first component (the pid for ssh/local).
    assert ADAPTERS[name]["job_id"].split(".")[0] in cancel
    assert shlex.split(cancel)
    for path in adapter.log_paths(job):
        assert re.fullmatch(r"[\w.\-]+", path), path


# --- transport and local check -----------------------------------------------

def test_for_remote_selects_the_transport_from_the_adapter():
    local = transport.for_remote(remote_for("local"), runner=lambda argv: None)
    assert isinstance(local, transport.LocalTransport)
    ssh = transport.for_remote(remote_for("pbs"), runner=lambda argv: None)
    assert isinstance(ssh, transport.Transport)
    assert not isinstance(ssh, transport.LocalTransport)


def test_local_transport_argv_is_golden(tmp_path):
    calls = []

    def runner(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    t = transport.LocalTransport(remote_for("local"), runner=runner)
    assert t.target == "localhost"
    t.ssh("cd /x && ls")
    assert calls[-1] == ["bash", "-c", "cd /x && ls"]
    t.local(["klist", "-s"])
    assert calls[-1] == ["klist", "-s"]
    t.rsync_from("/x/results/", "/ws/data/")
    assert calls[-1] == ["rsync", "-az", "/x/results/", "/ws/data/"]
    source = tmp_path / "a.txt"
    source.write_text("x")
    t.rsync_to(str(source), "/x/dest")
    assert calls[-1] == ["rsync", "-az", "--partial", "--append-verify",
                         str(source), "/x/dest/"]


def test_check_on_local_is_ok_without_klist_or_ssh(capsys):
    remote, t, calls = make("local")
    assert cli.cmd_check(remote, t) == 0
    assert capsys.readouterr().out == "OK\n"
    assert calls == []
