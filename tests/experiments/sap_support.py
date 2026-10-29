"""Synthetic designs and a run for the M-D2 t4 SAP-binding tests (plan 5.5). No patient data.

Designs are created and locked only through M-D1's own API (lock.lock, then service.answer_gate as
a human). Nothing here writes design/lock.jsonl or design/locked/.

Loaded through importlib by the test modules (the tests run with --import-mode=importlib).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from scieflow.core import service
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.core.run import status

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "design"
RUN = "r1"

# Synthetic mean-squared-error values, six replicates per configuration (the SSIM values are the
# reference set of stats_support.py).
MSE = {
    "000_sigma-0.5": [0.012, 0.011, 0.013, 0.012, 0.010, 0.014],
    "001_sigma-1.0": [0.020, 0.022, 0.019, 0.021, 0.024, 0.018],
    "002_sigma-2.0": [0.031, 0.029, 0.033, 0.030, 0.028, 0.032],
}


def make_root(root: Path) -> Path:
    """A project root with the schemas and checklists the design code reads, and workspace/r1."""
    shutil.copytree(REPO / "schemas", root / "schemas")
    shutil.copytree(REPO / "config" / "reporting", root / "config" / "reporting")
    ws = root / "workspace" / RUN
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status(RUN, "autonomous"))
    return ws


def write_design(ws: Path, source: str, mutate=None) -> None:
    (ws / "design" / "power").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "power-rct-ttest.json", ws / "design" / "power" / "rct-ttest.json")
    doc = yaml.safe_load((FIX / source).read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(doc)
    (ws / "design" / "design.yml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def lock_as_human(ws: Path) -> None:
    root = ws.parent.parent
    proj = Project(root)
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, RUN, gate["id"], "approve")


def sap_rct(method: str = "holm", alpha: float = 0.04, ssim_test: str = "mann-whitney"):
    """Synthetic RCT SAP: ssim is the primary outcome, mse an exploratory one (welch-t-test)."""
    def mutate(doc):
        doc["sap"]["estimands"] = [{
            "id": "est_primary", "outcome": "quality", "population": "randomised participants",
            "summary_measure": "mean difference",
        }]
        doc["sap"]["outcomes"] = [
            {"id": "quality", "variable": "score", "priority": "primary", "test": ssim_test,
             "estimand": "est_primary", "metric": "ssim"},
            {"id": "error", "variable": "score", "priority": "exploratory",
             "test": "welch-t-test", "metric": "mse"},
        ]
        doc["sap"]["multiplicity"] = {"method": method, "alpha": alpha}
        doc["sap"]["exploratory"] = {
            "split": {"fraction": 0.5, "seed": 7, "stratify_by": ["arm"]}, "fdr": "bh"}
    return mutate


def sap_computational(test: str = "mann-whitney"):
    """Synthetic computational SAP: ssim is the only outcome named by the design."""
    def mutate(doc):
        doc["sap"]["outcomes"] = [
            {"id": "quality", "variable": "ssim", "priority": "primary", "test": test,
             "metric": "ssim"},
        ]
        doc["sap"]["multiplicity"] = {"method": "holm", "alpha": 0.04}
    return mutate
