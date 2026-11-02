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
import os
import posixpath
import sys
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
from scieflow.experiments.stage import Stage, sif_path

RUNNER = None  # test seam: the transport runner; None is the real subprocess runner
STATE_FILE = "remote.json"
STATE_VERSION = 1
JOB_SCRIPT = "job.sh"
CHECK_OPS = ("check", "mkdir", "rsync", "fetch")
SCHEDULER_OPS = ("submit", "status")


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
                rc = R.rcmd.cmd_push(session.remote, session.t, str(session.bundle),
                                     session.remote_dir, workspace=session.run_dir,
                                     checksum=True, root=session.root)
    except R.policy.PolicyError as exc:
        raise _policy_error(exc) from exc
    _relay(buffer)
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


def resume_submission(campaign_dir, run_dir, *, runner=None) -> Outcome:
    """Continue a staged or running remote campaign: validate, check, push if staged, one submit pass.

    `scieflow experiment watch` (M-D3 t4) builds on this pass; a crash-retry uses it directly.
    """
    campaign_dir = Path(campaign_dir)
    run_dir = Path(run_dir)
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
                      python=remote_bundle.direct_python(remote.scheduler))
    session.t = R.transport.for_remote(remote, runner if runner is not None else RUNNER)
    _ticket(session)
    if state["state"] == "staged" and not _push(session, state):
        return _outcome(session, state, None)
    stop = _submit(session, state)
    return _outcome(session, state, stop)
