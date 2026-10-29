"""Synthetic campaign folders for the M-D2 t3 analysis tests (no patient data).

Loaded through importlib by the test modules (the tests run with --import-mode=importlib,
so they do not share a package). Values are the reference configurations of
reviews/m-d2-stats-reference.json: baseline A, configurations B and C, six replicates.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

CONFIGS = ["000_sigma-0.5", "001_sigma-1.0", "002_sigma-2.0"]
BASELINE = "001_sigma-1.0"
PARAMS = {
    "000_sigma-0.5": {"sigma": 0.5},
    "001_sigma-1.0": {"sigma": 1.0},
    "002_sigma-2.0": {"sigma": 2.0},
}
SSIM = {
    "000_sigma-0.5": [0.751, 0.768, 0.744, 0.772, 0.759, 0.763],
    "001_sigma-1.0": [0.712, 0.735, 0.698, 0.741, 0.722, 0.709],
    "002_sigma-2.0": [0.705, 0.731, 0.716, 0.750, 0.700, 0.724],
}


def write_campaign(root, *, name="stat-camp", values=None, analysis=None, replicated=True,
                   mode="apptainer", params=None, metrics=("ssim",), reference=True):
    """Write campaign.yaml and results.json for a synthetic sweep.

    values: {config_id: {metric: [value per replicate]}}; a None value leaves that run without
    the metric. reference=False writes runs without any metrics (a campaign without reference).
    """
    if values is None:
        values = {c: {"ssim": SSIM[c]} for c in CONFIGS}
    params = PARAMS if params is None else params
    campaign_dir = Path(root) / name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    n = len(next(iter(next(iter(values.values())).values())))
    camp = {
        "name": name, "pipeline": "demo", "stage": "seeded", "input": "../data/noisy.png",
        "metrics": list(metrics), "rank_by": metrics[0],
    }
    if replicated:
        camp["replicates"] = {"n": n, "seed": 0}
    if analysis is not None:
        camp["analysis"] = analysis
    runs = []
    for config, per_metric in values.items():
        for k in range(n):
            run_metrics = {}
            if reference:
                for metric, vals in per_metric.items():
                    if vals[k] is not None:
                        run_metrics[metric] = vals[k]
            runs.append({
                "run_id": f"{config}_r{k}" if replicated else config,
                "config_id": config,
                "replicate": k if replicated else None,
                "seed": k if replicated else None,
                "params": params[config],
                "mode": mode,
                "status": "completed",
                "metrics": run_metrics,
                "manifest_sha256": None,
            })
    doc = {"campaign": name, "rank_by": metrics[0], "mode": mode, "runs": runs}
    if replicated:
        doc["replicates"] = {"n": n, "seed": 0}
    (campaign_dir / "campaign.yaml").write_text(yaml.safe_dump(camp, sort_keys=False))
    (campaign_dir / "results.json").write_text(json.dumps(doc, indent=2))
    return campaign_dir


def edit_results(campaign_dir, edit) -> None:
    path = Path(campaign_dir) / "results.json"
    doc = json.loads(path.read_text())
    edit(doc)
    path.write_text(json.dumps(doc, indent=2))


def run_of(doc, run_id):
    return next(r for r in doc["runs"] if r["run_id"] == run_id)


def corrupt_results(campaign_dir, edit) -> None:
    """Write edit(results document) back as results.json; edit may return a new document."""
    path = Path(campaign_dir) / "results.json"
    path.write_text(json.dumps(edit(json.loads(path.read_text())), indent=2))


def _set_run(key, value):
    def edit(doc):
        doc["runs"][0][key] = value
        return doc
    return edit


def _drop_run_key(key):
    def edit(doc):
        del doc["runs"][0][key]
        return doc
    return edit


# Malformed results.json shapes: each must be refused with results.json named and no traceback.
MALFORMED = [
    ("top-level list", lambda doc: [doc]),
    ("run entry not a mapping", lambda doc: {**doc, "runs": ["000_sigma-0.5_r0"]}),
    ("run without status", _drop_run_key("status")),
    ("metrics not a mapping", _set_run("metrics", [0.7])),
    ("params not a mapping", _set_run("params", ["sigma"])),
    ("run_id not a string", _set_run("run_id", 7)),
    ("seed not an integer", _set_run("seed", [0])),
]
