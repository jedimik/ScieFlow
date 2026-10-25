"""Slurm adapter (M-E t2): golden command strings, status parsing, cmd_* wiring.

Slurm is experimental (D19). Nothing here shows that a real cluster accepts
these strings. The fixtures are synthetic (tests/fixtures/scheduler/README.md).
RED first: written before scripts/remote/schedulers/slurm.py exists.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli, schedulers, transport
from remote.errors import PolicyError
from remote.schedulers.base import JobRef, JobSpec, SubmitParseError

FIXTURES = Path(__file__).parent / "fixtures" / "scheduler" / "slurm"

REMOTE = policy.Remote(
    name="meta", host="h", user="u", auth="kerberos", scheduler="slurm",
    allowed_dirs=["/storage/x"],
    allowed_ops=["check", "submit", "status", "cancel", "logs"],
    limits={"max_walltime": "24:00:00", "max_cpus": 16, "max_mem_gb": 64,
            "max_gpus": 2, "max_scratch_gb": 100,
            "scratch_types": ["scratch_ssd", "tmp"], "queues": ["default"],
            "max_concurrent_jobs": 4, "max_fix_attempts": 3},
)

JOB = JobRef(job_id="12345", dir="/storage/x/repo", job_name="run")


def adapter():
    return schedulers.get("slurm")


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
        "task": "run", "job_id": "12345", "remote": "meta",
        "dir": "/storage/x/repo", "script": "run.sh", "job_name": "run",
        "resources": {}, "environment": {}, "scheduler": "slurm",
        "state": state, "attempt": 1, "submitted": "2026-10-25T00:00:00+00:00",
    }])


# --- registration and golden command strings --------------------------------

def test_slurm_is_registered_as_experimental_ssh_scheduler():
    a = adapter()
    assert a.name == "slurm"
    assert a.maturity == "experimental"
    assert a.transport == "ssh"
    assert a.needs_kerberos is True
    assert a.array_support is True
    assert a.concrete_ops == {"submit": "sbatch", "status": "sacct",
                              "cancel": "scancel", "logs": "logs"}
    assert a.labels == {"submit": "SBATCH_FAILED", "status": "SACCT_FAILED"}
    assert a.detail_fields == ("Elapsed",)


def test_submit_default_command_golden():
    assert adapter().submit_command(spec()) == (
        "cd /storage/x/repo && sbatch --parsable -J run -p default "
        "-t 01:00:00 -N 1 -n 1 -c 1 --mem=4G -o slurm-%j.out -e slurm-%j.err run.sh"
    )


def test_submit_gpus_and_env_golden():
    assert adapter().submit_command(spec(gpus=2, env={"A": "1", "B": "x"})) == (
        "cd /storage/x/repo && sbatch --parsable -J run -p default "
        "-t 01:00:00 -N 1 -n 1 -c 1 --mem=4G --gres=gpu:2 "
        "-o slurm-%j.out -e slurm-%j.err --export=ALL,A=1,B=x run.sh"
    )


def test_submit_tmp_scratch_golden():
    assert adapter().submit_command(spec(scratch_type="tmp", scratch_gb=10)) == (
        "cd /storage/x/repo && sbatch --parsable -J run -p default "
        "-t 01:00:00 -N 1 -n 1 -c 1 --mem=4G --tmp=10G "
        "-o slurm-%j.out -e slurm-%j.err run.sh"
    )


def test_scratch_other_than_tmp_is_refused_by_the_adapter():
    with pytest.raises(PolicyError,
                       match=r"slurm supports scratch_type 'tmp' only \(--tmp\)"):
        adapter().resource_args(spec(scratch_type="scratch_ssd", scratch_gb=10))


def test_cmd_submit_scratch_refused_before_any_ssh(tmp_path):
    t, calls = recording()
    with pytest.raises(PolicyError, match="slurm supports scratch_type 'tmp' only"):
        submit(tmp_path, t, scratch_type="scratch_ssd", scratch_gb=10)
    assert calls == []


@pytest.mark.parametrize("stdout,job_id", [
    ("12345\n", "12345"),
    ("12345;cluster\n", "12345"),
    ("  12345  \n", "12345"),
])
def test_parse_submit_accepts_id_and_cluster_suffix(stdout, job_id):
    assert adapter().parse_submit(stdout) == job_id


@pytest.mark.parametrize("stdout", [
    "",
    "\n",
    "sbatch: error: Batch job submission failed\n",
    "12a45\n",
])
def test_parse_submit_refuses_output_without_a_numeric_id(stdout):
    with pytest.raises(SubmitParseError):
        adapter().parse_submit(stdout)


def test_status_command_golden_and_tolerant_of_aged_out_jobs():
    command = adapter().status_command(JOB)
    assert command == (
        "sacct -X -n -P -j 12345 --format=JobID,State,ExitCode,Elapsed 2>/dev/null || true; "
        "printf '%s\\n' '#squeue'; squeue -h -j 12345 -o '%i|%T' 2>/dev/null || true"
    )
    assert command.endswith("|| true")


def test_cancel_and_logs_golden():
    a = adapter()
    assert a.cancel_command(JOB) == "scancel 12345"
    assert a.log_paths(JOB) == ("slurm-12345.out", "slurm-12345.err")
    assert a.logs_command(JOB) == (
        "cd /storage/x/repo && cat slurm-12345.out slurm-12345.err 2>/dev/null"
    )


# --- status parsing over the synthetic fixtures -----------------------------

STATUS_CASES = [
    ("completed-0.txt", "done", "0", {"Elapsed": "00:01:02"}),
    ("completed-1.txt", "failed", "1", {"Elapsed": "00:00:05"}),
    ("failed.txt", "failed", "1", {"Elapsed": "00:00:09"}),
    ("timeout.txt", "failed", "0", {"Elapsed": "01:00:00"}),
    ("out-of-memory.txt", "failed", "0", {"Elapsed": "00:00:44"}),
    ("running.txt", "running", "-", {"Elapsed": "00:00:10"}),
    ("completing.txt", "running", "-", {"Elapsed": "00:00:10"}),
    ("pending-squeue-only.txt", "queued", "-", {}),
    ("cancelled-by.txt", "cancelled", "0", {"Elapsed": "00:00:03"}),
    ("both-empty.txt", "unknown", "-", {}),
    ("completed-squeue-only.txt", "unknown", "-", {}),
    ("other-job-only.txt", "unknown", "-", {}),
    ("unknown-state.txt", "unknown", "-", {"Elapsed": "00:00:01"}),
    ("missing-marker.txt", None, "-", {}),
    ("wrong-field-count.txt", None, "-", {}),
    ("bad-exit-code.txt", None, "-", {}),
]


@pytest.mark.parametrize("fixture,state,code,details", STATUS_CASES)
def test_parse_status_over_every_fixture(fixture, state, code, details):
    status = adapter().parse_status(read(fixture), JOB)
    assert (status.state, status.exit_code, status.details) == (state, code, details)
    assert status.parse_error is (state is None)


# --- cmd_* wiring ------------------------------------------------------------

def test_cmd_submit_records_slurm_and_prints_the_job(tmp_path, capsys):
    t, calls = recording([(0, "12345\n", "")])
    assert submit(tmp_path, t) == 0
    assert calls[0][-1] == adapter().submit_command(spec())
    assert capsys.readouterr().out == "SUBMITTED: 12345 attempt=1\n"
    [entry] = jobs.load_jobs(tmp_path)
    assert entry["scheduler"] == "slurm"
    assert entry["job_id"] == "12345"
    assert entry["dir"] == "/storage/x/repo"
    assert entry["job_name"] == "run"
    assert entry["state"] == "queued"


def test_sbatch_failure_is_labelled_and_records_nothing(tmp_path, capsys):
    t, _ = recording([(1, "", "boom")])
    assert submit(tmp_path, t) == 1
    assert "SBATCH_FAILED: boom" in capsys.readouterr().err
    assert jobs.load_jobs(tmp_path) == []


def test_unparseable_sbatch_output_records_nothing(tmp_path, capsys):
    t, _ = recording([(0, "sbatch: error: nope\n", "")])
    assert submit(tmp_path, t) == 1
    assert "SBATCH_FAILED:" in capsys.readouterr().err
    assert jobs.load_jobs(tmp_path) == []


def test_status_running_updates_ledger(tmp_path, capsys):
    seed(tmp_path)
    t, calls = recording([(0, read("running.txt"), "")])
    assert cli.cmd_status(REMOTE, t, "12345", workspace=tmp_path) == 0
    assert capsys.readouterr().out == "STATE: running exit=-\n"
    assert jobs.load_jobs(tmp_path)[0]["state"] == "running"
    assert calls[0][-1] == adapter().status_command(JOB)


def test_status_completed_prints_elapsed_with_details(tmp_path, capsys):
    seed(tmp_path)
    t, _ = recording([(0, read("completed-0.txt"), "")])
    assert cli.cmd_status(REMOTE, t, "12345", workspace=tmp_path, details=True) == 0
    out = capsys.readouterr().out
    assert out == "STATE: done exit=0\nDETAIL: Elapsed=00:01:02\n"
    assert jobs.load_jobs(tmp_path)[0]["state"] == "done"


def test_sacct_failure_is_sacct_failed_and_ledger_untouched(tmp_path, capsys):
    seed(tmp_path)
    t, _ = recording([(1, "", "sacct: slurmdbd down")])
    assert cli.cmd_status(REMOTE, t, "12345", workspace=tmp_path) == 1
    assert "SACCT_FAILED: sacct: slurmdbd down" in capsys.readouterr().err
    assert jobs.load_jobs(tmp_path)[0]["state"] == "queued"


def test_unparseable_status_leaves_the_ledger_untouched(tmp_path, capsys):
    seed(tmp_path)
    t, _ = recording([(0, read("missing-marker.txt"), "")])
    assert cli.cmd_status(REMOTE, t, "12345", workspace=tmp_path) == 1
    assert "UNPARSEABLE" in capsys.readouterr().err
    assert jobs.load_jobs(tmp_path)[0]["state"] == "queued"


def test_cmd_logs_runs_the_slurm_logs_command(tmp_path, capsys):
    seed(tmp_path)
    t, calls = recording([(0, "out text\n", "")])
    assert cli.cmd_logs(REMOTE, t, "12345", workspace=tmp_path) == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && cat slurm-12345.out slurm-12345.err 2>/dev/null"
    )
    assert capsys.readouterr().out == "out text\n"
