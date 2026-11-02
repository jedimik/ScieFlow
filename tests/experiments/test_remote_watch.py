"""M-D3 t4: `scieflow experiment watch` — status, fetch, transfer check, finalize, results (plan 5.7-5.9).

The fake PBS transport answers `qstat` from tests/fixtures/scheduler/pbs and "copies" fetched run
folders out of a local stand-in for the remote filesystem. No test reaches a real host; the real
`local` adapter is exercised in test_remote_e2e_local.py.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

_SUPPORT = Path(__file__).with_name("remote_support.py")
_spec = importlib.util.spec_from_file_location("remote_support", _SUPPORT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

from scieflow.core import jobs  # noqa: E402
from scieflow.core.project import Project  # noqa: E402
from scieflow.core.run import budget  # noqa: E402
from scieflow.experiments import manifest, remote_campaign  # noqa: E402
from scieflow.experiments.campaign import Campaign  # noqa: E402
from scieflow.experiments.cli import experiment  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "scheduler" / "pbs"
CID = rs.CAMPAIGN_ID
R = rs.REMOTE_DIR
HOST = "meta.example.org"


def job_id(k: int) -> str:
    """The k-th PBS job id the FakeRemote hands out (qsub count 1 is 101)."""
    return f"{101 + k}.{HOST}"


class PbsWatch(rs.FakeRemote):
    """FakeRemote plus qstat answers and a copy-out of a stand-in remote filesystem."""

    def __init__(self, fs: Path, **kw):
        super().__init__(**kw)
        self.fs = fs
        self.statuses: dict[str, str] = {}  # job id -> fixture file name (default: running)
        self.status_rc: dict[str, int] = {}
        self.status_stderr = ""
        self.fetch_fail: set[str] = set()  # run ids whose next fetch fails once

    def __call__(self, argv):
        argv = list(argv)
        if argv[:1] == ["ssh"] and argv[-1].startswith("qstat"):
            self.calls.append(argv)
            job = argv[-1].split()[-1]
            rc = self.status_rc.get(job, 0)
            if rc:
                return rs._result(rc, "", self.status_stderr or "qstat: refused")
            return rs._result(0, (FIXTURES / self.statuses.get(job, "qstat-R.txt")).read_text())
        if argv[:1] == ["rsync"]:
            self.calls.append(argv)
            if ":" in argv[-1]:  # push: the local bundle into the remote campaign folder
                return rs._result(self.rsync_rc)
            src = argv[-2].split(":", 1)[1].rstrip("/")  # fetch: remote run folder -> local dest
            run_id = Path(src).name
            if run_id in self.fetch_fail:
                self.fetch_fail.discard(run_id)
                return rs._result(23, "", "rsync: partial transfer")
            origin = self.fs / src.lstrip("/")
            if not origin.is_dir():
                return rs._result(23, "", "rsync: no such folder")
            shutil.copytree(origin, argv[-1], symlinks=True, dirs_exist_ok=True)
            return rs._result(0)
        return super().__call__(argv)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = PbsWatch(tmp_path / "remote_fs")
    monkeypatch.setattr(remote_campaign, "RUNNER", f)
    monkeypatch.setattr(remote_campaign, "stamp", lambda: rs.STAMP)
    return f


def sweep(p, *extra):
    args = ["sweep", "-c", str(p.campaign), "--experiments-dir", str(p.experiments),
            "--pipelines-dir", str(p.root / "pipelines"), "--direct", *extra]
    return CliRunner().invoke(experiment, args)


def watch(p, *extra):
    return CliRunner().invoke(experiment, ["watch", str(p.campaign_dir), "--pipelines-dir",
                                           str(p.root / "pipelines"), *extra])


def run_ids(p) -> list[str]:
    return [r.run_id for r in Campaign.load(p.campaign).expand()]


def remote_run(fs: Path, run_id: str, *, rc: int = 0, output: bool = True, markers: bool = True):
    """A finished remote run folder, written the way the generated task script writes it (5.3)."""
    run = fs / R.lstrip("/") / "runs" / run_id
    for sub in ("artifacts", "logs", "remote"):
        (run / sub).mkdir(parents=True)
    if output:
        (run / "artifacts" / "result.txt").write_text(f"{run_id}\n")
    (run / "logs" / "stdout.log").write_text("ok\n")
    (run / "logs" / "stderr.log").write_text("")
    (run / "remote" / "started").write_text("2026-11-02T10:00:00+00:00\n")
    (run / "remote" / "finished").write_text("2026-11-02T10:00:05+00:00\n")
    (run / "remote" / "environment.txt").write_text(
        "system=Linux\nmachine=x86_64\nruntime=Python 3.12.3\n")
    import hashlib

    lines = []
    for sub in ("artifacts", "logs"):
        for path in sorted((run / sub).rglob("*")):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                lines.append(f"{digest}  {path.relative_to(run).as_posix()}\n")
    (run / "remote" / "SHA256SUMS").write_text("".join(lines))
    if markers:
        (run / "remote" / "exit").write_text(f"{rc}\n")
    return run


def local_run(p, run_id: str) -> Path:
    return p.campaign_dir / "runs" / run_id


def config_of(run: Path) -> dict:
    return yaml.safe_load((run / "config.yaml").read_text())


def results(p) -> dict:
    return json.loads((p.campaign_dir / "results.json").read_text())


def tasks(p) -> list[dict]:
    return rs.read_remote_json(p.campaign_dir)["tasks"]


def core_job(p):
    return next(j for j in jobs.list_jobs(Project(p.root), p.ws)
                if j.id == rs.read_remote_json(p.campaign_dir)["core_job"])


def spent(p) -> int:
    return budget.read_budget(p.ws)["spent"]["experiment_runs"]


def recorded_events(p) -> int:
    return sum(1 for e in rs.read_events(p.ws) if e.get("type") == "budget.recorded")


def add_reference(p):
    """Give the campaign a reference (the input file) so metrics are computed for completed runs."""
    doc = yaml.safe_load(p.campaign.read_text())
    doc["reference"] = "../data/in.txt"
    p.campaign.write_text(yaml.safe_dump(doc))


# ---- 1. a done job is fetched, verified, finalized, and the campaign completes ----


def test_done_jobs_are_fetched_finalized_and_written_as_results(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-F-0.txt"}
    for rid in run_ids(p):
        remote_run(fake.fs, rid)

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output

    assert [e["status"] for e in results(p)["runs"]] == ["completed", "completed"]
    assert (p.campaign_dir / "results.csv").is_file()
    for rid in run_ids(p):
        run = local_run(p, rid)
        cfg = config_of(run)
        assert cfg["status"] == "completed" and cfg["returncode"] == 0
        assert cfg["backend"] == "remote" and cfg["remote"] == "meta"
        assert {"campaign", "run_id", "params", "input", "output_file", "started", "finished"} <= set(cfg)
        man = json.loads((run / "manifest.json").read_text())
        assert manifest.REQUIRED_KEYS <= set(man)
        assert man["remote"]["transfer"]["verified"] is True
        assert set(man["remote"]) >= {"name", "scheduler", "jobs", "task_index", "transfer"}
        text = (run / "manifest.json").read_text()
        assert HOST not in text and R not in text and job_id(0) not in text
        assert not (run / "metrics.json").exists()  # no reference in this campaign
    assert rs.read_remote_json(p.campaign_dir)["state"] == "complete"
    assert core_job(p).state == "done" and core_job(p).exit_code == 0
    assert spent(p) == 2 and recorded_events(p) == 1

    calls = len(fake.calls)
    again = watch(p, "--once")
    assert again.exit_code == 0
    assert len(fake.calls) == calls  # a complete campaign makes no transport call


# ---- 2. failure reasons (5.9): each is a failed run with its reason, and no metrics ----

FAILURES = [
    # name, job fixture, remote_run kwargs, mutation after the run was written, expected reason
    ("exit-nonzero", "qstat-F-0.txt", {"rc": 3}, None, "exit-nonzero"),
    ("no-exit-marker", "qstat-F-1.txt", {"markers": False}, None, "no-exit-marker"),
    ("no-output", "qstat-F-0.txt", {"output": False}, None, "no-output"),
    ("tampered-artifact", "qstat-F-0.txt", {}, "tamper", "transfer-hash"),
    ("listed-file-removed", "qstat-F-0.txt", {}, "remove", "transfer-hash"),
    ("unlisted-file", "qstat-F-0.txt", {}, "extra", "transfer-extra"),
    ("checksum-list-missing", "qstat-F-0.txt", {}, "no-sums", "transfer-missing"),
]


@pytest.mark.parametrize("name,fixture,kwargs,mutation,reason",
                         FAILURES, ids=[f[0] for f in FAILURES])
def test_each_failure_reason_is_recorded_and_no_metrics_come_from_the_run(
        fake, tmp_path, name, fixture, kwargs, mutation, reason):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): fixture}
    rid = run_ids(p)[0]
    run = remote_run(fake.fs, rid, **kwargs)
    if mutation == "tamper":
        (run / "artifacts" / "result.txt").write_text("someone else\n")
    elif mutation == "remove":
        (run / "artifacts" / "result.txt").unlink()
    elif mutation == "extra":
        (run / "logs" / "extra.txt").write_text("unlisted\n")
    elif mutation == "no-sums":
        (run / "remote" / "SHA256SUMS").unlink()

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    cfg = config_of(local_run(p, rid))
    assert cfg["status"] == "failed"
    assert cfg["reason"] == reason
    assert results(p)["runs"][0]["status"] == "failed"
    assert not (local_run(p, rid) / "metrics.json").exists()


# ---- 3. planted links (5.12.5, 5.12.10) ----


def test_a_completed_run_with_a_reference_gets_metrics_computed_locally(fake, tmp_path, monkeypatch):
    p = rs.make_project(tmp_path, n_gains=1)
    add_reference(p)
    seen: list = []

    def fake_metrics(names, out, ref):
        seen.append((list(names), Path(out).name, Path(ref).name))
        return {"score": 0.25}

    monkeypatch.setattr(remote_campaign, "compute_metrics", fake_metrics)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    remote_run(fake.fs, rid)

    assert watch(p, "--once").exit_code == 0
    assert seen == [(["score"], "result.txt", "in.txt")]
    run = local_run(p, rid)
    assert json.loads((run / "metrics.json").read_text()) == {"score": 0.25}
    assert results(p)["runs"][0]["metrics"] == {"score": 0.25}
    man = json.loads((run / "manifest.json").read_text())
    assert man["metrics_sha256"] is not None


# ---- 3b. planted links (5.12.5, 5.12.10) ----


def test_a_symlink_in_the_fetched_outputs_fails_the_run_and_nothing_reads_through_it(
        fake, tmp_path, monkeypatch):
    p = rs.make_project(tmp_path, n_gains=1)
    add_reference(p)
    read_by_metrics: list = []
    monkeypatch.setattr(remote_campaign, "compute_metrics",
                        lambda names, out, ref: read_by_metrics.append(out) or {"score": 1.0})
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    run = remote_run(fake.fs, rid)
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of the run\n")
    (run / "artifacts" / "link.txt").symlink_to(outside)  # added after the checksum list

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    assert config_of(local_run(p, rid))["reason"] == "fetched-symlink"
    assert read_by_metrics == []
    assert not (local_run(p, rid) / "metrics.json").exists()


def test_a_planted_link_at_a_run_folder_is_refused_without_writing_through_it(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    remote_run(fake.fs, rid)
    outside = tmp_path / "outside"
    outside.mkdir()
    (p.campaign_dir / "runs").mkdir()
    (p.campaign_dir / "runs" / rid).symlink_to(outside, target_is_directory=True)

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    assert list(outside.iterdir()) == []  # nothing was fetched or written through the link
    assert tasks(p)[0]["state"] == "finalized" and tasks(p)[0]["reason"] == "fetched-symlink"


# ---- 4. states that are not finished: no fetch, exit 4, a counts line ----


def test_queued_and_running_jobs_are_not_fetched_and_the_pass_exits_4(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-Q.txt", job_id(1): "qstat-R.txt"}
    for rid in run_ids(p):
        remote_run(fake.fs, rid)

    result = watch(p, "--once")
    assert result.exit_code == 4, result.output
    assert not (p.campaign_dir / "runs").exists()
    assert rs.read_remote_json(p.campaign_dir)["state"] == "running"
    assert all(t["state"] == "submitted" for t in tasks(p))
    log = Path(core_job(p).log).read_text()
    assert "submitted=2" in log and "finalized=0" in log


def test_a_failed_status_call_leaves_that_job_and_the_others_are_processed(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-F-0.txt"}
    fake.status_rc = {job_id(0): 1}
    for rid in run_ids(p):
        remote_run(fake.fs, rid)

    result = watch(p, "--once")
    assert result.exit_code == 4, result.output
    assert [t["state"] for t in tasks(p)] == ["submitted", "finalized"]


def test_a_credentials_failure_in_status_is_no_ticket_and_remote_json_is_unchanged(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    fake.status_rc = {job_id(0): 2}
    fake.status_stderr = "REMOTE_NO_TICKET: key has expired"
    remote_run(fake.fs, run_ids(p)[0])
    before = (p.campaign_dir / "remote.json").read_bytes()

    result = watch(p, "--once")
    assert result.exit_code == 1, result.output
    assert "NO_TICKET" in result.output
    assert (p.campaign_dir / "remote.json").read_bytes() == before


def test_a_failed_fetch_keeps_the_task_submitted_and_the_next_pass_retries(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-F-0.txt"}
    fake.fetch_fail = {run_ids(p)[0]}
    for rid in run_ids(p):
        remote_run(fake.fs, rid)

    first = watch(p, "--once")
    assert first.exit_code == 4, first.output
    assert [t["state"] for t in tasks(p)] == ["submitted", "finalized"]

    second = watch(p, "--once")
    assert second.exit_code == 0, second.output
    assert [e["status"] for e in results(p)["runs"]] == ["completed", "completed"]


# ---- 5. slots: pending tasks are submitted as the concurrency ceiling frees ----


def test_pending_tasks_are_submitted_as_slots_free_and_the_budget_counts_once(fake, tmp_path):
    remote = rs.pbs_remote(limits=dict(rs.PBS_LIMITS, max_concurrent_jobs=2))
    p = rs.make_project(tmp_path, remote=remote, n_gains=5)
    assert sweep(p).exit_code == 0
    ids = run_ids(p)
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-F-0.txt"}
    for rid in ids[:2]:
        remote_run(fake.fs, rid)
    assert watch(p, "--once").exit_code == 4
    assert [t["state"] for t in tasks(p)] == ["finalized", "finalized", "submitted",
                                             "submitted", "pending"]

    fake.statuses.update({job_id(2): "qstat-F-0.txt", job_id(3): "qstat-F-0.txt"})
    for rid in ids[2:4]:
        remote_run(fake.fs, rid)
    assert watch(p, "--once").exit_code == 4
    assert [t["state"] for t in tasks(p)] == ["finalized"] * 4 + ["submitted"]

    fake.statuses[job_id(4)] = "qstat-F-0.txt"
    remote_run(fake.fs, ids[4])
    assert watch(p, "--once").exit_code == 0
    assert [e["status"] for e in results(p)["runs"]] == ["completed"] * 5
    assert spent(p) == 5 and recorded_events(p) == 1


# ---- 6. a submit that failed is a failed run in the report, and the campaign still completes ----


def test_a_submit_failed_task_is_finalized_as_a_failed_run(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    fake._qsub_rc = [0, 1]  # the second submit is refused
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    remote_run(fake.fs, run_ids(p)[0])

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    assert [t["state"] for t in tasks(p)] == ["finalized", "finalized"]
    assert tasks(p)[1]["reason"] == "submit-failed"
    assert [e["status"] for e in results(p)["runs"]] == ["completed", "failed"]
    assert config_of(local_run(p, run_ids(p)[1]))["status"] == "failed"
    assert (local_run(p, run_ids(p)[1]) / "manifest.json").is_file()
    assert spent(p) == 2


# ---- 7. the loop, the --json document, the options, and refusals ----


def test_the_loop_stops_at_the_time_limit_and_stops_at_once_when_complete(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-R.txt"}
    now = [0.0]
    sleeps: list[float] = []

    def clock():
        return now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    out = remote_campaign.watch(p.campaign_dir, pipelines_dir=p.root / "pipelines", interval=5, max_minutes=1,
                                sleep=sleep, clock=clock)
    assert out.exit_code == 4
    assert sleeps and all(s == 5 for s in sleeps)
    assert 60 <= sum(sleeps) <= 65

    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    remote_run(fake.fs, run_ids(p)[0])
    sleeps.clear()
    done = remote_campaign.watch(p.campaign_dir, pipelines_dir=p.root / "pipelines", interval=5, max_minutes=1,
                                 sleep=sleep, clock=clock)
    assert done.exit_code == 0 and sleeps == []


def test_the_json_document_after_one_pass(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-Q.txt"}
    remote_run(fake.fs, run_ids(p)[0])

    result = watch(p, "--once", "--json")
    assert result.exit_code == 4, result.output
    doc = json.loads([line for line in result.output.splitlines() if line.startswith("{")][-1])
    assert doc["campaign"] == "gain-sweep" and doc["campaign_id"] == CID
    assert doc["state"] == "running" and doc["complete"] is False
    assert doc["counts"] == {"pending": 0, "submitted": 1, "finalized": 1, "submit_failed": 0,
                             "completed": 1, "failed": 0}


@pytest.mark.parametrize("args", [["--interval", "4"], ["--max-minutes", "0"],
                                  ["--max-minutes", "1441"]])
def test_out_of_range_options_are_refused_by_click(fake, tmp_path, args):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    result = watch(p, "--once", *args)
    assert result.exit_code == 2
    assert "is not in the range" in result.output


def test_a_campaign_folder_outside_a_run_is_refused(fake, tmp_path):
    loose = tmp_path / "loose" / "gain-sweep"
    loose.mkdir(parents=True)
    result = CliRunner().invoke(experiment, ["watch", str(loose), "--once"])
    assert result.exit_code == 1
    assert "no-run" in result.output
    assert fake.calls == []


def test_a_tampered_remote_json_is_refused_before_any_transport_call(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    doc = rs.read_remote_json(p.campaign_dir)
    doc["remote_dir"] = "/data/x/elsewhere"
    (p.campaign_dir / "remote.json").write_text(json.dumps(doc))
    calls = len(fake.calls)

    result = watch(p, "--once")
    assert result.exit_code == 1
    assert "remote-state-invalid" in result.output
    assert len(fake.calls) == calls


# ---- 8. M-D3 t4 fix (review r1): fetched metrics are never read, links are never followed (5.9, 5.12) ----


def score_metric(p):
    """A real registered metric (the pipeline's metrics folder, as in 5.9); reads the two files it is given."""
    folder = p.root / "pipelines" / "demo" / "metrics"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "score.py").write_text(
        "from pathlib import Path\n"
        "def score(output, reference):\n"
        "    return float(len(Path(output).read_text()) - len(Path(reference).read_text()))\n"
        "METRICS = {'score': (score, True)}\n")


