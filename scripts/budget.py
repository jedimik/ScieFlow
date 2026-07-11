"""Budget ledger: workspace/<slug>/budget.yml, spend recording, low-budget detection."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

DIMENSIONS = {
    "iterations": "max_iterations",
    "experiment_runs": "max_experiment_runs",
    "wall_minutes": "max_wall_minutes",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_budget(max_iterations: int, max_experiment_runs: int, max_wall_minutes: int) -> dict:
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
    return yaml.safe_load((ws / "budget.yml").read_text())


def write_budget(ws: Path, b: dict) -> None:
    (ws / "budget.yml").write_text(yaml.safe_dump(b, sort_keys=False))


def record(b: dict, **spent) -> dict:
    """Add increments, e.g. record(b, iterations=1, experiment_runs=6). All-or-nothing."""
    for k in spent:
        if k not in DIMENSIONS:
            raise ValueError(f"unknown budget dimension: {k}")
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
