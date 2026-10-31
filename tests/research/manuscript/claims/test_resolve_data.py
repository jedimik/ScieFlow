"""M-G t2: data references (M-G 5.4): manifest, release and import resolution, confinement, the L2 rule.

Synthetic only: every file, id and value is invented.
"""

from __future__ import annotations

import os

import pytest

from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import (
    make_inputs, manuscript, released_record, sha_text, write, write_data,
)

ROW = "stride,0.91\n"


def _rules(result) -> set:
    return {p["rule"] for p in result.problems}


def _severity(result, rule) -> set:
    return {p["severity"] for p in result.problems if p["rule"] == rule}


def test_manifest_id_resolves_to_its_file_and_is_pinned(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:tbl-metrics]")
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv", "content": ROW}])
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [record] = ledger.read(synth.run_dir)
    assert record["pins"] == {
        "data:tbl-metrics": {"files": {"inputs/results/metrics.csv": sha_text(ROW)}}}


def test_released_id_resolves_and_its_bytes_are_rechecked(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:rel-1]")
    record = released_record(synth.run_dir, "rel-1", "agg.csv", ROW)
    write_data(synth.run_dir, released=[record])
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [rec] = ledger.read(synth.run_dir)
    assert rec["pins"] == {"data:rel-1": {"files": {"released/rel-1/agg.csv": sha_text(ROW)}}}
    (synth.run_dir / "released" / "rel-1" / "agg.csv").write_text("stride,0.50\n", encoding="utf-8")
    altered = ledger.build(synth.run_dir)
    assert "artifact-altered" in _rules(altered)
    assert _severity(altered, "artifact-altered") == {"error"}


def test_import_record_resolves_by_its_release_id(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:rel-1]")
    folder = "inputs/released/src-rel-1"
    write(synth.run_dir / folder / "agg.csv", ROW)
    imp = {"id": "imp-9", "file": f"{folder}/agg.csv", "sha256": sha_text(ROW), "from": "src",
           "release": "rel-1", "imported_at": "2026-10-31T00:00:00Z"}
    write_data(synth.run_dir, imports=[imp])
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [rec] = ledger.read(synth.run_dir)
    assert rec["pins"] == {"data:rel-1": {"files": {f"{folder}/agg.csv": sha_text(ROW)}}}


def test_id_matching_a_manifest_and_a_release_is_ambiguous(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:rel-1]")
    make_inputs(synth.run_dir, [{"id": "rel-1", "file": "results/metrics.csv", "content": ROW}])
    record = released_record(synth.run_dir, "rel-1", "agg.csv", ROW)
    write_data(synth.run_dir, released=[record])
    result = ledger.build(synth.run_dir)
    assert "ref-ambiguous" in _rules(result)
    [rec] = ledger.read(synth.run_dir)
    assert "data:rel-1" not in rec["pins"]


def test_unknown_id_is_unresolved_and_unpinned(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:nothing-here]")
    result = ledger.build(synth.run_dir)
    assert "ref-unresolved" in _rules(result)
    assert _severity(result, "ref-unresolved") == {"error"}
    [rec] = ledger.read(synth.run_dir)
    assert rec["pins"] == {}


@pytest.mark.parametrize("bad_path", ["../secret.csv", "/etc/hostname", "inputs/../../secret.csv"])
def test_manifest_path_that_leaves_inputs_is_unsafe(synth, bad_path, tmp_path):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:tbl-metrics]")
    secret = tmp_path / "secret.csv"
    secret.write_text(ROW, encoding="utf-8")
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv", "content": ROW}])
    manifest_file = synth.run_dir / "inputs" / "manifest.yml"
    manifest_file.write_text(manifest_file.read_text().replace("results/metrics.csv", bad_path),
                             encoding="utf-8")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe" in _rules(result)
    [rec] = ledger.read(synth.run_dir)
    assert rec["pins"] == {}


def test_manifest_file_that_is_a_symlink_is_unsafe(synth, tmp_path):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:tbl-metrics]")
    outside = tmp_path / "outside.csv"
    outside.write_text(ROW, encoding="utf-8")
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv", "content": ROW}])
    link = synth.run_dir / "inputs" / "results" / "linked.csv"
    os.symlink(outside, link)
    manifest_file = synth.run_dir / "inputs" / "manifest.yml"
    manifest_file.write_text(manifest_file.read_text().replace("results/metrics.csv",
                                                               "results/linked.csv"), encoding="utf-8")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe" in _rules(result)


def test_l2_run_refuses_a_manifest_only_reference(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:tbl-metrics]")
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv", "content": ROW}])
    write_data(synth.run_dir, default_level="L2")
    result = ledger.build(synth.run_dir)
    assert "data-unreleased" in _rules(result)
    assert _severity(result, "data-unreleased") == {"error"}


def test_l2_run_accepts_a_released_reference(synth):
    manuscript(synth, "Mean stride time was 0.91 s. % source: [data:rel-1]")
    record = released_record(synth.run_dir, "rel-1", "agg.csv", ROW)
    write_data(synth.run_dir, default_level="L2", released=[record])
    result = ledger.build(synth.run_dir)
    assert "data-unreleased" not in _rules(result)
    assert result.problems == []
