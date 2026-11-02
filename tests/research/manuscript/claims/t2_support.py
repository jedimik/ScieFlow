"""Synthetic evidence fixtures for the M-G t2 resolver and pin tests (M-G 5.4).

Every study, value, file, run and reference here is invented. METRIC_VALUE is a marker value that must never
appear in any ledger message, event or check output (M-G 5.16); the tests search for it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from scieflow.experiments import manifest

METRIC_VALUE = "7341.25"
RESULT_VALUE = "8812.5"

BIB_DOI = """@article{smith2001,
  author = {Synthetic, A.},
  title = {A synthetic title},
  year = {2001}
}
@article{lee2010,
  author = {Invented, B.},
  title = {Another invented title},
  year = {2010},
  doi = {https://doi.org/10.5555/SYN.2010.1}
}
"""


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def manuscript(synth, sentences: str, bib: str | None = BIB_DOI) -> None:
    """Write main.tex whose body is `sentences` (one claim per line) and references.bib."""
    body = ("\\documentclass{article}\n\\begin{document}\n" + sentences.rstrip("\n") + "\n"
            "\\bibliography{references}\n\\end{document}\n")
    write(synth.ms / "main.tex", body)
    if bib is not None:
        write(synth.ms / "references.bib", bib)


def make_inputs(run: Path, artifacts: list[dict]) -> None:
    """inputs/manifest.yml (a data package manifest) and each artifact's file under inputs/."""
    entries = []
    for art in artifacts:
        write(run / "inputs" / art["file"], art["content"])
        entries.append({"id": art["id"], "file": art["file"], "kind": "table",
                        "description": "Synthetic table."})
    doc = {"package": "synthetic", "delivered": "2026-10-31", "processing": "synthetic",
           "artifacts": entries}
    write(run / "inputs" / "manifest.yml", yaml.safe_dump(doc, sort_keys=False))


def write_data(run: Path, *, default_level: str | None = None, released=(), imports=()) -> None:
    """data.yml with the given released and imported records (M-B shapes)."""
    doc: dict = {"version": 1}
    if default_level is not None:
        doc["default_level"] = default_level
    doc["released"] = list(released)
    doc["imports"] = list(imports)
    write(run / "data.yml", yaml.safe_dump(doc, sort_keys=False))


def released_record(run: Path, rid: str, name: str, content: str) -> dict:
    """Write released/<rid>/<name> and return its release record."""
    rel = f"released/{rid}/{name}"
    write(run / rel, content)
    return {"id": rid, "file": rel, "sha256": sha_text(content), "source": "outputs/" + name,
            "gate": "01SYNTHGATE", "approved_by": "human", "approved_at": "2026-10-31T00:00:00Z",
            "checks": []}


def make_campaign(run: Path, campaign: str, runs: dict, tables: dict | None = None) -> Path:
    """experiments/<campaign>/ with runs (rid -> {metrics, artifacts, legacy}), tables and results.json.

    Each run's manifest.json is written through manifest.write_manifest, so verify_campaign passes until
    a test changes a recorded file. A run with `legacy: True` gets no manifest and no manifest_sha256.
    """
    cdir = run / "experiments" / campaign
    entries = []
    for rid, spec in runs.items():
        rdir = cdir / "runs" / rid
        metrics = spec.get("metrics", {"value": METRIC_VALUE})
        write(rdir / "metrics.json", json.dumps(metrics, sort_keys=True))
        for name, text in spec.get("artifacts", {}).items():
            write(rdir / "artifacts" / name, text)
        if spec.get("legacy"):
            entries.append({"run_id": rid})
            continue
        doc = {
            "version": 1, "run_id": rid, "campaign": campaign, "config_id": "c0", "replicate": 1,
            "seed": 1, "params": {}, "mode": "direct", "status": "ok", "returncode": 0,
            "started": "2026-10-31T00:00:00Z", "finished": "2026-10-31T00:00:01Z",
            "wall_seconds": 1.0, "code": {}, "stage": {}, "inputs": [], "environment": {},
            "packages": [], "data_level": "L0",
            "artifacts": manifest.artifact_list(rdir / "artifacts"),
            "metrics_sha256": manifest.file_sha256(rdir / "metrics.json"), "metrics_history": [],
        }
        msha = manifest.write_manifest(rdir, doc)
        entries.append({"run_id": rid, "manifest_sha256": msha})
    for name, text in (tables or {}).items():
        write(cdir / "tables" / name, text)
    write(cdir / "results.json", json.dumps({"runs": entries}, sort_keys=True))
    return cdir
