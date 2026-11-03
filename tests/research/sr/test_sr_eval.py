"""M-H t8 (plan 5.11, 5.13): `scieflow sr eval` on a run built from the screening calibration fixture.

Forty synthetic records are screened through the real writers by two screeners (codex = A, claude = B);
the gold labels come from tests/fixtures/sr/screening-calibration.yml. Every expected count is hand-typed.
No agent is launched, no network is used, and the gold file is written only inside the test run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.research.sr import cli as sr_cli
from tests.research.sr.test_sr_screening import SLUG, TA, screen, setup

REPO = Path(__file__).resolve().parents[3]
CALIBRATION = REPO / "tests" / "fixtures" / "sr" / "screening-calibration.yml"
GOLD_REL = "sr/eval/gold.yml"


@pytest.fixture
def sroot(srroot):
    (srroot / "config").mkdir(exist_ok=True)
    for name in ("agents.yml", "defaults.yml"):
        shutil.copy(REPO / "config" / name, srroot / "config" / name)
    return srroot


@pytest.fixture
def cli_root(sroot, monkeypatch):
    monkeypatch.setattr(sr_cli, "_root", lambda: sroot)
    return sroot


def _calibration() -> dict:
    return yaml.safe_load(CALIBRATION.read_text(encoding="utf-8"))


@pytest.fixture
def calibrated(cli_root):
    doc = _calibration()
    by_id = {row["id"]: row for row in doc["records"]}
    proj, ws = setup(cli_root, 40)
    screen(proj, ws, stage=TA, size=40, decide=lambda agent, rid: (by_id[rid][agent], ""))
    gold = ws / GOLD_REL
    gold.parent.mkdir(parents=True, exist_ok=True)
    labels = {row["id"]: row["gold"] for row in doc["records"]}
    gold.write_text(yaml.safe_dump({"format": 1, "stage": TA, "labels": labels}), encoding="utf-8")
    return cli_root, ws


def _eval(*args, gold: str = GOLD_REL):
    return CliRunner().invoke(sr_cli.sr, ["eval", SLUG, "--stage", TA, "--gold", gold, *args])


def _run_files(ws: Path) -> list[str]:
    return sorted(str(p.relative_to(ws)) for p in ws.rglob("*"))


def test_stream_confusions_equal_the_hand_counts_of_the_fixture(calibrated):
    result = _eval("--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    exp = _calibration()["expected"]
    assert doc["labelled"] == 40
    codex, claude = doc["streams"]["codex"], doc["streams"]["claude"]
    assert (codex["tp"], codex["fn"], codex["fp"], codex["tn"]) == (
        exp["A"]["tp"], exp["A"]["fn"], exp["A"]["fp"], exp["A"]["tn"])
    assert (claude["tp"], claude["fn"], claude["fp"], claude["tn"]) == (
        exp["B"]["tp"], exp["B"]["fn"], exp["B"]["fp"], exp["B"]["tn"])
    assert codex["undecided"] == [] and claude["undecided"] == []


def test_stream_metrics_are_the_pinned_wilson_intervals(calibrated):
    doc = json.loads(_eval("--json").output)
    codex = doc["streams"]["codex"]["metrics"]
    assert (codex["sensitivity"]["k"], codex["sensitivity"]["n"]) == (9, 10)
    assert codex["sensitivity"]["low"] == pytest.approx(0.595850, abs=1e-6)
    assert codex["sensitivity"]["high"] == pytest.approx(0.982124, abs=1e-6)
    assert codex["specificity"]["low"] == pytest.approx(0.743789, abs=1e-6)
    assert codex["accuracy"]["low"] == pytest.approx(0.769482, abs=1e-6)
    claude = doc["streams"]["claude"]["metrics"]
    assert claude["sensitivity"]["low"] == pytest.approx(0.722467, abs=1e-6)
    assert claude["specificity"]["low"] == pytest.approx(0.626943, abs=1e-6)
    assert claude["accuracy"]["low"] == pytest.approx(0.709277, abs=1e-6)


def test_resolved_verdict_counts_pending_records_and_names_them(calibrated):
    doc = json.loads(_eval("--json").output)
    resolved = doc["resolved"]
    assert (resolved["tp"], resolved["fn"], resolved["fp"], resolved["tn"]) == (9, 0, 3, 24)
    assert resolved["undecided"] == ["R00010", "R00014", "R00015", "R00016"]


def test_text_output_names_each_screener_and_the_undecided_records(calibrated):
    result = _eval()
    assert result.exit_code == 0, result.output
    assert "codex" in result.output and "claude" in result.output and "resolved" in result.output
    assert "9/10" in result.output
    assert "R00010" in result.output


def test_eval_writes_nothing_to_the_run(calibrated):
    _, ws = calibrated
    before = _run_files(ws)
    assert _eval().exit_code == 0
    assert _run_files(ws) == before


def test_a_gold_file_outside_the_run_is_refused(calibrated, tmp_path):
    _, ws = calibrated
    outside = tmp_path / "gold.yml"
    shutil.copy(ws / GOLD_REL, outside)
    result = _eval(gold=str(outside))
    assert result.exit_code == 1, result.output
    assert "inside the run" in result.output


def test_a_gold_path_with_dot_dot_is_refused(calibrated):
    result = _eval(gold="sr/eval/../eval/gold.yml")
    assert result.exit_code == 1, result.output
    assert ".." in result.output


def test_a_gold_symlink_inside_the_run_is_refused(calibrated):
    _, ws = calibrated
    link = ws / "sr" / "eval" / "link.yml"
    link.symlink_to(ws / GOLD_REL)
    result = _eval(gold="sr/eval/link.yml")
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output


def test_a_gold_directory_is_refused(calibrated):
    result = _eval(gold="sr/eval")
    assert result.exit_code == 1, result.output


def test_a_missing_gold_file_is_refused(calibrated):
    result = _eval(gold="sr/eval/absent.yml")
    assert result.exit_code == 1, result.output


@pytest.mark.parametrize("labels", [
    {"R00001": "maybe"},
    {"X00001": "include"},
    {"R0001": "include"},
])
def test_invalid_gold_labels_are_refused(calibrated, labels):
    _, ws = calibrated
    (ws / "sr" / "eval" / "bad.yml").write_text(
        yaml.safe_dump({"format": 1, "stage": TA, "labels": labels}), encoding="utf-8")
    result = _eval(gold="sr/eval/bad.yml")
    assert result.exit_code == 1, result.output


def test_a_gold_for_another_stage_is_refused(calibrated):
    _, ws = calibrated
    (ws / "sr" / "eval" / "ft.yml").write_text(
        yaml.safe_dump({"format": 1, "stage": "full-text", "labels": {"R00001": "include"}}), encoding="utf-8")
    result = _eval(gold="sr/eval/ft.yml")
    assert result.exit_code == 1, result.output
