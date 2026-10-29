from __future__ import annotations

import json
import math
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
    if results.get("replicates") is not None:
        comparison["configs"] = _config_ranking(
            results["runs"], ranked, metric.name, metric.higher_is_better,
            ranked_ids, excluded_ids)
        comparison["best_config"] = next(
            (c["config_id"] for c in comparison["configs"] if c["mean"] is not None), None)
    (campaign_dir / "comparison.json").write_text(json.dumps(comparison, indent=2))
    return comparison


def _config_ranking(runs: list[dict], ranked: list[dict], metric: str,
                    higher_is_better: bool, ranked_ids: set, excluded_ids: set) -> list[dict]:
    """Replicate campaigns: one entry per configuration, ordered by mean of the rank metric.

    A configuration with no ranked completed run keeps its entry (n 0, mean null) and sorts
    last, so its failed replicates stay visible in `n_failed`. A mean that is not finite
    (inf, -inf or nan among its runs) is written as null, sorts last and is never best.
    """
    values = {r["run_id"]: r["metrics"][metric] for r in ranked}
    by_config: dict[str, list[dict]] = {}
    for r in runs:
        by_config.setdefault(r["config_id"], []).append(r)
    configs = []
    for config_id, members in by_config.items():
        got = [m["run_id"] for m in members if m["run_id"] in values]
        mean = sum(values[rid] for rid in got) / len(got) if got else None
        if mean is not None and not math.isfinite(mean):
            mean = None
        configs.append({
            "config_id": config_id,
            "params": members[0]["params"],
            "n": len(got),
            "n_failed": sum(1 for m in members
                            if m["run_id"] not in ranked_ids and m["run_id"] not in excluded_ids),
            "mean": mean,
            "runs": got,
        })
    sign = -1 if higher_is_better else 1
    configs.sort(key=lambda c: (c["mean"] is None,
                                sign * c["mean"] if c["mean"] is not None else 0,
                                c["config_id"]))
    return configs
