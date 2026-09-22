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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, store
from scieflow.core.project import Project

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

    @property
    def duration_s(self) -> float | None:
        if not (self.started and self.finished):
            return None
        a = datetime.fromisoformat(self.started)
        b = datetime.fromisoformat(self.finished)
        return max(0.0, (b - a).total_seconds())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
          stdin_text: str | None = None, env: dict | None = None) -> tuple[Job, subprocess.Popen]:
    job_id = store.new_id()
    directory = jobs_dir(project, run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, kind=kind, argv=list(argv), cwd=str(cwd), label=label,
              run_dir=str(run_dir) if run_dir else None, queued=_now(), timeout_s=timeout_s,
              log=str(directory / f"{job_id}.log"), err=str(directory / f"{job_id}.err"))
    save(job)
    _emit(job, "job.queued")
    with open(job.log, "w") as out, open(job.err, "w") as err:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdout=out, stderr=err, text=True, env=env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
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
