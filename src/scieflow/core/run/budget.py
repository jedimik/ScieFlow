"""Budget ledger: workspace/<slug>/budget.yml, spend recording, low-budget detection."""

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import store

DIMENSIONS = {
    "iterations": "max_iterations",
    "experiment_runs": "max_experiment_runs",
    "wall_minutes": "max_wall_minutes",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_budget(max_iterations: int, max_experiment_runs: int, max_wall_minutes: int) -> dict:
    """A fresh budget ledger. 0 means unlimited (see `is_exhausted`); a
    negative cap is refused here rather than left to callers — otherwise
    `is_exhausted` reports the run exhausted before its first dispatch, since
    any non-negative spend (including 0) is already `>=` a negative cap."""
    for name, value in (("max_iterations", max_iterations),
                        ("max_experiment_runs", max_experiment_runs),
                        ("max_wall_minutes", max_wall_minutes)):
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative: {value}")
    return {
        "budgets": {
            "max_iterations": max_iterations,
            "max_experiment_runs": max_experiment_runs,
            "max_wall_minutes": max_wall_minutes,
        },
        "spent": {k: 0 for k in DIMENSIONS},
        "started": _now().isoformat(timespec="seconds"),
    }


def read_budget(ws: Path) -> dict:
    return store.read_yaml(Path(ws) / "budget.yml")


def write_budget(ws: Path, b: dict) -> None:
    store.write_yaml(Path(ws) / "budget.yml", b)


def is_exhausted(b: dict, dim: str) -> bool:
    cap = b["budgets"][DIMENSIONS[dim]]
    return bool(cap) and b["spent"][dim] >= cap


def record(b: dict, **spent) -> dict:
    """Add increments, e.g. record(b, iterations=1, experiment_runs=6). All-or-nothing.

    Negative values are rejected here rather than left to callers, because
    this is the one place every spend-recording path — the CLI's
    `scieflow run spend`, the service layer's `record_spend` (and therefore
    the web app), and `actions.record_spend`'s own internal callers — funnels
    through. A negative increment would silently un-spend the budget ledger,
    defeating the one automatic brake on runaway agent spend.
    """
    for k, v in spent.items():
        if k not in DIMENSIONS:
            raise ValueError(f"unknown budget dimension: {k}")
        if v < 0:
            raise ValueError(f"spend cannot be negative: {k}={v}")
    for k, v in spent.items():
        b["spent"][k] += v
    return b


def set_wall_from_clock(b: dict, now: datetime | None = None) -> dict:
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware (use datetime.now(timezone.utc))")
    started = datetime.fromisoformat(b["started"])
    elapsed = ((now or _now()) - started).total_seconds() / 60
    b["spent"]["wall_minutes"] = max(0.0, elapsed)
    return b


def remaining_fraction(b: dict) -> dict:
    out = {}
    for dim, cap_key in DIMENSIONS.items():
        cap = b["budgets"][cap_key]
        out[dim] = max(0.0, (cap - b["spent"][dim]) / cap) if cap else 0.0
    return out


def low_dimensions(b: dict, threshold: float = 0.10) -> list[str]:
    """Dimensions at or below the low-budget threshold — time to checkpoint."""
    return [d for d, f in remaining_fraction(b).items() if f <= threshold]


def exhausted(b: dict) -> list[str]:
    return [d for d, f in remaining_fraction(b).items() if f <= 0.0]