@pytest.mark.parametrize("rc", [0, 3], ids=["completed", "failed"])
def test_a_fetched_metrics_file_is_never_read_into_results(fake, tmp_path, rc):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    run = remote_run(fake.fs, rid, rc=rc)
    (run / "metrics.json").write_text(json.dumps({"score": 999}))  # root level: outside the checksum list

    assert watch(p, "--once").exit_code == 0
    entry = results(p)["runs"][0]
    assert entry["status"] == ("completed" if rc == 0 else "failed")
    assert entry["metrics"] == {}
    assert not (local_run(p, rid) / "metrics.json").exists()


def test_a_fetched_metrics_symlink_is_never_read_through(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    run = remote_run(fake.fs, rid)
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"secret": 1}))
    (run / "metrics.json").symlink_to(secret)

    assert watch(p, "--once").exit_code == 0
    assert config_of(local_run(p, rid))["reason"] == "fetched-symlink"
    assert results(p)["runs"][0]["metrics"] == {}
    assert secret.read_text() == json.dumps({"secret": 1})  # the link's target is untouched
    assert not (local_run(p, rid) / "metrics.json").is_symlink()


WRITE_TARGETS = ["config.yaml", "environment.json", "manifest.json", "metrics.json"]


@pytest.mark.parametrize("target", WRITE_TARGETS + [name + ".tmp" for name in WRITE_TARGETS])
def test_a_planted_link_at_a_write_target_does_not_stop_the_pass_and_is_never_written_through(
        fake, tmp_path, target):
    p = rs.make_project(tmp_path, n_gains=1)
    add_reference(p)
    score_metric(p)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    run = remote_run(fake.fs, rid)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    (run / target).symlink_to(outside)

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    assert outside.read_text() == "keep me\n"
    assert tasks(p)[0]["state"] == "finalized" and tasks(p)[0]["reason"] == "fetched-symlink"
    entry = results(p)["runs"][0]
    assert entry["status"] == "failed" and entry["metrics"] == {}
    assert rs.read_remote_json(p.campaign_dir)["state"] == "complete"


