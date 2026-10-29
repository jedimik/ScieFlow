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


def _load(campaign_dir: Path) -> tuple[dict, list[str]]:
    """The validated stats.json and the campaign's metric names (the only metrics validated)."""
    try:
        doc = tables.load_fresh_stats(campaign_dir)
        names = tables.campaign_metrics(campaign_dir)
    except tables.RenderError as e:
        raise CrossCompareError(f"{campaign_dir}: {e}") from e
    if not all(isinstance(m, str) for m in names):
        raise CrossCompareError(f"{campaign_dir}: campaign.yaml: metrics: names are required")
    # Keys this module indexes that tables.load_fresh_stats does not check (plan 5.12, fail closed).
    if not isinstance(doc["campaign"], str):
        raise CrossCompareError(f"{campaign_dir}: stats.json: campaign: a string is required")
    if not isinstance(doc["settings"].get("include_direct"), bool):
        raise CrossCompareError(f"{campaign_dir}: stats.json: settings.include_direct: "
                                f"a boolean is required")
    return doc, names


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
    if len({d.resolve() for d in dirs}) < 2:
        raise CrossCompareError(
            "compare-campaigns needs at least two distinct campaign directories")
    loaded = [(d, *_load(d)) for d in dirs]

    shared = set(loaded[0][2])
    for _, _, names in loaded[1:]:
        shared &= set(names)
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
    for d, doc, _ in loaded:
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
        # The hardened path of tables.write_outputs: refuses a symlink or non-file at the output and
        # at its .tmp sibling, and creates the .tmp with O_EXCL (review r1 finding 1).
        tables.write_outputs([(path, data)])
    except tables.RenderError as e:
        raise CrossCompareError(str(e)) from e
    except OSError as e:
        raise CrossCompareError(f"{path}: cannot write ({e.strerror})") from e
