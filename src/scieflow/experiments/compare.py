from __future__ import annotations

import json
from pathlib import Path

import yaml

from .metrics import get_metric, load_local_metrics


def check_validation(metrics: dict, rules: dict) -> dict:
    checks = {}
    for metric, rule in rules.items():
        value = metrics.get(metric)
        passed = value is not None
        if passed and "min" in rule:
            passed = value >= rule["min"]
        if passed and "max" in rule:
            passed = value <= rule["max"]
        checks[metric] = {"value": value, "rule": rule, "passed": bool(passed)}
    return {
        "passed": all(c["passed"] for c in checks.values()),
        "checks": checks,
    }


def _run_mode(run: dict, campaign_dir: Path) -> str | None:
    """The mode a run was recorded in: results.json first, then its environment.json."""
    if run.get("mode"):
        return run["mode"]
    try:
        env = json.loads(
            (campaign_dir / "runs" / run["run_id"] / "environment.json").read_text()
        )
        return env["mode"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def compare_campaign(campaign_dir: str | Path, include_direct: bool = False) -> dict:
    """Rank a campaign's completed runs.

    ``--direct`` runs are development-only, so they are not ranked unless
    ``include_direct`` is set; they are listed under ``direct`` instead. Runs
    whose mode cannot be determined stay ranked (legacy behaviour) and are
    listed under ``mode_unknown``.
    """
    campaign_dir = Path(campaign_dir)
    campaign = yaml.safe_load((campaign_dir / "campaign.yaml").read_text())
    results = json.loads((campaign_dir / "results.json").read_text())
    if campaign.get("pipeline_dir"):
        load_local_metrics(Path(campaign["pipeline_dir"]) / "metrics")
    metric = get_metric(results["rank_by"])
    validation_rules = campaign.get("validation") or {}

    modes = {r["run_id"]: _run_mode(r, campaign_dir) for r in results["runs"]}
    completed = [
        r for r in results["runs"]
        if r["status"] == "completed" and metric.name in r["metrics"]
    ]
    def by_metric(runs: list[dict]) -> list[dict]:
        return sorted(
            runs,
            key=lambda r: r["metrics"][metric.name],
            reverse=metric.higher_is_better,
        )

    direct_runs = [r for r in completed if modes[r["run_id"]] == "direct"]
    ranked = by_metric(
        completed if include_direct
        else [r for r in completed if modes[r["run_id"]] != "direct"]
    )
    direct = [] if include_direct else by_metric(direct_runs)
    ranked_ids = {r["run_id"] for r in ranked}
    excluded_ids = {r["run_id"] for r in direct}

    def entry(r: dict) -> dict:
        return {
            "run_id": r["run_id"],
            "params": r["params"],
            "mode": modes[r["run_id"]] or "unknown",
            "metrics": r["metrics"],
            "validation": check_validation(r["metrics"], validation_rules),
        }

    comparison = {
        "campaign": results["campaign"],
        "rank_by": metric.name,
        "include_direct": include_direct,
        "best": ranked[0]["run_id"] if ranked else None,
        "ranked": [entry(r) for r in ranked],
        "direct": [entry(r) for r in direct],
        "mode_unknown": [
            r["run_id"] for r in results["runs"] if modes[r["run_id"]] is None
        ],
        "failed": [
            r["run_id"] for r in results["runs"]
            if r["run_id"] not in ranked_ids and r["run_id"] not in excluded_ids
        ],
    }
    (campaign_dir / "comparison.json").write_text(json.dumps(comparison, indent=2))
    return comparison
