"""Cross-campaign comparison (M-D2 plan 5.8): `scieflow experiment compare-campaigns`.

Reads each campaign's fresh stats.json (validated by tables.load_fresh_stats) and reports, per
campaign and shared metric, the best configuration by mean in the metric's direction. No
statistic is computed here and no test crosses campaigns: the inputs and stages differ.
Read-only; write_comparison writes only when asked for an --out file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import manifest, tables

TOOL = "scieflow experiment compare-campaigns"
NOTE = "no test across campaigns: inputs and stages differ"


class CrossCompareError(ValueError):
    pass


def _load(campaign_dir: Path) -> dict:
    try:
        return tables.load_fresh_stats(campaign_dir)
    except tables.RenderError as e:
        raise CrossCompareError(f"{campaign_dir}: {e}") from e


def _best(doc: dict, metric: str) -> dict:
    """The best configuration by mean (ties by config id); n = 0 everywhere gives no best."""
    higher = doc["metrics"][metric]["higher_is_better"]
    rows = [(row, row["metrics"][metric]) for row in doc["configs"]
            if row["metrics"][metric]["n"] >= 1 and row["metrics"][metric]["mean"] is not None]
    if not rows:
        return {"best": None, "reason": "no-values"}
    sign = -1 if higher else 1
    row, cell = min(rows, key=lambda rc: (sign * rc[1]["mean"], rc[0]["config_id"]))
    return {
        "best": {"config_id": row["config_id"], "params": row["params"], "n": cell["n"],
                 "mean": cell["mean"], "ci": cell["ci"]},
        "reason": None,
    }


def compare_campaigns(campaign_dirs, metrics=None) -> dict:
    """The comparison document of two or more campaign folders. Writes nothing."""
    dirs = [Path(d) for d in campaign_dirs]
    if len(dirs) < 2:
        raise CrossCompareError("compare-campaigns needs at least two campaign directories")
    loaded = [(d, _load(d)) for d in dirs]

    shared = set(loaded[0][1]["metrics"])
    for _, doc in loaded[1:]:
        shared &= set(doc["metrics"])
    if metrics:
        unshared = [m for m in metrics if m not in shared]
        if unshared:
            raise CrossCompareError(
                f"--metric {', '.join(unshared)}: not shared by every campaign")
        chosen = list(dict.fromkeys(metrics))
    else:
        if not shared:
            raise CrossCompareError("no shared metric: the campaigns' stats.json share no metric")
        chosen = sorted(shared)

    campaigns = []
    for d, doc in loaded:
        per_metric = {}
        for m in chosen:
            per_metric[m] = {"higher_is_better": doc["metrics"][m]["higher_is_better"],
                             **_best(doc, m)}
        campaigns.append({
            "dir": str(d),
            "campaign": doc["campaign"],
            "stats_sha256": manifest.file_sha256(d / "stats.json"),
            "design_status": doc["design"]["status"],
            "include_direct": doc["settings"]["include_direct"],
            "metrics": per_metric,
        })
    return {"version": 1, "tool": TOOL, "metrics": chosen, "campaigns": campaigns, "note": NOTE}


def write_comparison(path, doc: dict, force: bool) -> None:
    """Write the document atomically. An existing file needs force; a symlink is never written."""
    path = Path(path)
    if path.is_symlink():
        raise CrossCompareError(f"refusing to write through a symlink: {path}")
    if path.exists() and not force:
        raise CrossCompareError(f"{path} exists; pass --force to replace it")
    data = (json.dumps(doc, sort_keys=True, indent=1, allow_nan=False) + "\n").encode("utf-8")
    try:
        manifest.atomic_write(path, data)
    except OSError as e:
        raise CrossCompareError(f"{path}: cannot write ({e.strerror})") from e
