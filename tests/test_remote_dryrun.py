"""Dry-run gate, DAG record, `schedulers` listing (M-E t3, plan section 6 t3).

Written before the implementation: each test describes the t3 behaviour
from reviews/m-e-plan.md sections 5.7 and 6.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from remote import jobs, policy, remote as cli, transport

CFG = """\
remotes:
  meta:
    host: skirit.metacentrum.cz
    user: testuser
    auth: kerberos
    scheduler: {scheduler}
    {extra}allowed_dirs:
      - /storage/x
    allowed_ops: [{ops}]
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

DEFAULT_OPS = "submit, status, cancel, logs"


def make(tmp_path, *, scheduler="pbs", ops=DEFAULT_OPS, replies=()):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    extra = "allow_experimental_scheduler: true\n    " if scheduler != "pbs" else ""
    (root / "config" / "remotes.yml").write_text(
        CFG.format(scheduler=scheduler, extra=extra, ops=ops))
    remote = policy.load_remote(root, "meta")
    calls = []
    queue = list(replies)

    def runner(argv):
        calls.append(argv)
        rc, out = queue.pop(0) if queue else (0, "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    t = transport.Transport(remote, runner=runner)
    ws = tmp_path / "ws"
    ws.mkdir()
    return remote, t, calls, ws


def dryrun_entry(state="done", **overrides):
    entry = {
        "task": "snk", "job_id": "102.meta-pbs", "remote": "meta",
        "dir": "/storage/x/repo", "script": "run.sh", "job_name": "run",
        "resources": {}, "environment": {"SCIEFLOW_DRYRUN": "1"},
        "scheduler": "pbs", "kind": "dryrun", "state": state, "attempt": 1,
        "submitted": "2026-10-25T00:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def seed(ws, entries):
    jobs.save_jobs(ws, list(entries))


# --- schedulers: listing needs no config and no SSH --------------------------

def test_schedulers_lists_every_adapter_without_loading_config(monkeypatch, capsys):
    def must_not_load(*args, **kwargs):
        raise AssertionError("schedulers must not read config/remotes.yml")

    monkeypatch.setattr(policy, "load_remote", must_not_load)
    assert cli.main(["schedulers"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "local experimental local submit=submit status=status cancel=cancel logs=logs",
        "pbs certified ssh submit=qsub status=qstat cancel=qdel logs=logs",
        "slurm experimental ssh submit=sbatch status=sacct cancel=scancel logs=logs",
        "ssh experimental ssh submit=submit status=status cancel=cancel logs=logs",
    ]


def test_schedulers_json_has_maturity_transport_and_aliases(monkeypatch, capsys):
    monkeypatch.setattr(policy, "load_remote", lambda *a, **k: 1 / 0)
    assert cli.main(["schedulers", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_name = {row["name"]: row for row in rows}
    assert sorted(by_name) == ["local", "pbs", "slurm", "ssh"]
    assert by_name["pbs"]["maturity"] == "certified"
    assert by_name["slurm"]["maturity"] == "experimental"
    assert by_name["slurm"]["ops"] == {
        "submit": "sbatch", "status": "sacct", "cancel": "scancel", "logs": "logs"}
    assert by_name["ssh"]["transport"] == "ssh"
    assert by_name["local"]["transport"] == "local"


# --- dryrun: composed submit and ledger kind ---------------------------------

def test_dryrun_pbs_composes_zero_gpu_no_scratch_with_dryrun_env(tmp_path):
    remote, t, calls, ws = make(tmp_path, replies=[(0, "102.meta-pbs\n")])
    rc = cli.cmd_dryrun(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                        task="snk", walltime="00:30:00", cpus=1, mem_gb=4,
                        queue="default", name=None)
    assert rc == 0
    assert calls[0][-1] == (
        "cd /storage/x/repo && qsub -N run -q default -l walltime=00:30:00 "
        "-l select=1:ncpus=1:mem=4gb -v SCIEFLOW_DRYRUN=1 run.sh")
    [entry] = jobs.load_jobs(ws)
    assert entry["kind"] == "dryrun"
    assert entry["scheduler"] == "pbs"
    assert entry["environment"] == {"SCIEFLOW_DRYRUN": "1"}


def test_dryrun_slurm_exports_dryrun_env_and_no_gpu_or_tmp(tmp_path):
    remote, t, calls, ws = make(tmp_path, scheduler="slurm",
                                replies=[(0, "12345\n")])
    assert cli.cmd_dryrun(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                          task="snk", walltime="00:30:00", cpus=1, mem_gb=4,
                          queue="default", name=None) == 0
    command = calls[0][-1]
    assert "--export=ALL,SCIEFLOW_DRYRUN=1" in command
    assert "--gres" not in command
    assert "--tmp" not in command


def test_dryrun_counts_toward_the_task_attempts(tmp_path):
    remote, t, calls, ws = make(tmp_path, replies=[(0, "102.meta-pbs\n"), (0, "103.meta-pbs\n")])
    for _ in range(2):
        assert cli.cmd_dryrun(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                              task="snk", walltime="00:30:00", cpus=1, mem_gb=4,
                              queue="default", name=None) == 0
    assert [e["attempt"] for e in jobs.load_jobs(ws)] == [1, 2]


def test_caller_supplied_dryrun_env_is_refused_before_ssh(tmp_path):
    remote, t, calls, ws = make(tmp_path)
    with pytest.raises(policy.PolicyError, match="SCIEFLOW_DRYRUN"):
        cli.cmd_dryrun(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                       task="snk", walltime="00:30:00", cpus=1, mem_gb=4,
                       queue="default", name=None,
                       environment_assignments=["SCIEFLOW_DRYRUN=0"])
    assert calls == []


def test_dryrun_without_submit_in_allowed_ops_is_refused_before_ssh(tmp_path):
    remote, t, calls, ws = make(tmp_path, ops="status, logs")
    with pytest.raises(policy.PolicyError, match="operation 'submit'"):
        cli.cmd_dryrun(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                       task="snk", walltime="00:30:00", cpus=1, mem_gb=4,
                       queue="default", name=None)
    assert calls == []


# --- dag: record the finished dry run's plan ---------------------------------

def test_dag_refuses_a_dryrun_that_is_not_done_before_ssh(tmp_path, capsys):
    remote, t, calls, ws = make(tmp_path)
    seed(ws, [dryrun_entry(state="running")])
    assert cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws) == 1
    assert "NOT_A_DONE_DRYRUN" in capsys.readouterr().err
    assert calls == []


def test_dag_refuses_a_real_job_before_ssh(tmp_path, capsys):
    remote, t, calls, ws = make(tmp_path)
    seed(ws, [dryrun_entry(kind=None)])
    entry = jobs.load_jobs(ws)[0]
    entry.pop("kind")
    seed(ws, [entry])
    assert cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws) == 1
    assert "NOT_A_DONE_DRYRUN" in capsys.readouterr().err
    assert calls == []


def test_dag_scheduler_mismatch_is_a_policy_refusal_before_ssh(tmp_path):
    remote, t, calls, ws = make(tmp_path)
    seed(ws, [dryrun_entry(scheduler="slurm")])
    with pytest.raises(policy.PolicyError, match="scheduler 'slurm'"):
        cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws)
    assert calls == []


def test_dag_records_exact_logs_atomically_with_hash(tmp_path, capsys):
    dag_text = "job 1: run.sh\njob 2: post.sh\n"
    remote, t, calls, ws = make(tmp_path, replies=[(0, dag_text)])
    seed(ws, [dryrun_entry()])
    assert cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws) == 0
    assert calls[0][-1] == "cd /storage/x/repo && cat run.o102 run.e102 2>/dev/null"
    dag_file = ws / "remote" / "dag-snk.txt"
    assert dag_file.read_text() == dag_text
    assert not (ws / "remote" / "dag-snk.txt.tmp").exists()
    digest = hashlib.sha256(dag_text.encode()).hexdigest()
    [entry] = jobs.load_jobs(ws)
    assert entry["dag_sha256"] == digest
    out = capsys.readouterr().out
    assert f"DAG_RECORDED: {dag_file} sha256={digest}" in out


def test_dag_output_over_the_limit_writes_nothing(tmp_path, capsys, monkeypatch):
    assert cli.DAG_MAX_BYTES == 10 * 1024 * 1024
    monkeypatch.setattr(cli, "DAG_MAX_BYTES", 10)
    remote, t, calls, ws = make(tmp_path, replies=[(0, "x" * 11)])
    seed(ws, [dryrun_entry()])
    assert cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws) == 1
    assert "DAG_TOO_LARGE" in capsys.readouterr().err
    assert not (ws / "remote" / "dag-snk.txt").exists()
    assert "dag_sha256" not in jobs.load_jobs(ws)[0]


