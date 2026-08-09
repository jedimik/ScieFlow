"""Job ledger at workspace/<slug>/remote/jobs.yml: states and attempt counts.

`attempt` counts submissions per task name — the fix-and-resubmit ceiling
(limits.max_fix_attempts) is enforced against it by remote.py submit.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml

STATES = ("queued", "running", "done", "failed")


def _path(workspace: Path) -> Path:
    return workspace / "remote" / "jobs.yml"


def load_jobs(workspace: Path) -> list:
    path = _path(workspace)
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save_jobs(workspace: Path, jobs: list) -> None:
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(jobs, sort_keys=False))


def attempts(jobs: list, task: str) -> int:
    return sum(1 for j in jobs if j["task"] == task)


def active_count(jobs: list, remote_name: str) -> int:
    return sum(
        1 for j in jobs
        if j["remote"] == remote_name and j["state"] in ("queued", "running")
    )


def record_submit(jobs: list, *, task: str, job_id: str, remote_name: str,
                  remote_dir: str, script: str, resources: dict,
                  environment: dict | None = None,
                  job_name: str | None = None) -> dict:
    entry = {
        "task": task,
        "job_id": job_id,
        "remote": remote_name,
        "dir": remote_dir,
        "script": script,
        "job_name": job_name or Path(script).stem,
        "resources": resources,
        "environment": environment or {},
        "state": "queued",
        "attempt": attempts(jobs, task) + 1,
        "submitted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    jobs.append(entry)
    return entry


def set_state(jobs: list, job_id: str, state: str) -> None:
    if state not in STATES:
        raise ValueError(f"unknown state '{state}' (one of {', '.join(STATES)})")
    for j in jobs:
        if j["job_id"] == job_id:
            j["state"] = state
            return
    raise KeyError(job_id)
