"""M-G t2: evidence pins (M-G 5.3, 5.4): carry, re-pin, limits, the explicit re-pin request.

Synthetic only. Every value is invented; no evidence value may appear in an event or a message.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.experiments import manifest
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import make_campaign, make_inputs, manuscript, write
from tests.research.manuscript.conftest import RUN

SIGMA = "Sigma 1.5 was the best setting. % source: [run:denoise/sigma-1.5]"
STRIDE = "Stride was 0.91 s in block one. % source: [data:tbl-metrics]"
TABLE = "inputs/results/metrics.csv"


def _pins_by_id(run) -> dict:
    return {rec["id"]: rec["pins"] for rec in ledger.read(run)}


def _id_starting(run, start: str) -> str:
    [rec] = [r for r in ledger.read(run) if r["text"].startswith(start)]
    return rec["id"]


def _events(run) -> list[dict]:
    path = run / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _repin_events(run) -> list[dict]:
    return [e for e in _events(run) if e["type"] == "note.claims_repinned"]


@pytest.fixture
def pinned(synth):
    """Two claims, each resting on its own evidence, built once."""
    make_campaign(synth.run_dir, "denoise", {"sigma-1.5": {"artifacts": {"plot.txt": "series 1\n"}}})
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "results/metrics.csv",
                                 "content": "stride,0.91\n"}])
    manuscript(synth, SIGMA + "\n" + STRIDE)
    ledger.build(synth.run_dir)
    return synth


def test_second_build_carries_pins_when_refs_are_unchanged(pinned):
    before = _pins_by_id(pinned.run_dir)
    assert all(before.values()), "every claim must carry pins after a build over resolvable evidence"
    write(pinned.run_dir / TABLE, "stride,0.50\n")
    ledger.build(pinned.run_dir)
    assert _pins_by_id(pinned.run_dir) == before
    assert _repin_events(pinned.run_dir) == []


def test_repin_takes_the_current_evidence_and_emits_one_count_event(pinned):
    stride = _id_starting(pinned.run_dir, "Stride was")
    old = _pins_by_id(pinned.run_dir)[stride]
    write(pinned.run_dir / TABLE, "stride,0.50\n")
    ledger.build(pinned.run_dir, repin=True)
    new = _pins_by_id(pinned.run_dir)[stride]
    assert new != old
    assert new["data:tbl-metrics"]["files"][TABLE] == manifest.file_sha256(pinned.run_dir / TABLE)
    events = _repin_events(pinned.run_dir)
    assert len(events) == 1
    assert events[0]["data"]["count"] == 2
    assert "0.50" not in json.dumps(events)


def test_repin_claim_repins_only_that_claim(pinned):
    stride = _id_starting(pinned.run_dir, "Stride was")
    sigma = _id_starting(pinned.run_dir, "Sigma 1.5")
    before = _pins_by_id(pinned.run_dir)
    write(pinned.run_dir / TABLE, "stride,0.50\n")
    write(pinned.run_dir / "experiments/denoise/runs/sigma-1.5/artifacts/plot.txt", "series 9\n")
    ledger.build(pinned.run_dir, repin_claims=[stride])
    after = _pins_by_id(pinned.run_dir)
    assert after[stride] != before[stride]
    assert after[sigma] == before[sigma]
    assert [e["data"]["count"] for e in _repin_events(pinned.run_dir)] == [1]


def test_unknown_repin_claim_id_is_refused(pinned):
    with pytest.raises(ledger.ClaimsError) as err:
        ledger.build(pinned.run_dir, repin_claims=["c-000000000000"])
    assert err.value.rule == "claim-unknown"


def test_changed_refs_take_new_pins_without_a_repin_request(pinned):
    make_campaign(pinned.run_dir, "other", {"alt": {"artifacts": {"x.txt": "alt\n"}}})
    manuscript(pinned, SIGMA + "\nStride was 0.91 s in block one. % source: [run:other/alt]")
    ledger.build(pinned.run_dir)
    [changed] = [r for r in ledger.read(pinned.run_dir) if r["text"].startswith("Stride was")]
    assert set(changed["pins"]) == {"run:other/alt"}
    assert _repin_events(pinned.run_dir) == []


def test_a_file_above_twenty_megabytes_is_pinned_and_not_refused(synth):
    make_campaign(synth.run_dir, "big", {"r": {"artifacts": {}}})
    huge = synth.run_dir / "experiments" / "big" / "runs" / "r" / "artifacts" / "huge.csv"
    write(huge, b"")
    with open(huge, "wb") as handle:
        handle.truncate(20 * 1024 * 1024 + 1)
    manuscript(synth, "Run r was stable. % source: [run:big/r]")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe" not in {p["rule"] for p in result.problems}
    [rec] = ledger.read(synth.run_dir)
    assert "experiments/big/runs/r/artifacts/huge.csv" in rec["pins"]["run:big/r"]["files"]


def test_more_than_two_hundred_evidence_files_is_refused(synth):
    artifacts = {f"f{i:03d}.txt": "x\n" for i in range(201)}
    make_campaign(synth.run_dir, "many", {"r": {"artifacts": artifacts}})
    manuscript(synth, "Run r was stable. % source: [run:many/r]")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe" in {p["rule"] for p in result.problems}
    [rec] = ledger.read(synth.run_dir)
    assert "run:many/r" not in rec["pins"]


def test_claims_build_cli_accepts_repin_claim(pinned):
    stride = _id_starting(pinned.run_dir, "Stride was")
    result = CliRunner().invoke(main, ["manuscript", "claims", "build", RUN,
                                       "--repin-claim", stride], catch_exceptions=False)
    assert result.exit_code == 0, result.output
