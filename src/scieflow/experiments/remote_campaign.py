"""M-D3 t3: preflight, stage, push and submit a `backend: remote` campaign (plan 5.4-5.6, 5.8).

The campaign folder, its bundle and the ledger are written here; every remote action goes through
remote.py's own command functions (cmd_check, cmd_mkdir, cmd_push, cmd_submit) called in-process with
the remote loaded by policy.load_remote. Job ids are read from the ledger, never from remote.py's text
(M-D3-D1). A refusal raises RemoteError before any write, spend or transport call. Nothing here waits:
collecting results is `scieflow experiment watch` (M-D3 t4).
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import posixpath
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from scieflow.core import config, events
from scieflow.core import jobs as core_jobs
from scieflow.core.data import egress
from scieflow.core.project import Project
from scieflow.core.run import actions
from scieflow.experiments import manifest, remote_bundle
from scieflow.experiments.campaign import MAX_REPLICATES, TOKEN_RE, Campaign
from scieflow.experiments.metrics import compute_metrics, load_local_metrics
from scieflow.experiments.stage import Stage, find_stage, sif_path
from scieflow.experiments.sweep import run_entry, write_results

RUNNER = None  # test seam: the transport runner; None is the real subprocess runner
STATE_FILE = "remote.json"
STATE_VERSION = 1
JOB_SCRIPT = "job.sh"
CHECK_OPS = ("check", "mkdir", "rsync", "fetch")
SCHEDULER_OPS = ("submit", "status")
MODES = ("direct", "apptainer")
CAMPAIGN_STATES = ("staged", "pushed", "submitting", "running", "complete")  # plan 5.6
TASK_STATES = ("pending", "submitted", "submit-failed", "finalized")


class RemoteError(Exception):
    """A refusal: `rule` names the rule, `message` says what failed. Nothing was written or sent."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@functools.lru_cache(maxsize=1)
def _remote():
    """remote.py's modules, imported lazily: scripts/ is on sys.path under pytest and in a checkout."""
    try:
        from remote import jobs as rjobs
        from remote import policy, schedulers, transport
    except ImportError:
        sys.path.insert(0, str(config.repo_root() / "scripts"))
        from remote import jobs as rjobs
        from remote import policy, schedulers, transport
    from remote import remote as rcmd
    from remote.schedulers.base import JobSpec
    return SimpleNamespace(jobs=rjobs, policy=policy, schedulers=schedulers,
                           transport=transport, rcmd=rcmd, JobSpec=JobSpec)


def stamp() -> str:
    """The UTC stamp of a remote campaign id (one function, monkeypatchable in tests)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def check_location(run_dir: Path | None, campaign_dir: Path) -> None:
    """The campaign folder must lie in a run and be fresh (plan 5.4 step 1)."""
    if run_dir is None:
        raise RemoteError("no-run", "a remote campaign runs inside a run: put --experiments-dir "
                                    "under workspace/<slug>/")
    if os.path.lexists(campaign_dir):
        raise RemoteError("campaign-exists", f"{campaign_dir} already exists; a remote campaign "
                                             "needs a fresh folder")


@dataclass
class Session:
    """Everything one remote campaign needs, once preflight has passed."""

    campaign: Campaign
    campaign_dir: Path
    run_dir: Path
    root: Path
    remote: object
    t: object
    cid: str
    remote_dir: str
    stage: Stage | None = None
    mode: str = "direct"
    python: str = "python3"

    @property
    def bundle(self) -> Path:
        return self.run_dir / "scratch" / "remote" / self.cid


@dataclass
class Outcome:
    campaign_dir: Path
    campaign_id: str
    remote: str
    jobs: str
    total: int
    submitted: int
    state: str
    exit_code: int
    stop: str | None = None  # "limit" when the concurrency ceiling stopped a pass


def _policy_error(exc) -> RemoteError:
    return RemoteError("policy", f"POLICY: {exc}")


def _relay(buffer: io.StringIO) -> None:
    """remote.py's own lines go to stderr with a prefix; they never decide anything here."""
    for line in buffer.getvalue().splitlines():
        print(f"remote: {line}", file=sys.stderr)


def _load_remote(root: Path, name: str):
    R = _remote()
    try:
        return R.policy.load_remote(root, name)
    except R.policy.PolicyError as exc:
        raise _policy_error(exc) from exc


def _check_ops(R, remote) -> None:
    """Every missing operation in one message (plan 5.4 step 4)."""
    missing = []
    for op in CHECK_OPS:
        try:
            R.policy.check_op(remote, op)
        except R.policy.PolicyError as exc:
            missing.append(str(exc))
    for op in SCHEDULER_OPS:
        try:
            R.policy.check_scheduler_op(remote, op)
        except R.policy.PolicyError as exc:
            missing.append(str(exc))
    if missing:
        raise RemoteError("policy", "POLICY: " + "; ".join(missing))


