"""M-G t2: run and campaign references (M-G 5.4, M-G-D15): resolution inside this run, integrity, confinement.

Synthetic only. METRIC_VALUE (tests/.../t2_support.py) is searched for in every message and event.
"""

from __future__ import annotations

import json
import os

from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import METRIC_VALUE, make_campaign, manuscript


def _rules(result) -> set:
    return {p["rule"] for p in result.problems}


def _sha_file(path) -> str:
    from scieflow.experiments import manifest
    return manifest.file_sha256(path)


def _one_run(synth, campaign="denoise", rid="sigma-1.5"):
    return make_campaign(synth.run_dir, campaign, {
        rid: {"metrics": {"value": METRIC_VALUE}, "artifacts": {"plot.txt": "series 1\n"}},
    }, tables={"summary.csv": "sigma,value\n1.5,7341.25\n"})


def test_campaign_name_resolves_to_its_results_and_tables(synth):
    _one_run(synth)
    manuscript(synth, "The campaign reached a stable optimum. % source: [run:denoise]")
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [rec] = ledger.read(synth.run_dir)
    files = rec["pins"]["run:denoise"]["files"]
    assert set(files) == {"experiments/denoise/results.json",
                          "experiments/denoise/tables/summary.csv"}
    assert files["experiments/denoise/tables/summary.csv"] == _sha_file(
        synth.run_dir / "experiments/denoise/tables/summary.csv")


def test_campaign_slash_run_resolves_to_the_run_evidence(synth):
    _one_run(synth)
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]")
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [rec] = ledger.read(synth.run_dir)
    files = rec["pins"]["run:denoise/sigma-1.5"]["files"]
    base = "experiments/denoise/runs/sigma-1.5"
    assert set(files) == {f"{base}/metrics.json", f"{base}/manifest.json",
                          f"{base}/artifacts/plot.txt"}


def test_bare_unique_run_id_resolves(synth):
    _one_run(synth)
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:sigma-1.5]")
    result = ledger.build(synth.run_dir)
    assert result.problems == []


def test_bare_run_id_in_two_campaigns_is_ambiguous(synth):
    _one_run(synth, campaign="denoise")
    _one_run(synth, campaign="other")
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:sigma-1.5]")
    result = ledger.build(synth.run_dir)
    assert "ref-ambiguous" in _rules(result)
    [rec] = ledger.read(synth.run_dir)
    assert "run:sigma-1.5" not in rec["pins"]


def test_run_id_containing_a_slash_resolves_bare_and_qualified(synth):
    make_campaign(synth.run_dir, "c", {"p/q": {"artifacts": {"a.txt": "x\n"}}})
    manuscript(synth, "Value p was stable. % source: [run:p/q]\n"
                      "Value q was stable. % source: [run:c/p/q]")
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    records = ledger.read(synth.run_dir)
    assert "run:p/q" in records[0]["pins"]
    assert "run:c/p/q" in records[1]["pins"]


def test_unknown_run_is_unresolved(synth):
    _one_run(synth)
    manuscript(synth, "Nothing matches this. % source: [run:no-such-run]")
    result = ledger.build(synth.run_dir)
    assert "ref-unresolved" in _rules(result)


def test_altered_run_artifact_is_artifact_altered(synth):
    _one_run(synth)
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]")
    (synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/plot.txt").write_text(
        "series 2\n", encoding="utf-8")
    result = ledger.build(synth.run_dir)
    assert "artifact-altered" in _rules(result)
    assert {p["severity"] for p in result.problems if p["rule"] == "artifact-altered"} == {"error"}


def test_legacy_run_without_a_manifest_is_unverifiable_warning(synth):
    make_campaign(synth.run_dir, "old", {"r1": {"legacy": True, "artifacts": {"a.txt": "x\n"}}})
    manuscript(synth, "Run one was stable. % source: [run:old/r1]")
    result = ledger.build(synth.run_dir)
    assert "run-unverifiable" in _rules(result)
    assert {p["severity"] for p in result.problems if p["rule"] == "run-unverifiable"} == {"warning"}
    assert "artifact-altered" not in _rules(result)


def test_symlinked_artifact_is_refused_and_never_followed(synth, tmp_path):
    _one_run(synth)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/link.txt"
    os.symlink(outside, link)
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe" in _rules(result)
    [rec] = ledger.read(synth.run_dir)
    assert "run:denoise/sigma-1.5" not in rec["pins"]


def test_messages_and_events_never_carry_an_evidence_value(synth):
    _one_run(synth)
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]\n"
                      "Value nine was reported. % source: [run:nothing]")
    (synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/plot.txt").write_text(
        "series 2\n", encoding="utf-8")
    result = ledger.build(synth.run_dir)
    text = json.dumps(result.problems, ensure_ascii=False)
    assert METRIC_VALUE not in text
    assert "8812.5" not in text
    assert result.problems
