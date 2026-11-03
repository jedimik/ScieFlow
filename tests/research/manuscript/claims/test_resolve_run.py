"""M-G t2: run and campaign references (M-G 5.4, M-G-D15): resolution inside this run, integrity, confinement.

Synthetic only. METRIC_VALUE (tests/.../t2_support.py) is searched for in every message and event.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import (
    METRIC_VALUE, RESULT_VALUE, make_campaign, make_inputs, manuscript, write,
)
from tests.research.manuscript.conftest import RUN


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


def test_file_added_after_the_manifest_alters_a_campaign_reference(synth):
    _one_run(synth)
    manuscript(synth, "The campaign reached a stable optimum. % source: [run:denoise]")
    write(synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/extra.txt", "late\n")
    result = ledger.build(synth.run_dir)
    assert "artifact-altered" in _rules(result)
    assert {p["severity"] for p in result.problems if p["rule"] == "artifact-altered"} == {"error"}


def test_a_fifo_in_run_artifacts_is_a_per_reference_finding_not_a_build_abort(synth):
    if not hasattr(os, "mkfifo"):
        pytest.skip("this platform has no mkfifo")
    _one_run(synth)
    os.mkfifo(synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/pipe")
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]")
    result = ledger.build(synth.run_dir)
    unsafe = [p for p in result.problems if p["rule"] == "ref-unsafe"]
    assert unsafe and "pipe" in unsafe[0]["message"]
    [rec] = ledger.read(synth.run_dir)
    assert "run:denoise/sigma-1.5" not in rec["pins"]


def test_cli_repin_prints_and_records_no_evidence_value(synth):
    make_campaign(synth.run_dir, "denoise",
                  {"sigma-1.5": {"metrics": {"value": METRIC_VALUE},
                                 "artifacts": {"plot.txt": f"series {RESULT_VALUE}\n"}}},
                  tables={"summary.csv": f"sigma,value\n1.5,{METRIC_VALUE}\n"})
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv",
                                 "content": f"stride,{RESULT_VALUE}\n"}])
    manuscript(synth, "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]\n"
                      "Stride was 0.91 s in block one. % source: [data:tbl-metrics]\n"
                      "Value nine was reported. % source: [run:nothing]")
    write(synth.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/plot.txt", "series 2\n")
    cli = CliRunner().invoke(main, ["manuscript", "claims", "build", RUN, "--repin"])
    direct = ledger.build(synth.run_dir, repin=True)
    assert direct.problems
    texts = [cli.output, json.dumps(direct.problems, ensure_ascii=False),
             (synth.run_dir / "report/claims/claims.jsonl").read_text(encoding="utf-8"),
             (synth.run_dir / "events.jsonl").read_text(encoding="utf-8")]
    for text in texts:
        assert METRIC_VALUE not in text
        assert RESULT_VALUE not in text
