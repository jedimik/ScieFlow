"""Scheduler seam: registry, op aliases, experimental gate, adapter wiring (M-E t1).

RED first: these tests describe the seam from reviews/m-e-plan.md section 5.
They are written before scripts/remote/schedulers/ exists.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from remote import errors, jobs, policy, remote as cli, schedulers, transport

BASE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"],
    allowed_ops=["qsub", "qstat", "logs"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 1, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd"], "queues": ["default"],
            "max_concurrent_jobs": 4, "max_fix_attempts": 3},
)

CFG = """\
remotes:
  meta:
    host: skirit.metacentrum.cz
    user: testuser
    auth: kerberos
    scheduler: {scheduler}
    {extra}allowed_dirs:
      - /storage/x
    allowed_ops: [check, qsub, qstat, logs]
    limits:
      max_walltime: "24:00:00"
      max_cpus: 16
      max_mem_gb: 64
      max_gpus: 1
      max_scratch_gb: 100
      scratch_types: [scratch_ssd]
      queues: [default]
      max_concurrent_jobs: 4
      max_fix_attempts: 3
"""


def write_root(tmp_path, scheduler="pbs", extra=""):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "remotes.yml").write_text(
        CFG.format(scheduler=scheduler, extra=extra))
    return root


def counting_transport(replies=()):
    calls = []
    queue = list(replies)

    def runner(argv):
        calls.append(argv)
        rc, out, err = queue.pop(0) if queue else (0, "", "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return transport.Transport(BASE, runner=runner), calls


# --- registry and aliases ---------------------------------------------------

def test_pbs_is_registered_as_certified_ssh_scheduler():
    adapter = schedulers.REGISTRY["pbs"]
    assert adapter.name == "pbs"
    assert adapter.maturity == "certified"
    assert adapter.transport == "ssh"
    assert adapter.concrete_ops == {
        "submit": "qsub", "status": "qstat", "cancel": "qdel", "logs": "logs",
    }


def test_schedulers_get_and_names():
    assert schedulers.get("pbs") is schedulers.REGISTRY["pbs"]
    assert "pbs" in schedulers.names()


def test_policy_reexports_the_errors_module_exception():
    assert policy.PolicyError is errors.PolicyError


def test_check_scheduler_op_accepts_abstract_or_concrete_name():
    policy.check_scheduler_op(replace(BASE, allowed_ops=["qsub"]), "submit")
    policy.check_scheduler_op(replace(BASE, allowed_ops=["submit"]), "submit")
    policy.check_scheduler_op(replace(BASE, allowed_ops=["qstat"]), "status")
    policy.check_scheduler_op(replace(BASE, allowed_ops=["status"]), "status")


def test_check_scheduler_op_refuses_missing_op_and_names_the_alias():
    with pytest.raises(policy.PolicyError,
                       match=r"operation 'submit' \(qsub\) not in allowed_ops "
                             r"for remote 'meta' \(allowed: qstat\)"):
        policy.check_scheduler_op(replace(BASE, allowed_ops=["qstat"]), "submit")


def test_check_scheduler_op_refuses_unknown_abstract_op():
    with pytest.raises(policy.PolicyError):
        policy.check_scheduler_op(BASE, "frobnicate")


# --- experimental gate, read at call time ----------------------------------

@pytest.fixture
def experimental_slurm(monkeypatch):
    fake = SimpleNamespace(
        name="slurm", maturity="experimental", transport="ssh",
        concrete_ops={"submit": "sbatch"},
        validate_remote=lambda entry: None,
    )
    monkeypatch.setitem(schedulers.REGISTRY, "slurm", fake)
    return fake


def test_experimental_scheduler_refused_without_opt_in(tmp_path, experimental_slurm):
    root = write_root(tmp_path, scheduler="slurm")
    with pytest.raises(policy.PolicyError) as exc:
        policy.load_remote(root, "meta")
    assert str(exc.value) == (
        "scheduler 'slurm' is experimental (not yet run against a real cluster); "
        "set allow_experimental_scheduler: true on remote 'meta' in "
        "config/remotes.yml to use it"
    )


def test_experimental_scheduler_loads_with_opt_in(tmp_path, experimental_slurm):
    root = write_root(tmp_path, scheduler="slurm",
                      extra="allow_experimental_scheduler: true\n    ")
    remote = policy.load_remote(root, "meta")
    assert remote.scheduler == "slurm"
    assert remote.allow_experimental_scheduler is True


def test_non_boolean_opt_in_is_refused(tmp_path, experimental_slurm):
    root = write_root(tmp_path, scheduler="slurm",
                      extra='allow_experimental_scheduler: "yes"\n    ')
    with pytest.raises(policy.PolicyError, match="allow_experimental_scheduler"):
        policy.load_remote(root, "meta")


def test_remote_defaults_to_no_experimental_opt_in():
    assert BASE.allow_experimental_scheduler is False


# --- commands go through the adapter ----------------------------------------

class Recording:
    """Delegates to the real PBS adapter and counts each adapter call."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return call


