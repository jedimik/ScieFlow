"""Run actions: status and budget changes that are also recorded as events.

The functions in status.py and budget.py stay pure; these read, change, write
(under one lock) and log. The CLI, the web app and agents all change runs
through here — and budgets are enforced here, in code, rather than by an
agent remembering to check.
"""

from __future__ import annotations

from pathlib import Path

from scieflow.core import events, store
from scieflow.core.run import budget, status


class BudgetExhausted(RuntimeError):
    def __init__(self, dims: list[str]):
        super().__init__(f"budget exhausted: {', '.join(dims)}")
        self.dims = dims


def _update_status(ws: Path, fn) -> dict:
    return store.update_yaml(Path(ws) / "status.yml", fn)


def mark_phase(ws: Path, phase: str, state: str, actor: str = "agent") -> dict:
    st = _update_status(ws, lambda s: status.mark(s, phase, state))
    kind = "phase.started" if state == "running" else f"phase.{state}"
    events.emit(ws, kind, actor, phase=phase, iteration=st["iteration"])
    return st


def checkpoint_run(ws: Path, reason: str, detail: str = "", actor: str = "agent") -> dict:
    from scieflow.core.run import checkpoint

    st = checkpoint.checkpoint(Path(ws), reason, detail)
    events.emit(ws, "checkpoint", actor, reason=reason, detail=detail)
    return st


def resume(ws: Path, actor: str = "agent") -> dict:
    st = _update_status(ws, status.clear_stop)
    events.emit(ws, "run.resumed", actor)
    return st


def record_spend(ws: Path, actor: str = "system", **spent) -> dict | None:
    path = Path(ws) / "budget.yml"
    if not path.exists():
        return None
    b = store.update_yaml(path, lambda cur: budget.record(cur, **spent))
    events.emit(ws, "budget.recorded", actor, **spent)
    return b


def _refuse(ws: Path, dims: list[str], detail: str) -> None:
    st = status.read_status(ws) or {}
    if not st.get("stopped"):
        checkpoint_run(ws, "low-budget", detail, actor="system")
    events.emit(ws, "job.refused", "system", reason="budget", dims=dims)
    raise BudgetExhausted(dims)


def guard_budget(ws: Path, dims: tuple[str, ...]) -> None:
    """Refuse work that would spend an exhausted dimension; checkpoint once."""
    path = Path(ws) / "budget.yml"
    if not path.exists():
        return
    b = store.read_yaml(path)
    out = [d for d in dims if budget.is_exhausted(b, d)]
    if out:
        _refuse(ws, out, f"budget exhausted: {', '.join(out)}")


def advance_iteration(ws: Path, actor: str = "agent") -> dict:
    path = Path(ws) / "budget.yml"
    if path.exists() and budget.is_exhausted(store.read_yaml(path), "iterations"):
        _refuse(ws, ["iterations"], "iterations exhausted")
    st = _update_status(ws, status.advance_iteration)
    events.emit(ws, "iteration.advanced", actor, iteration=st["iteration"])
    return st


def run_for_path(path: Path) -> Path | None:
    """The run workspace (workspace/<slug>/ with a status.yml) containing `path`."""
    p = Path(path).resolve()
    for candidate in [p, *p.parents]:
        if candidate.parent.name == "workspace" and (candidate / "status.yml").exists():
            return candidate
    return None
