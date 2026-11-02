"""M-D3 t3: stage, push and submit (plan 5.5), the duplicate guard, remote.json validation and the core job
(plan 5.8). The PBS strings are the certified golden strings; the local-adapter smoke runs real jobs and
kills their sessions in teardown.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import importlib.util
from pathlib import Path

_SUPPORT = Path(__file__).with_name("remote_support.py")
_spec = importlib.util.spec_from_file_location("remote_support", _SUPPORT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)
from scieflow.core import jobs
from scieflow.core.project import Project
from scieflow.core.run import budget
from scieflow.core.service import ServiceError, cancel_job
from scieflow.experiments import remote_campaign
from scieflow.experiments.cli import experiment

CID = rs.CAMPAIGN_ID
R = rs.REMOTE_DIR


@pytest.fixture
def fake(monkeypatch):
    f = rs.FakeRemote()
    monkeypatch.setattr(remote_campaign, "RUNNER", f)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    return f


def sweep(p, *extra):
    args = ["sweep", "-c", str(p.campaign), "--experiments-dir", str(p.experiments),
            "--pipelines-dir", str(p.root / "pipelines"), *extra]
    return CliRunner().invoke(experiment, args)


def spent(p) -> int:
    return budget.read_budget(p.ws)["spent"]["experiment_runs"]


def bundle_dir(p):
    return p.ws / "scratch" / "remote" / CID


def test_pbs_per_run_golden_strings_three_runs(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=3)
    result = sweep(p, "--direct")
    assert result.exit_code == 0, result.output
    assert fake.calls[0] == ["klist", "-s"]
    ssh = fake.ssh_commands()
    assert "klist -s; then" in ssh[0]                       # the remote ticket check
    assert ssh[1] == f"mkdir -p -- {R}"
    rsyncs = [c for c in fake.calls if c[0] == "rsync"]
    assert rsyncs == [["rsync", "-az", "--partial", "--checksum",
                       f"{bundle_dir(p)}/", f"u@meta.example.org:{R}/"]]
    assert fake.qsubs() == [
        f"cd {R} && qsub -N sf-0 -q default -l walltime=01:00:00 "
        "-l select=1:ncpus=1:mem=4gb -v SCIEFLOW_TASK=0 job.sh",
        f"cd {R} && qsub -N sf-1 -q default -l walltime=01:00:00 "
        "-l select=1:ncpus=1:mem=4gb -v SCIEFLOW_TASK=1 job.sh",
        f"cd {R} && qsub -N sf-2 -q default -l walltime=01:00:00 "
        "-l select=1:ncpus=1:mem=4gb -v SCIEFLOW_TASK=2 job.sh",
    ]
    from remote import jobs as rjobs
    ledger = rjobs.load_jobs(p.ws)
    assert [e["task"] for e in ledger] == [f"{CID}.0", f"{CID}.1", f"{CID}.2"]
    assert {e["scheduler"] for e in ledger} == {"pbs"}
    state = rs.read_remote_json(p.campaign_dir)
    assert [t["job_id"] for t in state["tasks"]] == [
        "101.meta.example.org", "102.meta.example.org", "103.meta.example.org"]
    assert state["budget_recorded"] == 3 and state["state"] == "running"
    assert spent(p) == 3
    recorded = [e for e in rs.read_events(p.ws) if e["type"] == "budget.recorded"]
    assert len(recorded) == 1
    assert "submitted 3 of 3 runs on meta" in result.output
    assert "scieflow experiment watch" in result.output


def test_concurrency_ceiling_submits_the_rest_later(fake, tmp_path, monkeypatch):
    p = rs.make_project(tmp_path, n_gains=5,
                        remote=rs.pbs_remote(limits=dict(rs.PBS_LIMITS, max_concurrent_jobs=2)))
    result = sweep(p, "--direct")
    assert result.exit_code == 0, result.output
    assert len(fake.qsubs()) == 2
    state = rs.read_remote_json(p.campaign_dir)
    assert [t["state"] for t in state["tasks"]] == ["submitted", "submitted",
                                                    "pending", "pending", "pending"]
    assert spent(p) == 5                                    # N recorded once, at the first job
    assert "submitted 2 of 5" in result.output


def test_a_failed_submit_marks_only_that_task(tmp_path, monkeypatch):
    fake = rs.FakeRemote(qsub_rc=[0, 1, 0])
    monkeypatch.setattr(remote_campaign, "RUNNER", fake)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    p = rs.make_project(tmp_path, n_gains=3)
    result = sweep(p, "--direct")
    assert result.exit_code == 0, result.output
    state = rs.read_remote_json(p.campaign_dir)
    assert [t["state"] for t in state["tasks"]] == ["submitted", "submit-failed", "submitted"]
    assert state["tasks"][1]["reason"] == "submit-failed"
    assert spent(p) == 3


def test_array_is_one_job_and_counts_n_runs(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=3, jobs="array")
    result = sweep(p, "--direct")
    assert result.exit_code == 0, result.output
    assert fake.qsubs() == [
        f"cd {R} && qsub -N sf-array -q default -l walltime=01:00:00 "
        "-l select=1:ncpus=1:mem=4gb job.sh"]
    from remote import jobs as rjobs
    ledger = rjobs.load_jobs(p.ws)
    assert len(ledger) == 1 and ledger[0]["task"] == f"{CID}.array"
    state = rs.read_remote_json(p.campaign_dir)
    assert {t["job_id"] for t in state["tasks"]} == {"101.meta.example.org"}
    assert spent(p) == 3


def test_push_failure_stays_staged_and_spends_nothing(tmp_path, monkeypatch):
    fake = rs.FakeRemote(rsync_rc=1)
    monkeypatch.setattr(remote_campaign, "RUNNER", fake)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    p = rs.make_project(tmp_path, n_gains=2)
    result = sweep(p, "--direct")
    assert result.exit_code == 1
    assert rs.read_remote_json(p.campaign_dir)["state"] == "staged"
    assert fake.qsubs() == []
    assert spent(p) == 0


def test_duplicate_guard_adopts_a_job_the_ledger_already_has(tmp_path, monkeypatch):
    failing = rs.FakeRemote(rsync_rc=1)
    monkeypatch.setattr(remote_campaign, "RUNNER", failing)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p, "--direct").exit_code == 1
    # Simulated crash: the scheduler accepted task 0, but remote.json never got the id.
    from remote import jobs as rjobs
    ledger = rjobs.load_jobs(p.ws)
    rjobs.record_submit(ledger, task=f"{CID}.0", job_id="777.meta.example.org",
                        remote_name="meta", remote_dir=R, script="job.sh",
                        resources={}, job_name="sf-0", scheduler="pbs")
    rjobs.save_jobs(p.ws, ledger)
    ok = rs.FakeRemote()
    result = remote_campaign.resume_submission(p.campaign_dir, p.ws, runner=ok)
    assert not any("SCIEFLOW_TASK=0" in q for q in ok.qsubs())
    assert any("SCIEFLOW_TASK=1" in q for q in ok.qsubs())
    state = rs.read_remote_json(p.campaign_dir)
    assert state["tasks"][0]["job_id"] == "777.meta.example.org"
    assert state["tasks"][0]["state"] == "submitted"
    assert result.submitted == 2


@pytest.mark.parametrize("field,value", [
    ("remote_dir", "/data/x/other-20261102T000000Z"),
    ("remote_dir", "/etc/passwd"),
    ("version", 2),
])
def test_remote_json_is_revalidated_before_any_transport_call(tmp_path, monkeypatch, field, value):
    failing = rs.FakeRemote(rsync_rc=1)
    monkeypatch.setattr(remote_campaign, "RUNNER", failing)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p, "--direct").exit_code == 1
    import json
    path = p.campaign_dir / "remote.json"
    doc = json.loads(path.read_text())
    doc[field] = value
    path.write_text(json.dumps(doc))
    ok = rs.FakeRemote()
    with pytest.raises(remote_campaign.RemoteError) as caught:
        remote_campaign.resume_submission(p.campaign_dir, p.ws, runner=ok)
    assert caught.value.rule == "remote-state-invalid"
    assert field in caught.value.message
    assert ok.calls == []


def test_core_job_is_a_remote_job_that_cannot_be_cancelled(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p, "--direct").exit_code == 0
    project = Project(p.root)
    listed = [j for j in jobs.list_jobs(project, p.ws) if j.kind == "remote"]
    assert len(listed) == 1
    job = listed[0]
    assert job.state == "running" and job.pid is None
    assert job.label == f"gain-sweep on meta"
    types = [e["type"] for e in rs.read_events(p.ws) if e["data"].get("job") == job.id]
    assert types == ["job.queued", "job.started"]
    with pytest.raises(ServiceError, match="cannot be cancelled from ScieFlow"):
        cancel_job(project, job.id)
    after = jobs.find(project, job.id)
    assert after.state == "running"
    assert not [e for e in rs.read_events(p.ws) if e["type"] == "job.cancelled"]
    assert jobs.reconcile(project, p.ws) == []
    assert jobs.find(project, job.id).state == "running"


def test_local_adapter_smoke_submits_real_jobs(tmp_path):
    base = tmp_path / "remote_root"
    base.mkdir()
    p = rs.make_project(tmp_path, n_gains=2, remote=rs.local_remote(str(base)),
                        campaign_remote={"name": "meta", "dir": str(base)})
    ids: list[str] = []
    try:
        result = sweep(p, "--direct")
        from remote import jobs as rjobs
        ids = [e["job_id"] for e in rjobs.load_jobs(p.ws)]
        assert result.exit_code == 0, result.output
        assert len(ids) == 2
        assert all(__import__("re").fullmatch(r"\d+\.\d+", i) for i in ids)
        assert spent(p) == 2
    finally:
        rs.kill_local_sessions(ids)