def _task_names(campaign: Campaign, cid: str, runs) -> list[tuple[str, str]]:
    """(task name, job name) per run; an array job shares one task and one job name (plan 5.6)."""
    if campaign.remote.jobs == "array":
        return [(f"{cid}.array", "sf-array") for _ in runs]
    return [(f"{cid}.{k}", f"sf-{k}") for k in range(len(runs))]


def _refuse_links(path: Path, what: str) -> None:
    if path.is_symlink():
        raise RemoteError("symlink", f"a symlink is refused in the {what}: {path}")
    if path.is_dir():
        for cur, dirs, files in os.walk(path):
            for name in dirs + files:
                full = os.path.join(cur, name)
                if os.path.islink(full):
                    raise RemoteError("symlink", f"a symlink is refused in the {what}: {full}")


def _push_check(root: Path, path: Path, remote_name: str) -> None:
    try:
        egress.push_check(root, path, remote_name)
    except egress.EgressRefused as exc:
        raise RemoteError("data-protection", str(exc)) from exc


def preflight(campaign: Campaign, stage: Stage, campaign_dir: Path, run_dir: Path | None,
              mode: str, *, runner=None) -> Session:
    """Every check that must pass before anything is written, spent or sent (plan 5.4).

    The local budget, design and analysis guards run in the CLI before this (same order as a local
    sweep). The Kerberos check is the last step and the only transport call.
    """
    R = _remote()
    check_location(run_dir, campaign_dir)
    root = egress.root_of_run(run_dir)
    remote = _load_remote(root, campaign.remote.name)
    _check_ops(R, remote)
    cid = f"{campaign.name}-{stamp()}"
    remote_dir = posixpath.join(campaign.remote.dir, cid)
    runs = campaign.expand()
    res = campaign.resources
    try:
        R.policy.check_dir(remote, campaign.remote.dir)
        R.policy.check_dir(remote, remote_dir)
        R.policy.check_queue(remote, res.queue)
        R.policy.check_token(cid, "campaign id")
        for run in runs:
            R.policy.check_token(run.run_id, "run id")
        for task, job_name in set(_task_names(campaign, cid, runs)):
            R.policy.check_token(task, "task name")
            R.policy.check_token(job_name, "job name")
        R.policy.check_script(JOB_SCRIPT)
        _, warnings = R.policy.clamp_resources(remote, res.walltime, res.cpus, res.mem_gb,
                                               res.gpus, res.scratch_type, res.scratch_gb)
        adapter = R.schedulers.get(remote.scheduler)
        adapter.resource_args(R.JobSpec(
            name="sf-0", dir=remote_dir, script=JOB_SCRIPT, walltime=res.walltime,
            cpus=res.cpus, mem_gb=res.mem_gb, gpus=res.gpus, queue=res.queue,
            scratch_type=res.scratch_type, scratch_gb=res.scratch_gb))
    except R.policy.PolicyError as exc:
        raise _policy_error(exc) from exc
    if warnings:  # M-D3-D6: a request above a limit is refused, never silently cut down
        raise RemoteError("resources-over-limit",
                          "resources-over-limit: " + "; ".join(warnings)
                          + " (lower the request to the limit; nothing was run)")
    if campaign.remote.jobs == "array" and len(runs) > MAX_REPLICATES:
        raise RemoteError("array-too-large", f"a jobs: array campaign holds at most "
                                             f"{MAX_REPLICATES} runs ({len(runs)} planned)")
    if mode == "apptainer" and stage is not None and not sif_path(stage).exists():
        raise RemoteError("sif-missing", f"{sif_path(stage)} not built — run: scieflow experiment "
                                         f"env build {stage.dir}")
    session = Session(campaign=campaign, campaign_dir=Path(campaign_dir), run_dir=Path(run_dir),
                      root=root, remote=remote, t=None, cid=cid, remote_dir=remote_dir,
                      stage=stage, mode=mode,
                      python=remote_bundle.direct_python(remote.scheduler))
    _push_check(root, campaign.input, remote.name)
    if campaign.reference is not None:
        _push_check(root, campaign.reference, remote.name)
    _push_check(root, session.bundle, remote.name)
    if stage is not None:
        _refuse_links(stage.dir, "stage folder")
    _refuse_links(campaign.input, "input")  # plan 5.4 step 9: before any write, before the ticket
    session.t = R.transport.for_remote(remote, runner if runner is not None else RUNNER)
    _ticket(session)
    return session


def _ticket(session: Session) -> None:
    """cmd_check (Kerberos local and remote): the only transport call before a write."""
    R = _remote()
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = R.rcmd.cmd_check(session.remote, session.t)
    except R.policy.PolicyError as exc:
        raise _policy_error(exc) from exc
    message = buffer.getvalue().strip()
    if rc == 2:
        raise RemoteError("no-ticket", message or "NO_TICKET: stop and ask the user to run `kinit`")
    if rc:
        raise RemoteError("check-failed", message or "the remote check failed")


