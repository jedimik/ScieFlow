"""Synthetic stats.json documents for the M-D2 t5 tables, figures and report tests (plan 5.7).

Synthetic values only (no patient data). Loaded through importlib by the test modules, which run
with --import-mode=importlib. The campaign folders come from stats_support.write_campaign; the
stats.json is written by hand here with the source hashes of those files, so that tables, figures
and the report can be tested without running the analysis (fresh stats.json is the only input).
"""

from __future__ import annotations

import importlib.util
import json
import statistics
from pathlib import Path

from scieflow.experiments import manifest

_SUPPORT = Path(__file__).with_name("stats_support.py")
_spec = importlib.util.spec_from_file_location("stats_support", _SUPPORT)
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)

CONFIGS = support.CONFIGS
BASELINE = support.BASELINE
PARAMS = support.PARAMS
PSNR = {
    "000_sigma-0.5": [24.1, 24.6, 23.8, 24.4, 24.0, 24.3],
    "001_sigma-1.0": [22.9, 23.4, 22.6, 23.1, 22.8, 23.0],
    "002_sigma-2.0": [21.7, 22.2, 21.9, 22.4, 21.5, 22.0],
}
VALUES = {"ssim": support.SSIM, "psnr": PSNR}


def _cell(config: str, metric: str) -> dict:
    vals = VALUES[metric][config]
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals)
    return {
        "n": len(vals), "mean": mean, "sd": sd, "median": statistics.median(vals),
        "min": min(vals), "max": max(vals), "n_missing": 0, "n_nonfinite": 0,
        "values": [{"run_id": f"{config}_r{k}", "seed": k, "value": float(v)}
                   for k, v in enumerate(vals)],
        "excluded": [],
        "ci": {"low": mean - sd / 2, "high": mean + sd / 2, "level": 0.95, "method": "BCa",
               "resamples": 9999, "seed": 0},
        "ci_note": None,
    }


def _empty_cell() -> dict:
    return {
        "n": 0, "mean": None, "sd": None, "median": None, "min": None, "max": None,
        "n_missing": 6, "n_nonfinite": 0,
        "values": [], "excluded": [{"run_id": f"r{k}", "seed": k, "reason": "missing"}
                                   for k in range(6)],
        "ci": None, "ci_note": None,
    }


def _test_entry(metric: str, config: str, *, statistic, df, p, p_adjusted, reject, effect):
    return {
        "id": f"{metric}:{config}", "metric": metric, "kind": "pairwise", "test": "welch-t-test",
        "source": "campaign", "config_id": config, "baseline": BASELINE, "n": 6, "n_baseline": 6,
        "n_unpaired": 0, "computed": True, "reason": None, "statistic": statistic, "df": df,
        "p": p, "p_adjusted": p_adjusted, "reject": reject, "family": "campaign",
        "confirmatory": False, "effect": effect, "warnings": [],
    }


def fixed_doc(metrics=("ssim",), empty: bool = False) -> dict:
    """A complete stats.json document (version 1) with the fixed numbers of these tests."""
    configs = []
    for c in CONFIGS:
        cells = {m: (_empty_cell() if empty else _cell(c, m)) for m in metrics}
        configs.append({
            "config_id": c, "params": PARAMS[c], "n_runs": 6, "n_failed": 0,
            "n_direct_excluded": 0, "metrics": cells,
        })
    tests = []
    if not empty:
        for m in metrics:
            tests.append(_test_entry(
                m, "000_sigma-0.5", statistic=2.5, df=9.876, p=0.0312, p_adjusted=0.0624,
                reject=False,
                effect={"mean_difference": 0.02, "hedges_g": 0.8123, "cliffs_delta": 0.5,
                        "d_z": None, "better": True}))
            tests.append(_test_entry(
                m, "002_sigma-2.0", statistic=-3.1, df=7.2, p=0.0004, p_adjusted=0.0008,
                reject=True,
                effect={"mean_difference": -0.05, "hedges_g": -1.5, "cliffs_delta": -1.0,
                        "d_z": None, "better": False}))
    return {
        "version": 1,
        "tool": "scieflow experiment stats",
        "campaign": "stat-camp",
        "source": {},
        "settings": {
            "include_direct": True, "alpha": 0.05, "ci": 0.95, "correction": "holm",
            "bootstrap": {"resamples": 9999, "seed": 0}, "baseline": BASELINE,
            "tests": {m: "welch-t-test" for m in metrics},
        },
        "design": {"status": "none", "version": None, "in_force_hash": None,
                   "study_type": None},
        "data_level": None,
        "metrics": {m: {"higher_is_better": True} for m in metrics},
        "configs": configs,
        "tests": tests,
        "families": [{"name": "campaign", "correction": "holm", "alpha": 0.05,
                      "size": len(tests)}] if tests else [],
        "method": {"scipy": "1.18.1", "numpy": "2.5.3", "assumptions": {}},
        "warnings": [],
    }


def install_doc(cdir, doc: dict) -> dict:
    """Write stats.json for doc, with the sha256 of the campaign's results.json and campaign.yaml."""
    cdir = Path(cdir)
    doc["source"] = {
        "results_sha256": manifest.file_sha256(cdir / "results.json"),
        "campaign_sha256": manifest.file_sha256(cdir / "campaign.yaml"),
    }
    (cdir / "stats.json").write_text(json.dumps(doc, sort_keys=True, indent=1, allow_nan=False))
    return doc


def install(cdir, edit=None, **kwargs) -> dict:
    """fixed_doc (optionally edited) written as a fresh stats.json of the campaign folder."""
    doc = fixed_doc(**kwargs)
    if edit is not None:
        edit(doc)
    return install_doc(cdir, doc)


def write_raw(cdir, doc: dict) -> dict:
    """stats.json of doc with the campaign's source hashes; NaN and Infinity are written as-is.

    For malformed-input tests only: install_doc refuses non-finite numbers, this one does not.
    """
    cdir = Path(cdir)
    doc["source"] = {
        "results_sha256": manifest.file_sha256(cdir / "results.json"),
        "campaign_sha256": manifest.file_sha256(cdir / "campaign.yaml"),
    }
    (cdir / "stats.json").write_text(json.dumps(doc, sort_keys=True, indent=1))
    return doc


def campaign(root, *, name="stat-camp", metrics=("ssim",), mode="direct", reference=True,
             replicated=True, analysis=None):
    """A synthetic sweep folder (6 replicates × 3 configurations) with the given metrics."""
    values = {c: {m: VALUES[m][c] for m in metrics} for c in CONFIGS}
    return support.write_campaign(root, name=name, values=values, metrics=tuple(metrics),
                                  mode=mode, reference=reference, replicated=replicated,
                                  analysis=analysis)


def edit_text(path, suffix: str = "\n") -> None:
    """Change a file's bytes (and so its sha256) without changing its JSON or YAML meaning."""
    p = Path(path)
    p.write_text(p.read_text() + suffix)