@pytest.fixture
def recording_pbs(monkeypatch):
    rec = Recording(schedulers.REGISTRY["pbs"])
    monkeypatch.setitem(schedulers.REGISTRY, "pbs", rec)
    return rec


def test_submit_status_logs_each_call_the_adapter_once(tmp_path, recording_pbs):
    t, calls = counting_transport([
        (0, "101.meta-pbs\n", ""),
        (0, "    job_state = R\n", ""),
        (0, "out\n", ""),
    ])
    assert cli.cmd_submit(BASE, t, "/storage/x/repo", "run.sh", workspace=tmp_path,
                          task="run", walltime="01:00:00", cpus=1, mem_gb=1,
                          gpus=0, queue="default", name=None) == 0
    assert recording_pbs.calls.count("submit_command") == 1
    assert cli.cmd_status(BASE, t, "101.meta-pbs", workspace=tmp_path) == 0
    assert recording_pbs.calls.count("status_command") == 1
    assert recording_pbs.calls.count("parse_status") == 1
    assert cli.cmd_logs(BASE, t, "101.meta-pbs", workspace=tmp_path) == 0
    assert recording_pbs.calls.count("logs_command") == 1
    assert len(calls) == 3


def test_submit_records_the_scheduler_name(tmp_path):
    t, _ = counting_transport([(0, "101.meta-pbs\n", "")])
    cli.cmd_submit(BASE, t, "/storage/x/repo", "run.sh", workspace=tmp_path,
                   task="run", walltime="01:00:00", cpus=1, mem_gb=1, gpus=0,
                   queue="default", name=None)
    assert jobs.load_jobs(tmp_path)[0]["scheduler"] == "pbs"


# --- ledger integrity before any SSH (M-E-D5, M-E-D6) ----------------------

def test_status_of_unknown_job_is_refused_before_ssh(tmp_path, capsys):
    t, calls = counting_transport()
    assert cli.cmd_status(BASE, t, "404.meta", workspace=tmp_path) == 1
    assert calls == []
    assert "UNKNOWN_JOB" in capsys.readouterr().err


def test_logs_of_unknown_job_is_refused_before_ssh(tmp_path, capsys):
    t, calls = counting_transport()
    assert cli.cmd_logs(BASE, t, "404.meta", workspace=tmp_path) == 1
    assert calls == []
    assert "UNKNOWN_JOB" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["status", "logs"])
def test_entry_from_another_scheduler_is_a_policy_refusal(tmp_path, command):
    jobs.save_jobs(tmp_path, [{
        "task": "t", "job_id": "5.meta", "remote": "meta", "dir": "/storage/x",
        "script": "s.sh", "job_name": "s", "state": "queued", "attempt": 1,
        "scheduler": "slurm",
    }])
    t, calls = counting_transport()
    with pytest.raises(policy.PolicyError):
        if command == "status":
            cli.cmd_status(BASE, t, "5.meta", workspace=tmp_path)
        else:
            cli.cmd_logs(BASE, t, "5.meta", workspace=tmp_path)
    assert calls == []


def test_legacy_ledger_entry_without_scheduler_is_treated_as_pbs(tmp_path, capsys):
    jobs.save_jobs(tmp_path, [{
        "task": "t", "job_id": "7.meta", "remote": "meta", "state": "queued",
        "attempt": 1,
    }])
    t, calls = counting_transport([(0, "    job_state = R\n", "")])
    assert cli.cmd_status(BASE, t, "7.meta", workspace=tmp_path) == 0
    assert calls[0][-1] == "qstat -xf 7.meta"
    assert capsys.readouterr().out == "STATE: running exit=-\n"


# --- authorise before SSH, per command (review t1r F1) ----------------------