def _task_rows(campaign: Campaign, cid: str) -> list[dict]:
    rows = []
    for index, (run, (task, job_name)) in enumerate(
            zip(campaign.expand(), _task_names(campaign, cid, campaign.expand()))):
        rows.append({
            "index": index, "run_id": run.run_id, "config_id": run.config_id,
            "replicate": run.replicate, "seed": run.seed, "params": run.params,
            "task": task, "job_name": job_name, "job_id": None,
            "state": "pending", "status": None, "reason": None,
        })
    return rows


def _save(session: Session, state: dict) -> None:
    manifest.atomic_write(session.campaign_dir / STATE_FILE, manifest.canonical_bytes(state))


def _discard(session: Session) -> None:
    """Remove what _start wrote before a refusal: the campaign folder and its bundle."""
    shutil.rmtree(session.campaign_dir, ignore_errors=True)
    shutil.rmtree(session.bundle, ignore_errors=True)


def _start(session: Session) -> dict:
    """Write the campaign folder, the bundle and remote.json (state staged); open the core job."""
    campaign, run_dir = session.campaign, session.run_dir
    session.campaign_dir.mkdir(parents=True)
    context = manifest.capture_context(campaign, session.stage, session.mode, session.campaign_dir)
    (session.campaign_dir / "campaign.yaml").write_text(yaml.safe_dump(
        campaign.to_dict() | {"pipeline_dir": str(session.stage.dir.parent.parent)},
        sort_keys=False))
    try:
        digest = remote_bundle.build_bundle(campaign, session.stage, campaign.expand(),
                                            session.mode, session.bundle,
                                            remote_root=session.remote_dir, python=session.python)
    except remote_bundle.BundleError as exc:
        _discard(session)  # no refusal leaves a campaign folder or a bundle behind (plan 5.5)
        raise RemoteError("bundle", str(exc)) from exc
    rows = _task_rows(campaign, session.cid)
    state = {
        "version": STATE_VERSION, "campaign": campaign.name, "campaign_id": session.cid,
        "remote": session.remote.name, "scheduler": session.remote.scheduler,
        "remote_dir": session.remote_dir, "jobs": campaign.remote.jobs, "mode": session.mode,
        "resources": asdict(campaign.resources), "state": "staged",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bundle": {"sha256": digest["sha256"], "files": digest["files"]},
        "budget_recorded": 0, "core_job": None, "context": context, "tasks": rows,
    }
    _save(session, state)
    job = core_jobs.record_external(
        Project(session.root), kind="remote",
        label=f"{campaign.name} on {session.remote.name}", run_dir=run_dir,
        argv=["scieflow", "experiment", "watch", str(session.campaign_dir)],
        cwd=session.campaign_dir)
    state["core_job"] = job.id
    _save(session, state)
    events.emit(run_dir, "note.experiment_remote", "system", kind="staged",
                campaign=campaign.name, campaign_id=session.cid, remote=session.remote.name,
                runs=len(rows))
    return state


def _push(session: Session, state: dict) -> bool:
    """mkdir the remote campaign folder, then rsync the bundle with --checksum (plan 5.5 step 3)."""
    R = _remote()
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = R.rcmd.cmd_mkdir(session.remote, session.t, session.remote_dir)
        _relay(buffer)
        if rc == 0:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = R.rcmd.cmd_push(session.remote, session.t, str(session.bundle),
                                     session.remote_dir, workspace=session.run_dir,
                                     checksum=True, root=session.root)
            _relay(buffer)
    except R.policy.PolicyError as exc:
        raise _policy_error(exc) from exc
    if rc == 0:
        state["state"] = "pushed"
        _save(session, state)
    return rc == 0


def _submit_units(state: dict) -> list[list[dict]]:
    pending = [row for row in state["tasks"] if row["state"] == "pending"]
    if not pending:
        return []
    if state["jobs"] == "array":
        return [pending]  # one job for every run of the campaign
    return [[row] for row in pending]


