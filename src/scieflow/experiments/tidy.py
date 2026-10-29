"""results.csv (M-D2 plan 5.6): one row per run and campaign metric, written from results.json.

Deterministic: the same results.json always gives the same bytes. A failed run and a run without a
value keep their rows (empty value); a non-finite value is written as inf, -inf or nan.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import yaml

from .manifest import atomic_write

CORE = ("campaign", "config_id", "run_id", "replicate", "seed", "mode", "status", "metric", "value")


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _campaign_metrics(root: Path, runs: list[dict]) -> list[str]:
    campaign_yaml = root / "campaign.yaml"
    if campaign_yaml.is_file():
        data = yaml.safe_load(campaign_yaml.read_text()) or {}
        if isinstance(data.get("metrics"), list):
            return [str(m) for m in data["metrics"]]
    return sorted({k for r in runs for k in (r.get("metrics") or {})})


def write_results_csv(campaign_dir, metrics: list[str] | None = None) -> Path:
    root = Path(campaign_dir)
    doc = json.loads((root / "results.json").read_text())
    runs = doc.get("runs", [])
    if metrics is None:
        metrics = _campaign_metrics(root, runs)
    params = sorted({k for r in runs for k in (r.get("params") or {})})
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([*CORE, *(f"param.{p}" for p in params)])
    for r in runs:
        run_params = r.get("params") or {}
        param_cells = [cell(run_params.get(p)) for p in params]
        for metric in metrics:
            value = (r.get("metrics") or {}).get(metric)
            writer.writerow([
                cell(doc.get("campaign")), cell(r.get("config_id")), cell(r.get("run_id")),
                cell(r.get("replicate")), cell(r.get("seed")), cell(r.get("mode")),
                cell(r.get("status")), metric, cell(value), *param_cells,
            ])
    path = root / "results.csv"
    atomic_write(path, buf.getvalue().encode("utf-8"))
    return path
