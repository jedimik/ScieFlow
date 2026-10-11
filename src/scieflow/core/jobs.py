"""Job runner: every long-running thing is a tracked, cancellable job.

A job is a subprocess in its own process group, with stdout and stderr streamed
to files while it runs and a JSON record of what it is and what state it is in.
The CLI waits for it; the web app starts it and watches it. Output is never
lost: a timeout keeps everything the process already wrote.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import stat

from scieflow.core import events, sandbox, store
from scieflow.core.project import Project
from scieflow.core.turn_artifacts import ArtifactError, ArtifactRoot, open_run_artifact, pin_artifact_root

STATES = ("queued", "running", "done", "failed", "timeout", "cancelled", "lost")
FINAL = frozenset({"done", "failed", "timeout", "cancelled", "lost"})
TIMEOUT_EXIT = 124
KILL_GRACE = 10.0


@dataclass
class Job:
    id: str
    kind: str
    argv: list[str]
    cwd: str
    label: str = ""
    run_dir: str | None = None
    state: str = "queued"
    pid: int | None = None
    queued: str = ""
    started: str | None = None
    finished: str | None = None
    exit_code: int | None = None
    timeout_s: float | None = None
    log: str = ""
    err: str = ""
    sandboxed: bool = False
    turn_audit: dict | None = None

    def __post_init__(self):
        if self.turn_audit is not None:
            from scieflow.core.run.conversation import validate_audit
            self.turn_audit = validate_audit(self.turn_audit)

    @property
    def duration_s(self) -> float | None:
        if not (self.started and self.finished):
            return None
        a = datetime.fromisoformat(self.started)
        b = datetime.fromisoformat(self.finished)
        return max(0.0, (b - a).total_seconds())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JobArtifactError(ValueError):
    """Fixed safe reader failures, never raw record/path exception text."""


def _artifact_job_id(value):
    if (type(value) is not str or len(value) != 26 or value[0] not in '01234567' or
            any(char not in store._CROCKFORD for char in value)):
        raise JobArtifactError('invalid-job-id')


def _artifact_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise JobArtifactError('invalid-job-record')
        value[key] = item
    return value


def _artifact_constant(value):
    raise JobArtifactError('invalid-job-record')


@dataclass(frozen=True)
class JobArtifactLocation:
    """Detached records are data; only this active pinned root selects bytes."""
    job_id: str
    run_dir: Path | None
    root: ArtifactRoot
    _parent_root: ArtifactRoot = field(repr=False)

    @property
    def relative_log(self):
        return f'jobs/{self.job_id}.log'

    @property
    def relative_err(self):
        return f'jobs/{self.job_id}.err'

    def read_job(self) -> Job:
        try:
            self.root._verify()
            with open_run_artifact(self._parent_root, f'{self.job_id}.json') as source:
                chunks, offset = [], 0
                while offset < source.snapshot_size:
                    requested = min(65536, source.snapshot_size - offset)
                    raw = source.read_at(offset, requested)
                    if len(raw) != requested:
                        raise JobArtifactError('invalid-job-record')
                    chunks.append(raw)
                    offset += len(raw)
                data = json.loads(b''.join(chunks), object_pairs_hook=_artifact_pairs,
                                  parse_constant=_artifact_constant)
                source.verify_unchanged()
                if type(data) is not dict:
                    raise JobArtifactError('invalid-job-record')
                value = Job(**data)
                for name in ('id', 'kind', 'cwd', 'label', 'state', 'queued', 'log', 'err'):
                    if type(getattr(value, name)) is not str:
                        raise JobArtifactError('invalid-job-record')
                if (type(value.argv) is not list or any(type(arg) is not str for arg in value.argv) or
                        type(value.sandboxed) is not bool):
                    raise JobArtifactError('invalid-job-record')
                for name in ('pid', 'exit_code'):
                    if getattr(value, name) is not None and type(getattr(value, name)) is not int:
                        raise JobArtifactError('invalid-job-record')
                for name in ('run_dir', 'started', 'finished'):
                    if getattr(value, name) is not None and type(getattr(value, name)) is not str:
                        raise JobArtifactError('invalid-job-record')
                if value.timeout_s is not None and type(value.timeout_s) not in (int, float):
                    raise JobArtifactError('invalid-job-record')
                if (value.id != self.job_id or
                        value.run_dir != (str(self.run_dir) if self.run_dir is not None else None) or
                        value.log != str(self.root.path / self.relative_log) or
                        value.err != str(self.root.path / self.relative_err) or value.state not in STATES):
                    raise JobArtifactError('invalid-job-record')
                source.verify_unchanged()
                return value
        except (ArtifactError, OSError, ValueError, TypeError, RecursionError) as exc:
            raise JobArtifactError('invalid-job-record') from exc

    def peek_state(self) -> str | None:
        try:
            return self.read_job().state
        except JobArtifactError:
            return None

    @contextmanager
    def open_log(self, stream='stdout'):
        if type(stream) is not str or stream not in ('stdout', 'stderr'):
            raise JobArtifactError('invalid-job-stream')
        relative = self.relative_log if stream == 'stdout' else self.relative_err
        try:
            self.root._verify()
            with open_run_artifact(self._parent_root, relative.removeprefix('jobs/')) as source:
                yield source
        except (ArtifactError, OSError) as exc:
            raise JobArtifactError('unsafe-job-log') from exc


def _artifact_canonical(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or '\0' in str(path):
        raise JobArtifactError('invalid-job-root')
    return path


def _artifact_candidate(stack, root_path, run_dir, job_id, expected=None):
    # Missing roots/records are absence; unsafe matching entries are refusal.
    try:
        root_path.lstat()
    except FileNotFoundError:
        return None
    root = stack.enter_context(pin_artifact_root(root_path, expected_identity=expected))
    try:
        info = os.stat('jobs', dir_fd=root._fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise JobArtifactError('unsafe-job-root')
    # Traversal into the parent is descriptor-owned, never a following lookup.
    parent = stack.enter_context(pin_artifact_root(root_path / 'jobs',
        expected_identity=(info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))))
    try:
        os.stat(f'{job_id}.json', dir_fd=parent._fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    parent._verify()
    root._verify()
    location = JobArtifactLocation(job_id, run_dir, root, parent)
    location.read_job()
    return location


@contextmanager
def locate_artifacts(project: Project, job_id: str, *, run_dir: Path | None = None,
                     expected_run_identity: tuple[int, int, int] | None = None):
    """Acquire one selected owner; discover global roots once, poll only it."""
    _artifact_job_id(job_id)  # BEFORE even project/path lookup.
    stacks = []
    try:
        project_root = _artifact_canonical(project.root)
        workspace = _artifact_canonical(project.workspace_root)
        if workspace != project_root / 'workspace':
            raise JobArtifactError('invalid-job-root')
        if run_dir is not None:
            selected = _artifact_canonical(run_dir)
            if selected.parent != workspace:
                raise JobArtifactError('invalid-job-root')
            owner = ExitStack()
            stacks.append(owner)
            location = _artifact_candidate(owner, selected, selected, job_id, expected_run_identity)
            if location is None:
                raise JobArtifactError('missing-job-record')
        else:
            if expected_run_identity is not None:
                raise JobArtifactError('invalid-expected-run-identity')
            state = _artifact_canonical(project.state_dir)  # Host override captured ONCE.
            candidates: list[tuple[Path, Path | None]] = [(state, None)]
            try:
                workspace.lstat()
            except FileNotFoundError:
                pass
            else:
                with pin_artifact_root(workspace) as workspace_handle:
                    with os.scandir(workspace_handle._fd) as entries:
                        candidates.extend((workspace / entry.name, workspace / entry.name)
                                          for entry in entries if entry.is_dir(follow_symlinks=False))
                    workspace_handle._verify()
            matches = []
            for path, owning_run in candidates:
                owner = ExitStack()
                stacks.append(owner)
                match = _artifact_candidate(owner, path, owning_run, job_id)
                if match is None:
                    owner.close()
                else:
                    matches.append((owner, match))
            if len(matches) != 1:
                raise JobArtifactError('ambiguous-job-record' if matches else 'missing-job-record')
            owner, location = matches[0]
            for other in stacks:
                if other is not owner:
                    other.close()
        yield location
    except (ArtifactError, OSError, TypeError) as exc:
        raise JobArtifactError('unsafe-job-location') from exc
    finally:
        for stack in reversed(stacks):
            stack.close()


def jobs_dir(project: Project, run_dir: Path | None) -> Path:
    return Path(run_dir) / "jobs" if run_dir else project.state_dir / "jobs"


def _record_path(job: Job) -> Path:
    return Path(job.log).with_suffix(".json")


def save(job: Job) -> None:
    store.write_text(_record_path(job), json.dumps(asdict(job), indent=2))


def load(path: Path) -> Job:
    return Job(**json.loads(Path(path).read_text()))


def _reload(job: Job) -> Job:
    try:
        return load(_record_path(job))
    except (OSError, ValueError, TypeError):
        return job


def peek_state(log: Path) -> str | None:
    """Read a single job's current state directly from its record file.

    `log` is a job's log path (e.g. `Job.log`); the record lives beside it at
    the same path with a `.json` suffix, per `_record_path`. This is O(1) in
    the number of jobs -- unlike `find`/`list_jobs`, it never globs or loads
    any other job's record -- for callers on a hot path (a poll loop) that
    only need to know whether *this* job has reached a final state.

    Records are written atomically via `os.replace`, so a torn read should
    not normally happen, but any failure to read or parse -- file missing,
    unreadable, malformed -- is treated as "state unknown" (returns `None`)
    rather than raised, so a caller polling in a loop can simply keep
    polling instead of crashing on a transient hiccup.
    """
    try:
        return json.loads(Path(log).with_suffix(".json").read_text())["state"]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def list_jobs(project: Project, run_dir: Path | None = None) -> list[Job]:
    dirs = ([jobs_dir(project, run_dir)] if run_dir else
            [project.state_dir / "jobs", *sorted(project.workspace_root.glob("*/jobs"))])
    out = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                out.append(load(path))
            except (OSError, ValueError, TypeError):
                continue
    return sorted(out, key=lambda j: j.id)


def find(project: Project, job_id: str) -> Job | None:
    return next((j for j in list_jobs(project) if j.id == job_id), None)


def _emit(job: Job, type_: str, **data) -> None:
    if job.run_dir:
        events.emit(Path(job.run_dir), type_, "system", job=job.id, kind=job.kind,
                    label=job.label, **data)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(project: Project, argv: list[str], *, kind: str, cwd: Path,
          run_dir: Path | None = None, label: str = "", timeout_s: float | None = None,
          stdin_text: str | None = None, env: dict | None = None,
          sandbox_writable: list[Path] | None = None, turn_audit=None,
          sandbox_mask: list[Path] | None = None) -> tuple[Job, subprocess.Popen]:
    job_id = store.new_id()
    directory = jobs_dir(project, run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, kind=kind, argv=list(argv), cwd=str(cwd), label=label,
              run_dir=str(run_dir) if run_dir else None, queued=_now(), timeout_s=timeout_s,
              log=str(directory / f"{job_id}.log"), err=str(directory / f"{job_id}.err"),
              sandboxed=sandbox_writable is not None, turn_audit=turn_audit)
    if job.turn_audit is not None:
        job.turn_audit['job_id'] = job_id
    captured_mask = list(sandbox_mask) if sandbox_mask is not None else None
    save(job)
    _emit(job, "job.queued")
    with open(job.log, "w") as out, open(job.err, "w") as err:
        try:
            # The job records the command that was asked for; the wrapper is plumbing.
            launch = (sandbox.wrap(argv, writable=sandbox_writable, cwd=cwd,
                                   mask=(captured_mask if captured_mask is not None else
                                         sandbox.masks_for(sandbox_writable, run_dir=run_dir)))
                      if sandbox_writable is not None else list(argv))
            proc = subprocess.Popen(
                launch, cwd=cwd, stdout=out, stderr=err, text=True, env=env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, sandbox.SandboxError) as exc:
            err.write(f"failed to launch: {exc}\n")
            job.state, job.finished = "failed", _now()
            save(job)
            _emit(job, "job.failed", error=str(exc))
            raise
    job.state, job.pid, job.started = "running", proc.pid, _now()
    save(job)
    _emit(job, "job.started", pid=proc.pid)
    if stdin_text is not None:
        def feed() -> None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        threading.Thread(target=feed, daemon=True).start()
    return job, proc


def _kill_group(pid: int, proc: subprocess.Popen | None = None, grace: float = KILL_GRACE) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        done = proc.poll() is not None if proc is not None else not _alive(pid)
        if done:
            break
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)   # also takes down children left behind
    except (ProcessLookupError, PermissionError):
        pass


def _finish(job: Job, state: str, code: int | None) -> Job:
    job.state, job.exit_code, job.finished = state, code, _now()
    save(job)
    kind = {"done": "job.finished", "failed": "job.failed", "timeout": "job.timeout"}[state]
    _emit(job, kind, exit_code=code, duration_s=job.duration_s)
    return job


def wait(job: Job, proc: subprocess.Popen) -> Job:
    try:
        code = proc.wait(timeout=job.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, proc)
        proc.wait()
        return _finish(job, "timeout", TIMEOUT_EXIT)
    current = _reload(job)
    if current.state == "cancelled":       # cancelled by another caller meanwhile
        return current
    return _finish(job, "done" if code == 0 else "failed", code)


def run_blocking(project: Project, argv: list[str], **kw) -> Job:
    job, proc = start(project, argv, **kw)
    return wait(job, proc)


def cancel(job: Job) -> Job:
    current = _reload(job)
    if current.state in FINAL:
        return current
    current.state, current.finished = "cancelled", _now()
    save(current)
    _emit(current, "job.cancelled")
    if current.pid:
        _kill_group(current.pid)
    return current


def reconcile(project: Project, run_dir: Path | None = None) -> list[Job]:
    """Jobs recorded as running whose process is gone (host restart) become lost."""
    lost = []
    for job in list_jobs(project, run_dir):
        if job.state == "running" and job.pid and not _alive(job.pid):
            job.state, job.finished = "lost", _now()
            save(job)
            _emit(job, "job.lost")
            lost.append(job)
    return lost