def _submit(session: Session, state: dict) -> str | None:
    """Submit the pending units in index order; return "limit" when the concurrency ceiling stops it."""
    R = _remote()
    run_dir = session.run_dir
    total = len(state["tasks"])
    for unit in _submit_units(state):
        task, job_name = unit[0]["task"], unit[0]["job_name"]
        ledger = R.jobs.load_jobs(run_dir)
        adopted = next((e for e in reversed(ledger) if e["task"] == task), None)
        if adopted is not None:  # duplicate guard: the scheduler already has this task (plan 5.5)
            _mark_submitted(session, state, unit, adopted["job_id"], total)
            continue
        env = [] if state["jobs"] == "array" else [f"SCIEFLOW_TASK={unit[0]['index']}"]
        resources = state["resources"]
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                rc = R.rcmd.cmd_submit(
                    session.remote, session.t, session.remote_dir, JOB_SCRIPT,
                    workspace=run_dir, task=task, walltime=resources["walltime"],
                    cpus=resources["cpus"], mem_gb=resources["mem_gb"], gpus=resources["gpus"],
                    queue=resources["queue"], name=job_name,
                    scratch_type=resources["scratch_type"], scratch_gb=resources["scratch_gb"],
                    environment_assignments=env, submit_dir=None, kind="job")
        except R.policy.PolicyError as exc:
            _relay(buffer)
            raise _policy_error(exc) from exc
        _relay(buffer)
        if rc == 4:  # the concurrency or attempts ceiling: a full queue, not a failure
            return "limit"
        if rc != 0:
            for row in unit:
                row.update(state="submit-failed", reason="submit-failed")
            _save(session, state)
            continue
        accepted = [e for e in R.jobs.load_jobs(run_dir) if e["task"] == task]
        if not accepted:
            raise RemoteError("ledger", f"the submit of {task} left no ledger entry")
        _mark_submitted(session, state, unit, accepted[-1]["job_id"], total)
    return None


def _mark_submitted(session: Session, state: dict, unit: list[dict], job_id: str, total: int):
    for row in unit:
        row.update(job_id=job_id, state="submitted", reason=None)
    if state["budget_recorded"] == 0:  # M-D3-D3: N runs, recorded once, at the first accepted job
        actions.record_spend(session.run_dir, experiment_runs=total)
        state["budget_recorded"] = total
    state["state"] = "running"
    _save(session, state)


def _outcome(session: Session, state: dict, stop: str | None) -> Outcome:
    submitted = sum(1 for row in state["tasks"] if row["state"] == "submitted")
    return Outcome(campaign_dir=session.campaign_dir, campaign_id=session.cid,
                   remote=session.remote.name, jobs=state["jobs"], total=len(state["tasks"]),
                   submitted=submitted, state=state["state"], stop=stop,
                   exit_code=_exit_code(state, stop))


def _exit_code(state: dict, stop: str | None) -> int:
    """0 when the pass did what it could (a full queue is normal); 1 when nothing could be submitted."""
    if state["state"] == "staged":
        return 1
    submitted = any(row["state"] == "submitted" for row in state["tasks"])
    if not submitted and stop != "limit":
        return 1
    return 0


def submit(session: Session) -> Outcome:
    """Stage (write the campaign folder, bundle and remote.json), push, then one submit pass (plan 5.5)."""
    state = _start(session)
    if not _push(session, state):
        return _outcome(session, state, None)
    stop = _submit(session, state)
    return _outcome(session, state, stop)


def _load_state(campaign_dir: Path) -> dict:
    import json

    path = Path(campaign_dir) / STATE_FILE
    if not path.is_file():
        raise RemoteError("remote-state-missing", f"{path} is missing; the campaign was not staged")
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RemoteError("remote-state-invalid", f"remote-state-invalid: remote.json is not "
                                                  f"readable JSON ({exc})") from exc
    if not isinstance(state, dict):
        raise RemoteError("remote-state-invalid", "remote-state-invalid: remote.json is not an object")
    return state


def _invalid(field: str, why: str) -> RemoteError:
    return RemoteError("remote-state-invalid", f"remote-state-invalid: {field} {why}")