def test_dag_checks_the_task_name_before_building_the_path(tmp_path):
    remote, t, calls, ws = make(tmp_path)
    seed(ws, [dryrun_entry(task="../evil")])
    with pytest.raises(policy.PolicyError, match="task name"):
        cli.cmd_dag(remote, t, "102.meta-pbs", workspace=ws)
    assert calls == []
    assert not (ws / "evil.txt").exists()


# --- submit --after-dryrun: refuse unless the recorded plan is intact --------

def submit_after(remote, t, ws):
    return cli.cmd_submit(remote, t, "/storage/x/repo", "run.sh", workspace=ws,
                          task="snk", walltime="01:00:00", cpus=1, mem_gb=4,
                          gpus=0, queue="default", name=None, after_dryrun=True)


def test_after_dryrun_refused_without_a_done_dryrun(tmp_path, capsys):
    remote, t, calls, ws = make(tmp_path)
    assert submit_after(remote, t, ws) == 1
    assert "NO_DAG" in capsys.readouterr().err
    assert calls == []


def test_after_dryrun_refused_without_a_recorded_dag(tmp_path, capsys):
    remote, t, calls, ws = make(tmp_path)
    seed(ws, [dryrun_entry()])
    assert submit_after(remote, t, ws) == 1
    assert "NO_DAG" in capsys.readouterr().err
    assert calls == []


def test_after_dryrun_refused_when_the_dag_file_was_edited(tmp_path, capsys):
    remote, t, calls, ws = make(tmp_path)
    (ws / "remote").mkdir()
    (ws / "remote" / "dag-snk.txt").write_text("edited plan\n")
    seed(ws, [dryrun_entry(dag_sha256=hashlib.sha256(b"original plan\n").hexdigest())])
    assert submit_after(remote, t, ws) == 1
    assert "NO_DAG" in capsys.readouterr().err
    assert calls == []


def test_after_dryrun_allowed_after_a_valid_dag(tmp_path):
    remote, t, calls, ws = make(tmp_path, replies=[(0, "103.meta-pbs\n")])
    text = b"job 1: run.sh\n"
    (ws / "remote").mkdir()
    (ws / "remote" / "dag-snk.txt").write_bytes(text)
    seed(ws, [dryrun_entry(dag_sha256=hashlib.sha256(text).hexdigest())])
    assert submit_after(remote, t, ws) == 0
    assert len(calls) == 1
    assert "-v" not in calls[0][-1] or "SCIEFLOW_DRYRUN" not in calls[0][-1]