def _cancel_on_status(monkeypatch):
    """The scheduler reports the job cancelled: cmd_status writes that state to the ledger (as for slurm)."""
    R = remote_campaign._remote()

    def cancelled(remote, t, job_id, workspace=None, **kw):
        ledger = R.jobs.load_jobs(workspace)
        R.jobs.set_state(ledger, job_id, "cancelled")
        R.jobs.save_jobs(workspace, ledger)
        return 0

    monkeypatch.setattr(R.rcmd, "cmd_status", cancelled)


@pytest.mark.parametrize("fetch_ok", [False, True], ids=["fetch-fails", "fetch-ok-no-markers"])
def test_a_cancelled_job_without_markers_is_a_failed_run_with_its_folder(
        fake, tmp_path, monkeypatch, fetch_ok):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    _cancel_on_status(monkeypatch)
    rid = run_ids(p)[0]
    remote_run(fake.fs, rid, markers=False)
    if not fetch_ok:
        fake.fetch_fail = {rid}

    result = watch(p, "--once")
    assert result.exit_code == 0, result.output
    run = local_run(p, rid)
    assert config_of(run)["status"] == "failed" and config_of(run)["reason"] == "job-cancelled"
    assert json.loads((run / "manifest.json").read_text())["status"] == "failed"
    assert results(p)["runs"][0]["status"] == "failed"
    assert not (run / "metrics.json").exists()