def _validate(state: dict, campaign: Campaign, remote, runs) -> None:
    """Re-check remote.json against campaign.yaml, the policy and the token rules (plan 5.6)."""
    R = _remote()
    if state.get("version") != STATE_VERSION:
        raise _invalid("version", f"must be {STATE_VERSION}")
    if state.get("campaign") != campaign.name:
        raise _invalid("campaign", "does not match campaign.yaml")
    cid = state.get("campaign_id")
    if not (isinstance(cid, str) and cid.startswith(campaign.name + "-")
            and TOKEN_RE.fullmatch(cid)):
        raise _invalid("campaign_id", "must be the campaign name and a UTC stamp")
    if state.get("remote") != campaign.remote.name:
        raise _invalid("remote", "does not match the campaign's remote")
    if state.get("scheduler") != remote.scheduler:
        raise _invalid("scheduler", "does not match the remote's scheduler")
    if state.get("jobs") != campaign.remote.jobs:
        raise _invalid("jobs", "does not match the campaign's jobs")
    expected = posixpath.join(campaign.remote.dir, cid)
    if state.get("remote_dir") != expected:
        raise _invalid("remote_dir", f"must be {expected}")
    try:
        R.policy.check_dir(remote, expected)
    except R.policy.PolicyError as exc:
        raise _invalid("remote_dir", str(exc)) from exc
    if state.get("resources") != asdict(campaign.resources):
        raise _invalid("resources", "do not match campaign.yaml")
    if state.get("mode") not in MODES:
        raise _invalid("mode", f"must be one of {', '.join(MODES)}")
    if state.get("state") not in CAMPAIGN_STATES:
        raise _invalid("state", f"must be one of {', '.join(CAMPAIGN_STATES)}")
    recorded = state.get("budget_recorded")
    if isinstance(recorded, bool) or not isinstance(recorded, int) or recorded not in (0, len(runs)):
        raise _invalid("budget_recorded", f"must be 0 or {len(runs)}")
    rows = state.get("tasks")
    if not isinstance(rows, list) or len(rows) != len(runs):
        raise _invalid("tasks", "do not match the campaign's runs")
    names = _task_names(campaign, cid, runs)
    for k, (row, run, (task, job_name)) in enumerate(zip(rows, runs, names)):
        if not isinstance(row, dict):
            raise _invalid("tasks", f"row {k} is not an object")
        expected_row = (run.run_id, run.config_id, run.replicate, run.seed, run.params)
        got = (row.get("run_id"), row.get("config_id"), row.get("replicate"), row.get("seed"),
               row.get("params"))
        if got != expected_row or row.get("index") != k:
            raise _invalid("tasks", f"row {k} does not match run {run.run_id}")
        if row.get("task") != task or row.get("job_name") != job_name:
            raise _invalid("tasks", f"row {k} has the wrong task or job name")
        job_id = row.get("job_id")
        if job_id is not None:
            try:
                R.policy.check_token(job_id, "job id")
            except R.policy.PolicyError as exc:
                raise _invalid("job_id", str(exc)) from exc
        if row.get("state") not in TASK_STATES:
            raise _invalid("tasks", f"row {k} state must be one of {', '.join(TASK_STATES)}")
    submitted = any(row.get("state") == "submitted" for row in rows)
    if submitted and recorded != len(runs):  # the spend is recorded once, at the first accepted job
        raise _invalid("budget_recorded", f"must be {len(runs)} once a task is submitted")


def _open(campaign_dir: Path, run_dir: Path, runner=None, *, stage: Stage | None = None):
    """Load campaign.yaml and remote.json, validated against the policy, before any transport call."""
    campaign = Campaign.load(campaign_dir / "campaign.yaml")
    root = egress.root_of_run(run_dir)
    remote = _load_remote(root, campaign.remote.name)
    runs = campaign.expand()
    state = _load_state(campaign_dir)
    _validate(state, campaign, remote, runs)  # before any transport call
    R = _remote()
    session = Session(campaign=campaign, campaign_dir=campaign_dir, run_dir=run_dir, root=root,
                      remote=remote, t=None, cid=state["campaign_id"],
                      remote_dir=state["remote_dir"], mode=state["mode"],
                      python=remote_bundle.direct_python(remote.scheduler), stage=stage)
    session.t = R.transport.for_remote(remote, runner if runner is not None else RUNNER)
    return session, state


def resume_submission(campaign_dir, run_dir, *, runner=None) -> Outcome:
    """Continue a staged or running remote campaign: validate, check, push if staged, one submit pass.

    `scieflow experiment watch` (M-D3 t4) builds on this pass; a crash-retry uses it directly.
    """
    campaign_dir = Path(campaign_dir)
    run_dir = Path(run_dir)
    session, state = _open(campaign_dir, run_dir, runner)
    _ticket(session)
    if state["state"] == "staged" and not _push(session, state):
        return _outcome(session, state, None)
    stop = _submit(session, state)
    return _outcome(session, state, stop)


# ---- M-D3 t4: watch — status, fetch, transfer check, finalize, results (plan 5.7-5.9) ----

FINISHED_JOB_STATES = ("done", "failed", "cancelled")  # the states that trigger a fetch (5.7 step 5)
WATCH_IN_PROGRESS = 4  # M-D3-D13: the same number remote.py uses for a full queue


@dataclass
class WatchOutcome:
    campaign_dir: Path
    campaign_id: str
    remote: str
    state: str
    complete: bool
    counts: dict
    exit_code: int  # 0 complete, 4 still running (wait), 1 refused or failed


def watch(campaign_dir, *, pipelines_dir, once: bool = False, interval: int = 60,
          max_minutes: int = 60, runner=None, sleep=None, clock=None) -> WatchOutcome:
    """Run watch passes until the campaign is complete, or once, or until max_minutes (5.7)."""
    campaign_dir = Path(campaign_dir)
    run_dir = actions.run_for_path(campaign_dir)
    if run_dir is None:
        raise RemoteError("no-run", "a remote campaign runs inside a run: put the campaign folder "
                                    "under workspace/<slug>/")
    if not (campaign_dir / "campaign.yaml").is_file():
        raise RemoteError("remote-state-missing", f"{campaign_dir / 'campaign.yaml'} is missing")
    campaign = Campaign.load(campaign_dir / "campaign.yaml")
    stage = find_stage(pipelines_dir, campaign.pipeline, campaign.stage)
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + max_minutes * 60
    while True:
        out = _pass(campaign_dir, run_dir, stage, runner)
        if out.complete or once or out.exit_code != WATCH_IN_PROGRESS:
            return out
        if clock() + interval > deadline:  # the time limit: still running, exit 4
            return out
        sleep(interval)


