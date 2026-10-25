"""Job ledger at workspace/<slug>/remote/jobs.yml: states and attempt counts.

`attempt` counts submissions per task name — the fix-and-resubmit ceiling
(limits.max_fix_attempts) is enforced against it by remote.py submit.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import store

STATES = ("queued", "running", "done", "failed", "cancelled", "unknown")
# "unknown" stays active so it never frees a concurrency slot (M-E-D6).
ACTIVE_STATES = ("queued", "running", "unknown")
# Ledger entries written before the seam carry no scheduler; they are PBS.
DEFAULT_SCHEDULER = "pbs"


def _path(workspace: Path) -> Path:
    return workspace / "remote" / "jobs.yml"


def load_jobs(workspace: Path) -> list:
    path = _path(workspace)
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save_jobs(workspace: Path, jobs: list) -> None:
    store.write_yaml(_path(workspace), jobs)


def attempts(jobs: list, task: str) -> int:
    return sum(1 for j in jobs if j["task"] == task)


def active_count(jobs: list, remote_name: str) -> int:
    return sum(
        1 for j in jobs
        if j["remote"] == remote_name and j["state"] in ACTIVE_STATES
    )


def find(jobs: list, job_id: str) -> dict | None:
    """Return the first ledger entry with this job id, or None."""
    return next((j for j in jobs if j["job_id"] == job_id), None)


def entry_scheduler(entry: dict) -> str:
    return entry.get("scheduler", DEFAULT_SCHEDULER)


def record_submit(jobs: list, *, task: str, job_id: str, remote_name: str,
                  remote_dir: str, script: str, resources: dict,
                  environment: dict | None = None,
                  job_name: str | None = None,
                  scheduler: str = DEFAULT_SCHEDULER,
                  kind: str = "job") -> dict:
    entry = {
        "task": task,
        "job_id": job_id,
        "remote": remote_name,
        "dir": remote_dir,
        "script": script,
        "job_name": job_name or Path(script).stem,
        "resources": resources,
        "environment": environment or {},
        "scheduler": scheduler,
        "state": "queued",
        "attempt": attempts(jobs, task) + 1,
        "submitted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Real jobs keep the entry shape they had before M-E; only dry runs say so.
    if kind != "job":
        entry["kind"] = kind
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
