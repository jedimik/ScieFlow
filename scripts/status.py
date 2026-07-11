"""Run state: workspace/<slug>/status.yml transitions and resumability."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from sflib import config

_SCHEMA = yaml.safe_load((config.repo_root() / "schemas" / "status.yml").read_text())
PHASES: list = _SCHEMA["phases"]
STATES = set(_SCHEMA["states"])
STOP_REASONS = set(_SCHEMA["stop_reasons"])
APPROVAL_MODES = set(_SCHEMA["approval_modes"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_status(run: str, approval: str) -> dict:
    if approval not in APPROVAL_MODES:
        raise ValueError(f"approval must be one of {sorted(APPROVAL_MODES)}")
    return {
        "run": run,
        "created": _now(),
        "approval": approval,
        "iteration": 1,
        "phases": {p: "pending" for p in PHASES},
        "stopped": None,
    }


def read_status(ws: Path) -> dict:
    return yaml.safe_load((ws / "status.yml").read_text())


def write_status(ws: Path, st: dict) -> None:
    (ws / "status.yml").write_text(yaml.safe_dump(st, sort_keys=False))


def mark(st: dict, phase: str, state: str) -> dict:
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")
    st["phases"][phase] = state
    return st


def next_pending(st: dict) -> str | None:
    """First phase of the current iteration not marked done; None if all done."""
    for p in PHASES:
        if st["phases"][p] != "done":
            return p
    return None


def advance_iteration(st: dict) -> dict:
    if next_pending(st) is not None:
        raise ValueError("cannot advance: current iteration has unfinished phases")
    st["iteration"] += 1
    st["phases"] = {p: "pending" for p in PHASES}
    return st


def stop(st: dict, reason: str, detail: str = "", resume: str = "") -> dict:
    if reason not in STOP_REASONS:
        raise ValueError(f"unknown stop reason: {reason}")
    st["stopped"] = {"reason": reason, "at": _now(), "detail": detail, "resume": resume}
    return st


def clear_stop(st: dict) -> dict:
    """Resume: remove the stopped block recorded by stop()/checkpoint."""
    st["stopped"] = None
    return st