def _pass(campaign_dir: Path, run_dir: Path, stage: Stage, runner) -> WatchOutcome:
    session, state = _open(campaign_dir, run_dir, runner, stage=stage)
    if state["state"] == "complete":  # nothing is sent for a complete campaign
        return _watch_outcome(session, state, complete=True, exit_code=0)
    _ticket(session)
    if state["state"] == "staged" and not _push(session, state):
        return _watch_outcome(session, state, complete=False, exit_code=1)
    statuses = _read_statuses(session, state)
    _settle(session, state, statuses)  # fetch and finalize the jobs that have finished
    _submit(session, state)  # slots that finished jobs freed
    if not any(row["state"] in ("pending", "submitted") for row in state["tasks"]):
        _complete(session, state)
        return _watch_outcome(session, state, complete=True, exit_code=0)
    _log_counts(session, state)
    return _watch_outcome(session, state, complete=False, exit_code=WATCH_IN_PROGRESS)


def _counts(state: dict) -> dict:
    rows = state["tasks"]
    finalized = [row for row in rows if row["state"] == "finalized"]
    return {
        "pending": sum(row["state"] == "pending" for row in rows),
        "submitted": sum(row["state"] == "submitted" for row in rows),
        "finalized": len(finalized),
        "submit_failed": sum(row.get("reason") == "submit-failed" for row in rows),
        "completed": sum(row.get("status") == "completed" for row in finalized),
        "failed": sum(row.get("status") == "failed" for row in finalized),
    }


def _watch_outcome(session: Session, state: dict, *, complete: bool, exit_code: int) -> WatchOutcome:
    return WatchOutcome(campaign_dir=session.campaign_dir, campaign_id=session.cid,
                        remote=session.remote.name, state=state["state"], complete=complete,
                        counts=_counts(state), exit_code=exit_code)


def _read_statuses(session: Session, state: dict) -> dict[str, str | None]:
    """cmd_status for each distinct job of a submitted task (5.7 step 4); the ledger gives the state."""
    R = _remote()
    run_dir = session.run_dir
    job_ids: list[str] = []
    for row in state["tasks"]:
        if row["state"] == "submitted" and row["job_id"] not in job_ids:
            job_ids.append(row["job_id"])
    found: dict[str, str | None] = {}
    for job_id in job_ids:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                rc = R.rcmd.cmd_status(session.remote, session.t, job_id, workspace=run_dir)
        except R.policy.PolicyError as exc:
            _relay(buffer)
            raise _policy_error(exc) from exc
        _relay(buffer)
        if rc == 2:  # credentials: the pass stops here and nothing else changes
            raise RemoteError("no-ticket", buffer.getvalue().strip()
                              or "NO_TICKET: stop and ask the user to run `kinit`")
        if rc != 0:  # that job is left as it is for this pass; the others are still read
            print(f"watch: the status of job {job_id} was not read this pass", file=sys.stderr)
            continue
        entry = R.jobs.find(R.jobs.load_jobs(run_dir), job_id)
        found[job_id] = entry["state"] if entry else None
    return found


def _linked(run: Path, campaign_dir: Path) -> bool:
    """A planted link at the run folder or at the runs folder: nothing is read or written through it."""
    return run.is_symlink() or (campaign_dir / "runs").is_symlink()


