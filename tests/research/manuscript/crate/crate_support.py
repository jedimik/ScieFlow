"""Synthetic fixtures for M-G t7 (RO-Crate export, M-G 5.14). Every study, value, file, run, DOI and author here is
invented. The evidence values written below are marker values: the tests search the crate for them (the crate
holds hashes and ids only, never evidence values; M-G 5.16).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.experiments import manifest
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import make_inputs, manuscript, write
from tests.research.manuscript.conftest import RUN

CSV = "metric,value\nStride,0.91\n"
RAW = "metric,value\nRAWMARK,9913.5\n"
STAGE_SHA = "a" * 64
STAGE_YAML_SHA = "b" * 64
FINISHED = "2026-10-31T00:00:01Z"
SENTENCES = (
    "Mean stride time was 0.91 s in block one. % source: [data:tbl-metrics]\n"
    "Sigma was 1.5 in the inline run [run:denoise/sigma-1.5].\n"
    "Timing changed \\cite{lee2010}."
)
L2_SENTENCES = (
    "Mean stride time was 0.91 s in block one. % source: [data:tbl-metrics]\n"
    "Raw counts were 2.5 in block two. % source: [data:tbl-raw]"
)


def make_run(run, campaign: str = "denoise", rid: str = "sigma-1.5", stage_sha: str = STAGE_SHA) -> None:
    """experiments/<campaign>/runs/<rid> with a manifest that names a stage and a finish time.

    The campaign's results.json lists every run written so far (a second call adds its run, not replaces it).
    """
    rdir = run / "experiments" / campaign / "runs" / rid
    write(rdir / "metrics.json", json.dumps({"value": 1}, sort_keys=True))
    write(rdir / "artifacts" / "trace.txt", "synthetic trace\n")
    doc = {
        "version": 1, "run_id": rid, "campaign": campaign, "config_id": "c0", "replicate": 1,
        "seed": 1, "params": {}, "mode": "direct", "status": "ok", "returncode": 0,
        "started": "2026-10-31T00:00:00Z", "finished": FINISHED, "wall_seconds": 1.0,
        "code": {}, "stage": {"name": "denoise", "script_sha256": stage_sha,
                              "stage_yaml_sha256": STAGE_YAML_SHA},
        "inputs": [], "environment": {}, "packages": [], "data_level": "L0",
        "artifacts": manifest.artifact_list(rdir / "artifacts"),
        "metrics_sha256": manifest.file_sha256(rdir / "metrics.json"), "metrics_history": [],
    }
    msha = manifest.write_manifest(rdir, doc)
    results = run / "experiments" / campaign / "results.json"
    prior = json.loads(results.read_text(encoding="utf-8"))["runs"] if results.exists() else []
    write(results, json.dumps({"runs": [*prior, {"run_id": rid, "manifest_sha256": msha}]}, sort_keys=True))


def crate_run(synth) -> None:
    """A manuscript with a data-package claim, a run claim and a DOI claim, its evidence, and a built ledger."""
    manuscript(synth, SENTENCES)
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "metrics.csv", "content": CSV}])
    make_run(synth.run_dir)
    ledger.build(synth.run_dir, main="main.tex")


def invoke(*args):
    """`scieflow manuscript <args>` in-process; returns the CliRunner result (no exception on exit)."""
    return CliRunner().invoke(main, ["manuscript", *args], catch_exceptions=False)


def crate_json(synth) -> dict:
    """The crate document printed by `manuscript crate --json --no-write` (nothing is written)."""
    result = invoke("crate", RUN, "--json", "--no-write")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def crate_path(synth):
    return synth.run_dir / "report" / "ro-crate" / "ro-crate-metadata.json"
