"""Synthetic fixtures for the M-G t3 claim check (C001-C006, waivers, package refusal; M-G 5.6-5.8).

Every study, value, file, run, reference and decision here is invented. The metric values written below are
marker values: the tests search every message, report and event for them (M-G 5.16).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scieflow.experiments import manifest
from scieflow.research.manuscript.claims import compile as compile_mod
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import make_inputs, manuscript, write
from tests.research.manuscript.t8_support import project_of

SECTION = "\\section{Results}\n"
PLANTED_CSV = "metric,value\nSSIM,0.9134\nStride,7341.25\n"
MARKERS = ("0.9134", "7341.25")


def body(*lines: str) -> str:
    """Results-section sentences, one claim per line."""
    return SECTION + "\n".join(lines) + "\n"


def results_run(synth, sentences: str, *, csv: str = PLANTED_CSV, bib=None) -> Path:
    """A run whose manuscript holds `sentences`, whose data package holds tbl-metrics (a CSV) and whose
    evidence is the synthetic CSV above. Returns the run folder."""
    kwargs = {} if bib is None else {"bib": bib}
    manuscript(synth, sentences, **kwargs)
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "metrics.csv", "content": csv}])
    return synth.run_dir


def built_check(synth, main: str = "main.tex"):
    """Build the ledger, then check it (the `claims build` then `claims check` path)."""
    ledger.build(synth.run_dir, main=main)
    return compile_mod.check_ledger(project_of(synth), synth.run_dir, main=main)


def rules(result) -> list:
    return [(f["code"], f["rule"], f["severity"]) for f in result.findings]


def of_rule(result, rule: str) -> list:
    return [f for f in result.findings if f["rule"] == rule]


def of_code(result, code: str) -> list:
    return [f for f in result.findings if f["code"] == code]


def text_of(result) -> str:
    """Every message and claim-level field of the check, joined (for the no-value-leak checks)."""
    return json.dumps(result.document(), sort_keys=True, ensure_ascii=False)


def write_bundle(run: Path, flags: list, round_no: int = 1) -> None:
    """A review evidence bundle with the given flags (review/round-N/evidence/bundle.json)."""
    write(run / "review" / f"round-{round_no}" / "evidence" / "bundle.json",
          json.dumps({"flags": flags}, sort_keys=True))


def make_campaign_with_code(run: Path, campaign: str, runs: dict) -> Path:
    """experiments/<campaign>/ like t2_support.make_campaign, but each run carries `code` (its manifest
    `code` block) and metrics that hold a marker value. `runs`: rid -> code block."""
    cdir = run / "experiments" / campaign
    entries = []
    for rid, code in runs.items():
        rdir = cdir / "runs" / rid
        write(rdir / "metrics.json", json.dumps({"value": 1}, sort_keys=True))
        doc = {
            "version": 1, "run_id": rid, "campaign": campaign, "config_id": "c0", "replicate": 1,
            "seed": 1, "params": {}, "mode": "direct", "status": "ok", "returncode": 0,
            "started": "2026-10-31T00:00:00Z", "finished": "2026-10-31T00:00:01Z", "wall_seconds": 1.0,
            "code": code, "stage": {}, "inputs": [], "environment": {}, "packages": [], "data_level": "L0",
            "artifacts": manifest.artifact_list(rdir / "artifacts"),
            "metrics_sha256": manifest.file_sha256(rdir / "metrics.json"), "metrics_history": [],
        }
        msha = manifest.write_manifest(rdir, doc)
        entries.append({"run_id": rid, "manifest_sha256": msha})
    write(cdir / "results.json", json.dumps({"runs": entries}, sort_keys=True))
    return cdir


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "-c", "user.email=synthetic@example.test",
                          "-c", "user.name=synthetic", *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def init_repo(repo: Path) -> str:
    """Make `repo` a git repository with one synthetic commit; returns HEAD."""
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "synthetic base")
    return git(repo, "rev-parse", "HEAD")