def _ledger_entry(**overrides):
    entry = {
        "task": "t", "job_id": "5.meta", "remote": "meta", "dir": "/storage/x",
        "script": "s.sh", "job_name": "s", "state": "queued", "attempt": 1,
        "scheduler": "pbs",
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    "command, abstract, alias",
    [("submit", "submit", "qsub"), ("status", "status", "qstat"),
     ("logs", "logs", "logs")],
)
def test_op_missing_from_allowed_ops_is_refused_before_ssh(
        tmp_path, command, abstract, alias):
    jobs.save_jobs(tmp_path, [_ledger_entry()])
    restricted = replace(BASE, allowed_ops=["check"])
    t, calls = counting_transport()
    with pytest.raises(policy.PolicyError,
                       match=rf"operation '{abstract}' \({alias}\) not in allowed_ops"):
        if command == "submit":
            cli.cmd_submit(restricted, t, "/storage/x/repo", "run.sh",
                           workspace=tmp_path, task="run", walltime="01:00:00",
                           cpus=1, mem_gb=1, gpus=0, queue="default", name=None)
        elif command == "status":
            cli.cmd_status(restricted, t, "5.meta", workspace=tmp_path)
        else:
            cli.cmd_logs(restricted, t, "5.meta", workspace=tmp_path)
    assert calls == []


@pytest.mark.parametrize(
    "entry_overrides",
    [
        {"dir": "/etc"},
        {"dir": "/storage/x/../../etc"},
        {"job_name": "a;touch x"},
        {"scheduler": 5},
    ],
    ids=["dir-outside-allowed", "dir-traversal", "unsafe-job-name", "non-string-scheduler"],
)
def test_tampered_ledger_entry_is_refused_by_logs_before_ssh(tmp_path, entry_overrides):
    jobs.save_jobs(tmp_path, [_ledger_entry(**entry_overrides)])
    t, calls = counting_transport()
    with pytest.raises(policy.PolicyError):
        cli.cmd_logs(BASE, t, "5.meta", workspace=tmp_path)
    assert calls == []


def test_unsafe_job_id_is_refused_by_logs_before_ssh(tmp_path):
    jobs.save_jobs(tmp_path, [_ledger_entry()])
    t, calls = counting_transport()
    with pytest.raises(policy.PolicyError, match="unsafe job id"):
        cli.cmd_logs(BASE, t, "5;rm.meta", workspace=tmp_path)
    assert calls == []


# --- status re-validates the ledger, as logs does (M-E t2 fix, review t2r-r1 F1) ---

@pytest.mark.parametrize("scheduler", ["ssh", "local", "slurm"])
@pytest.mark.parametrize(
    "entry_overrides",
    [
        {"dir": "/etc"},
        {"dir": "/storage/x/../../etc"},
        {"job_name": "a;touch x"},
        {"job_name": "../x"},
    ],
    ids=["dir-outside-allowed", "dir-traversal", "unsafe-job-name", "job-name-with-slash"],
)
def test_tampered_ledger_entry_is_refused_by_status_before_ssh(
        tmp_path, scheduler, entry_overrides):
    # Each id is valid for its adapter, so the refusal under test is the tamper check.
    job_id = "12345" if scheduler == "slurm" else "5.1761400000"
    remote = replace(BASE, scheduler=scheduler,
                     allowed_ops=["check", "submit", "status", "cancel", "logs"])
    entry = _ledger_entry(job_id=job_id, **entry_overrides)
    entry["scheduler"] = scheduler
    jobs.save_jobs(tmp_path, [entry])
    t, calls = counting_transport()
    with pytest.raises(policy.PolicyError,
                       match=r"outside allowed_dirs|unsafe (remote path|job name)"):
        cli.cmd_status(remote, t, job_id, workspace=tmp_path)
    assert calls == []


# --- walltime conversion: adapters and policy must agree (M-E t2 fix, review A2) ---

@pytest.mark.parametrize("walltime", ["00:00:00", "00:02:05", "01:00:00", "24:00:00", "100:00:00"])
def test_walltime_conversion_matches_policy(walltime):
    from remote.schedulers.base import walltime_to_seconds
    assert walltime_to_seconds(walltime) == policy.walltime_seconds(walltime)


@pytest.mark.parametrize("walltime", ["1:00", "01:02:03:04", "aa:00:00", ""])
def test_walltime_refusal_matches_policy(walltime):
    from remote.schedulers.base import walltime_to_seconds
    with pytest.raises(errors.PolicyError) as adapter_error:
        walltime_to_seconds(walltime)
    with pytest.raises(errors.PolicyError) as policy_error:
        policy.walltime_seconds(walltime)
    assert str(adapter_error.value) == str(policy_error.value)