def test_config_yaml_has_the_local_keys_plus_backend_and_remote_and_no_user_name(fake, tmp_path):
    p = rs.make_project(tmp_path, remote=rs.pbs_remote(user="zqremoteuser"), n_gains=1)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt"}
    rid = run_ids(p)[0]
    remote_run(fake.fs, rid)
    assert watch(p, "--once").exit_code == 0

    local_keys = {"campaign", "run_id", "params", "input", "reference", "output_file", "metrics",
                  "pipeline_dir", "status", "started", "returncode", "finished"}  # runner.py:86-109
    assert set(config_of(local_run(p, rid))) == local_keys | {"backend", "remote"}
    for path in local_run(p, rid).rglob("*"):
        if path.is_file():
            assert "zqremoteuser" not in path.read_text(errors="ignore"), path.name
            assert R not in path.read_text(errors="ignore"), path.name


def test_the_campaign_tools_work_on_a_finished_remote_campaign_and_metrics_recompute(fake, tmp_path):
    p = rs.make_project(tmp_path, n_gains=2)
    add_reference(p)
    score_metric(p)
    assert sweep(p).exit_code == 0
    fake.statuses = {job_id(0): "qstat-F-0.txt", job_id(1): "qstat-F-0.txt"}
    for rid in run_ids(p):
        remote_run(fake.fs, rid)
    assert watch(p, "--once").exit_code == 0

    for command in ("stats", "compare", "report"):
        result = CliRunner().invoke(experiment, [command, "--include-direct", str(p.campaign_dir)])
        assert result.exit_code == 0, (command, result.output)
    run = local_run(p, run_ids(p)[0])
    result = CliRunner().invoke(experiment, ["metrics", str(run)])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(experiment, ["verify", str(p.campaign_dir)])
    assert result.exit_code == 0, result.output
    assert "note metrics-recomputed" in result.output


@pytest.mark.parametrize("broken", ["yaml", "stage"])
def test_a_broken_campaign_file_exits_one_with_a_rule_not_a_traceback(fake, tmp_path, broken):
    p = rs.make_project(tmp_path, n_gains=1)
    assert sweep(p).exit_code == 0
    campaign_file = p.campaign_dir / "campaign.yaml"
    if broken == "yaml":
        campaign_file.write_text("name: [unclosed\n")
    else:
        doc = yaml.safe_load(campaign_file.read_text())
        doc["stage"] = "no-such-stage"
        campaign_file.write_text(yaml.safe_dump(doc))
    calls = len(fake.calls)

    result = watch(p, "--once")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)  # a message, not a crash
    assert "rule campaign-" in result.output
    assert len(fake.calls) == calls  # refused before any transport call
