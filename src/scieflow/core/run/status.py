"""Run state: workspace/<slug>/status.yml transitions and resumability.

The vocabulary (phases, states, stop reasons, approval modes) is data in
schemas/status.yml, loaded on first use — importing this module does no I/O.
`PHASES`, `STATES`, `STOP_REASONS` and `APPROVAL_MODES` stay available as
module attributes for existing callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import store
from scieflow.core.project import Project

_VOCAB: dict | None = None


def vocab() -> dict:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = Project.discover().schema("status")
    return _VOCAB


def __getattr__(name: str):
    if name == "PHASES":
        return list(vocab()["phases"])
    if name == "STATES":
        return set(vocab()["states"])
    if name == "STOP_REASONS":
        return set(vocab()["stop_reasons"])
    if name == "APPROVAL_MODES":
        return set(vocab()["approval_modes"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_status(run: str, approval: str) -> dict:
    if approval not in vocab()["approval_modes"]:
        raise ValueError(f"approval must be one of {sorted(vocab()['approval_modes'])}")
    return {
        "run": run,
        "id": store.new_id(),
        "created": _now(),
        "approval": approval,
        "iteration": 1,
        "phases": {p: "pending" for p in vocab()["phases"]},
        "stopped": None,
    }


def read_status(ws: Path) -> dict:
    return store.read_yaml(Path(ws) / "status.yml")


def write_status(ws: Path, st: dict) -> None:
    store.write_yaml(Path(ws) / "status.yml", st)


def ensure_id(ws: Path) -> str:
    """Give an existing run a stable id the first time it is needed."""
    def add(st: dict) -> dict:
        st.setdefault("id", store.new_id())
        return st
    return store.update_yaml(Path(ws) / "status.yml", add)["id"]


def mark(st: dict, phase: str, state: str) -> dict:
    if phase not in vocab()["phases"]:
        raise ValueError(f"unknown phase: {phase}")
    if state not in vocab()["states"]:
        raise ValueError(f"unknown state: {state}")
    st["phases"][phase] = state
    return st


def next_pending(st: dict) -> str | None:
    """First phase of the current iteration not marked done; None if all done."""
    for p in vocab()["phases"]:
        if st["phases"][p] != "done":
            return p
    return None


def advance_iteration(st: dict) -> dict:
    if next_pending(st) is not None:
        raise ValueError("cannot advance: current iteration has unfinished phases")
    st["iteration"] += 1
    st["phases"] = {p: "pending" for p in vocab()["phases"]}
    return st


def stop(st: dict, reason: str, detail: str = "", resume: str = "") -> dict:
    if reason not in vocab()["stop_reasons"]:
        raise ValueError(f"unknown stop reason: {reason}")
    st["stopped"] = {"reason": reason, "at": _now(), "detail": detail, "resume": resume}
    return st


def clear_stop(st: dict) -> dict:
    """Resume: remove the stopped block recorded by stop()/checkpoint."""
    st["stopped"] = None
    return st