def _drop_fetched_metrics(run: Path) -> None:
    """Remove a fetched metrics.json and metrics.json.tmp before anything reads the run folder (5.9, 5.12.5).

    A fetched metrics file is outside the checksum list, so it is never evidence: only this pass's own
    compute_metrics may write one. os.unlink removes a link itself and never touches the link's target.
    """
    for name in ("metrics.json", "metrics.json.tmp"):
        path = run / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _fetch(session: Session, row: dict) -> bool:
    """cmd_fetch of one run folder into <campaign>/runs/<run_id>/ (5.7 step 5); True on success."""
    R = _remote()
    dest = session.campaign_dir / "runs" / row["run_id"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = posixpath.join(session.remote_dir, "runs", row["run_id"]) + "/"
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = R.rcmd.cmd_fetch(session.remote, session.t, src, str(dest),
                                  workspace=session.run_dir, root=session.root)
    except R.policy.PolicyError as exc:
        _relay(buffer)
        raise _policy_error(exc) from exc
    _relay(buffer)
    return rc == 0


def _settle(session: Session, state: dict, statuses: dict) -> None:
    """Fetch and finalize every submitted task whose job has finished (5.7 step 5)."""
    campaign_dir = session.campaign_dir
    for row in state["tasks"]:
        if row["state"] != "submitted":
            continue
        job_state = statuses.get(row["job_id"])
        if job_state not in FINISHED_JOB_STATES:
            continue
        run = campaign_dir / "runs" / row["run_id"]
        if _linked(run, campaign_dir):
            _finalize(session, state, row, fetched=False, job_state=job_state,
                      reason="fetched-symlink")
            continue
        fetched = _fetch(session, row)
        if not fetched:  # the task stays submitted and the next pass retries it
            if job_state == "cancelled":
                _finalize(session, state, row, fetched=False, job_state=job_state,
                          reason="job-cancelled")
            continue
        _finalize(session, state, row, fetched=True, job_state=job_state)


def _now_aware(text: str | None, fallback: str) -> str:
    """A marker time usable by run_manifest (timezone-aware), else the fetch time."""
    if text is None:
        return fallback
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    return text if parsed.tzinfo is not None else fallback


def _sums_digest(run: Path) -> str | None:
    sums = run / "remote" / "SHA256SUMS"
    if sums.is_file() and not sums.is_symlink():
        return manifest.file_sha256(sums)
    return None


def _written_manifest_digest(run: Path) -> str | None:
    """The hash of the manifest.json this pass wrote as a regular file; None when it is missing or a link (5.9)."""
    path = run / manifest.MANIFEST_FILE
    if path.is_symlink() or not path.is_file():
        return None
    return manifest.file_sha256(path)


def _judge(session: Session, run: Path, job_state: str | None, now: str):
    """(status, reason, transfer, markers) of one fetched run (plan 5.9)."""
    markers = remote_bundle.read_markers(run, now)
    ok, why, files = remote_bundle.verify_transfer(run)
    transfer = {"verified": ok, "reason": why, "files": files, "sums_sha256": _sums_digest(run)}
    if why == "fetched-symlink":
        return "failed", why, transfer, markers
    if markers.exit_code is None:  # a run killed at its walltime has no checksum list either
        reason = "job-cancelled" if job_state == "cancelled" else "no-exit-marker"
        return "failed", reason, transfer, markers
    if not ok:
        return "failed", why, transfer, markers
    if markers.exit_code != 0:
        return "failed", "exit-nonzero", transfer, markers
    output = run / "artifacts" / session.stage.output_file
    if output.is_symlink() or not output.is_file():
        return "failed", "no-output", transfer, markers
    return "completed", None, transfer, markers


def _write_run_folder(session: Session, state: dict, row: dict, spec, run: Path, *, status: str,
                      reason: str | None, returncode, started: str, finished: str,
                      transfer: dict, markers) -> None:
    """config.yaml, environment.json, metrics.json (completed only) and manifest.json (5.9)."""
    campaign, stage, mode = session.campaign, session.stage, state["mode"]
    remote_info = {"name": session.remote.name, "scheduler": session.remote.scheduler,
                   "jobs": state["jobs"], "task_index": row["index"]}
    system = markers.system if markers else None
    machine = markers.machine if markers else None
    runtime = markers.runtime if markers else None
    run.mkdir(parents=True, exist_ok=True)

    config = {
        "campaign": campaign.name, "run_id": spec.run_id, "params": spec.params,
        "input": str(campaign.input),
        "reference": str(campaign.reference) if campaign.reference else None,
        "output_file": stage.output_file, "metrics": campaign.metrics,
        "pipeline_dir": str(stage.dir.parent.parent), "status": status, "started": started,
    }
    if spec.replicate is not None or spec.seed is not None:
        config.update(config_id=spec.config_id, replicate=spec.replicate, seed=spec.seed)
    config.update(returncode=returncode, finished=finished, backend="remote",
                  remote=session.remote.name)
    if reason:
        config["reason"] = reason
    manifest.atomic_write(run / "config.yaml", yaml.safe_dump(config, sort_keys=False).encode())

    command = remote_bundle.task_argv(
        stage, spec, remote_bundle.Layout("<remote>", Path(campaign.input).name), mode,
        session.python)  # the remote folder is shown as a placeholder: it holds the user's name
    environment = {"mode": mode, "command": command, "recorded": manifest.utc_now(),
                   "remote": {**remote_info, "job_id": row["job_id"]},
                   "platform": {"system": system, "machine": machine}, "runtime": runtime}
    manifest.atomic_write(run / "environment.json",
                          json.dumps(environment, indent=2).encode())

    if status == "completed" and campaign.reference is not None:
        load_local_metrics(stage.dir.parent.parent / "metrics")
        metrics = compute_metrics(campaign.metrics, run / "artifacts" / stage.output_file,
                                  campaign.reference)
        manifest.atomic_write(run / "metrics.json", json.dumps(metrics, indent=2).encode())

    doc = manifest.run_manifest(run, campaign_name=campaign.name, spec=spec, mode=mode,
                                status=status, returncode=returncode, started=started,
                                finished=finished, context=state["context"])
    ctx_env = state["context"]["environment"]
    doc["environment"] = {
        "mode": mode, "python": runtime if mode == "direct" else None,
        "platform": {"system": system, "machine": machine},
        "sif": ctx_env.get("sif"), "sif_sha256": ctx_env.get("sif_sha256"),
        "apptainer_version": runtime if mode == "apptainer" else None,
    }
    doc["remote"] = {**remote_info, "transfer": transfer}
    manifest.write_manifest(run, doc)


def _finalize(session: Session, state: dict, row: dict, *, fetched: bool, job_state: str | None,
              reason: str | None = None) -> None:
    """Make one task's run folder final and mark its row finalized (plan 5.9)."""
    spec = session.campaign.expand()[row["index"]]
    run = session.campaign_dir / "runs" / row["run_id"]
    now = manifest.utc_now()
    if _linked(run, session.campaign_dir):
        row.update(state="finalized", status="failed", reason="fetched-symlink")
    elif fetched:
        status, why, transfer, markers = _judge(session, run, job_state, now)
        started = _now_aware(markers.started, now)
        finished = _now_aware(markers.finished, now)
        _drop_fetched_metrics(run)
        status, why = _write_or_refuse(session, state, row, spec, run, status=status, reason=why,
                                       returncode=markers.exit_code, started=started, finished=finished,
                                       transfer=transfer, markers=markers)
        row.update(state="finalized", status=status, reason=why)
    else:  # never ran (a refused submit, or a cancelled job without a transfer): an empty failed run
        why = reason or "submit-failed"
        transfer = {"verified": False, "reason": why, "files": 0, "sums_sha256": None}
        _drop_fetched_metrics(run)
        status, why = _write_or_refuse(session, state, row, spec, run, status="failed", reason=why,
                                       returncode=None, started=now, finished=now, transfer=transfer,
                                       markers=None)
        row.update(state="finalized", status=status, reason=why)
    _save(session, state)


def _write_or_refuse(session: Session, state: dict, row: dict, spec, run: Path, *, status: str,
                     reason: str | None, returncode, started: str, finished: str, transfer: dict,
                     markers) -> tuple[str, str | None]:
    """_write_run_folder, or a refusal at a planted link (5.9, 5.12.10): the run is failed/fetched-symlink.

    Nothing is written through the link and the pass goes on; the other tasks are still finalized.
    FileExistsError is the O_EXCL create in manifest.atomic_write refusing a link planted after the check.
    """
    try:
        _write_run_folder(session, state, row, spec, run, status=status, reason=reason,
                          returncode=returncode, started=started, finished=finished,
                          transfer=transfer, markers=markers)
    except (manifest.UnsafeOutputError, FileExistsError):
        return "failed", "fetched-symlink"
    return status, reason


def _complete(session: Session, state: dict) -> None:
    """All tasks are finished: finalize the refused submits, write results, close the core job (5.7)."""
    for row in state["tasks"]:
        if row["state"] == "submit-failed":
            _finalize(session, state, row, fetched=False, job_state=None, reason="submit-failed")
    entries = []
    for spec, row in zip(session.campaign.expand(), state["tasks"]):
        run = session.campaign_dir / "runs" / row["run_id"]
        if _linked(run, session.campaign_dir) or row.get("reason") == "fetched-symlink":
            # nothing is read through a planted link, nor from a folder whose write was refused (5.12.5)
            entries.append({"run_id": spec.run_id, "config_id": spec.config_id,
                            "replicate": spec.replicate, "seed": spec.seed, "params": spec.params,
                            "mode": state["mode"], "status": "failed", "metrics": {},
                            "manifest_sha256": None if _linked(run, session.campaign_dir)
                            else _written_manifest_digest(run)})
        else:
            entries.append(run_entry(spec, run, state["mode"]))
    write_results(session.campaign_dir, session.campaign, entries, state["mode"])
    state["state"] = "complete"
    _save(session, state)
    job = _core_job(session, state)
    if job is not None and job.state not in core_jobs.FINAL:
        core_jobs.update_external(job, "done", 0)
    events.emit(session.run_dir, "note.experiment_remote", "system", kind="complete",
                campaign=session.campaign.name, campaign_id=session.cid,
                remote=session.remote.name, counts=_counts(state))


def _core_job(session: Session, state: dict):
    if not state.get("core_job"):
        return None
    return next((j for j in core_jobs.list_jobs(Project(session.root), session.run_dir)
                 if j.id == state["core_job"]), None)


def _log_counts(session: Session, state: dict) -> None:
    job = _core_job(session, state)
    if job is not None:  # counts only: never values or file contents
        counts = _counts(state)
        core_jobs.append_log(job, "counts " + " ".join(f"{k}={v}" for k, v in counts.items()))

