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


def compare_campaign(campaign_dir: str | Path) -> dict:
    campaign_dir = Path(campaign_dir)
    campaign = yaml.safe_load((campaign_dir / "campaign.yaml").read_text())
    results = json.loads((campaign_dir / "results.json").read_text())
    if campaign.get("pipeline_dir"):
        load_local_metrics(Path(campaign["pipeline_dir"]) / "metrics")
    metric = get_metric(results["rank_by"])
    validation_rules = campaign.get("validation") or {}

    completed = [
        r for r in results["runs"]
        if r["status"] == "completed" and metric.name in r["metrics"]
    ]
    ranked = sorted(
        completed,
        key=lambda r: r["metrics"][metric.name],
        reverse=metric.higher_is_better,
    )
    ranked_ids = {r["run_id"] for r in ranked}
    comparison = {
        "campaign": results["campaign"],
        "rank_by": metric.name,
        "best": ranked[0]["run_id"] if ranked else None,
        "ranked": [
            {
                "run_id": r["run_id"],
                "params": r["params"],
                "metrics": r["metrics"],
                "validation": check_validation(r["metrics"], validation_rules),
            }
            for r in ranked
        ],
        "failed": [
            r["run_id"] for r in results["runs"] if r["run_id"] not in ranked_ids
        ],
    }
    (campaign_dir / "comparison.json").write_text(json.dumps(comparison, indent=2))
    return comparison
