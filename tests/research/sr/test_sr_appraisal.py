"""M-H t7 (plan 5.10, 5.13, Part B t7 items 2, 4, 5): `sr rob` and `sr grade` through the real writers.

Every test runs the plan 5.12 scenario (robins-i, GRADE planned, six included studies at full text) on a
temporary project whose config/sr holds the shipped tool definitions. Dispatch evidence is a job record; no
agent is launched, nothing touches the network, and the data are synthetic.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr import verify
from tests.research.sr import t6_scenario as scn

REPO = Path(__file__).resolve().parents[3]
INCLUDED = ["R00002", "R00003", "R00004", "R00005", "R00006", "R00045"]


def _factory(rob_tool: str = "robins-i", grade: bool = True):
    """make_root of the t6 scenario, plus the shipped config/sr and a chosen appraisal block (before the lock)."""
    original = scn.make_root

    def make_root(base: Path) -> Path:
        root = original(base)
        shutil.copytree(REPO / "config" / "sr", root / "config" / "sr")
        path = root / "workspace" / scn.SLUG / "design" / "design.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["review"]["appraisal"] = {"rob_tool": rob_tool, "grade": grade}
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return root

    return make_root


def _rob(*args):
    return CliRunner().invoke(sr_cli.sr, ["rob", *args, scn.SLUG])


def _grade(*args):
    return CliRunner().invoke(sr_cli.sr, ["grade", *args, scn.SLUG])


def _fill_rob(ws: Path, rids, judgement: str = "low", overall: str = "low") -> None:
    for rid in rids:
        path = ws / "sr" / "rob" / f"{rid}.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for row in doc["domains"]:
            row["judgement"] = judgement
            row["support"] = "synthetic support"
        doc["overall"]["judgement"] = overall
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _rows(ws: Path, type_: str) -> list[dict]:
    return [r for r in events.read(ws) if r.get("type") == type_]


@pytest.fixture
def appr(tmp_path, monkeypatch):
    monkeypatch.setattr(scn, "make_root", _factory())
    _proj, ws, _box = scn.scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    return tmp_path, ws


# --- rob init ------------------------------------------------------------------------------------------


def test_rob_init_creates_one_worksheet_per_included_study(appr):
    _root, ws = appr
    result = _rob("init")
    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in (ws / "sr" / "rob").glob("*.yml"))
    assert names == sorted(f"{rid}.yml" for rid in INCLUDED)


def test_rob_init_never_overwrites_a_worksheet(appr):
    _root, ws = appr
    assert _rob("init").exit_code == 0
    path = ws / "sr" / "rob" / "R00002.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["overall"]["rationale"] = "synthetic marker"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert _rob("init").exit_code == 0
    assert "synthetic marker" in path.read_text(encoding="utf-8")


def test_rob_init_refuses_when_the_protocol_has_no_risk_of_bias_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(scn, "make_root", _factory(rob_tool="none"))
    _proj, ws, _box = scn.scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    result = _rob("init")
    assert result.exit_code == 1
    assert not (ws / "sr" / "rob").exists()


def test_rob_init_refuses_a_symlinked_worksheet_folder_and_writes_nothing(appr, tmp_path):
    _root, ws = appr
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / "sr" / "rob")
    result = _rob("init")
    assert result.exit_code == 1
    assert list(elsewhere.iterdir()) == []


def test_rob_init_refuses_an_invalid_ledger_and_writes_nothing(appr):
    _root, ws = appr
    with (ws / "sr" / "rounds" / "1" / "records.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{}\n")
    result = _rob("init")
    assert result.exit_code == 1
    assert "sr-ledger-invalid" in result.output
    assert not (ws / "sr" / "rob").exists()


# --- rob check and record ------------------------------------------------------------------------------


def test_rob_check_fails_on_unjudged_templates(appr):
    _root, _ws = appr
    assert _rob("init").exit_code == 0
    result = _rob("check")
    assert result.exit_code == 1
    assert "R00002" in result.output


def test_rob_record_writes_six_appraisals_and_one_rob_commit(appr):
    _root, ws = appr
    assert _rob("init").exit_code == 0
    _fill_rob(ws, INCLUDED)
    assert _rob("check").exit_code == 0
    result = _rob("record")
    assert result.exit_code == 0, result.output
    appraisals = [r for r in _rows(ws, "sr.appraisal") if (r.get("data") or {}).get("kind") == "rob"]
    assert len(appraisals) == 6
    assert sorted(r["data"]["record_id"] for r in appraisals) == INCLUDED
    commits = [r for r in _rows(ws, "sr.committed") if (r.get("data") or {}).get("kind") == "rob"]
    assert len(commits) == 1
    assert verify.verify(None, ws)["ok"] is True


def test_appraisal_events_carry_judgements_and_never_support_text(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED, judgement="serious", overall="serious")
    assert _rob("record").exit_code == 0
    for row in _rows(ws, "sr.appraisal"):
        assert "synthetic support" not in str(row.get("data"))


def test_a_worksheet_changed_after_record_is_a_verify_error_and_record_refuses(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED)
    assert _rob("record").exit_code == 0
    path = ws / "sr" / "rob" / "R00002.yml"
    path.write_text(path.read_text(encoding="utf-8").replace("synthetic support", "edited support"),
                    encoding="utf-8")
    verified = CliRunner().invoke(sr_cli.sr, ["verify", scn.SLUG])
    assert verified.exit_code == 1
    assert "R00002.yml" in verified.output
    again = _rob("record")
    assert again.exit_code == 1
    assert "sr-ledger-invalid" in again.output


def test_record_refuses_when_one_worksheet_fails_and_writes_nothing(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED)
    path = ws / "sr" / "rob" / "R00004.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["domains"][0]["judgement"] = "maybe"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    result = _rob("record")
    assert result.exit_code == 1
    assert "R00004" in result.output
    assert [r for r in _rows(ws, "sr.appraisal") if (r.get("data") or {}).get("kind") == "rob"] == []
    assert [r for r in _rows(ws, "sr.committed") if (r.get("data") or {}).get("kind") == "rob"] == []


def test_record_refuses_a_missing_worksheet(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED)
    (ws / "sr" / "rob" / "R00005.yml").unlink()
    result = _rob("record")
    assert result.exit_code == 1
    assert "R00005" in result.output


def test_a_later_record_supersedes_without_a_verify_error(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED)
    assert _rob("record").exit_code == 0
    assert _rob("record").exit_code == 0
    assert verify.verify(None, ws)["ok"] is True


def test_rob_record_as_agent_is_recorded_with_the_agent_actor(appr):
    _root, ws = appr
    _rob("init")
    _fill_rob(ws, INCLUDED)
    assert CliRunner().invoke(sr_cli.sr, ["rob", "record", scn.SLUG, "--as-agent"]).exit_code == 0
    rows = [r for r in _rows(ws, "sr.appraisal") if (r.get("data") or {}).get("kind") == "rob"]
    assert rows and all(r["actor"] == "agent" for r in rows)


# --- grade ---------------------------------------------------------------------------------------------


def _grade_doc(ws: Path) -> dict:
    return yaml.safe_load((ws / "sr" / "grade.yml").read_text(encoding="utf-8"))


def _grade_write(ws: Path, doc: dict) -> None:
    (ws / "sr" / "grade.yml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_grade_init_writes_one_entry_per_sap_outcome(appr):
    _root, ws = appr
    result = _grade("init")
    assert result.exit_code == 0, result.output
    doc = _grade_doc(ws)
    assert [e["outcome"] for e in doc["outcomes"]] == ["primary_outcome"]


def test_grade_init_never_overwrites(appr):
    _root, ws = appr
    assert _grade("init").exit_code == 0
    doc = _grade_doc(ws)
    doc["outcomes"][0]["effect_text"] = "synthetic marker"
    _grade_write(ws, doc)
    assert _grade("init").exit_code == 1
    assert "synthetic marker" in (ws / "sr" / "grade.yml").read_text(encoding="utf-8")


def test_grade_init_refuses_when_grade_is_not_planned(tmp_path, monkeypatch):
    monkeypatch.setattr(scn, "make_root", _factory(grade=False))
    _proj, ws, _box = scn.scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    assert _grade("init").exit_code == 1
    assert not (ws / "sr" / "grade.yml").exists()


def test_grade_check_record_and_table_derive_certainty(appr):
    _root, ws = appr
    assert _grade("init").exit_code == 0
    doc = _grade_doc(ws)
    entry = doc["outcomes"][0]
    entry["design"] = "randomised"
    entry["studies"] = list(INCLUDED)
    entry["downgrade"]["risk-of-bias"] = {"level": "serious", "reason": "synthetic"}
    entry["downgrade"]["imprecision"] = {"level": "serious", "reason": "synthetic"}
    _grade_write(ws, doc)
    checked = CliRunner().invoke(sr_cli.sr, ["grade", "check", scn.SLUG, "--json"])
    assert checked.exit_code == 0, checked.output
    assert '"certainty": "low"' in checked.output
    assert _grade("record").exit_code == 0
    rows = [r for r in _rows(ws, "sr.appraisal") if (r.get("data") or {}).get("kind") == "grade"]
    assert len(rows) == 1 and rows[0]["data"]["certainty"] == "low"
    assert [r for r in _rows(ws, "sr.committed") if (r.get("data") or {}).get("kind") == "grade"]
    table = CliRunner().invoke(sr_cli.sr, ["grade", "table", scn.SLUG, "--write"])
    assert table.exit_code == 0, table.output
    rendered = (ws / "sr" / "grade" / "summary-of-findings.md").read_text(encoding="utf-8")
    assert "primary_outcome" in rendered and "low" in rendered


def test_grade_refuses_a_study_that_is_not_included(appr):
    _root, ws = appr
    assert _grade("init").exit_code == 0
    doc = _grade_doc(ws)
    doc["outcomes"][0]["design"] = "randomised"
    doc["outcomes"][0]["studies"] = ["R00001"]
    _grade_write(ws, doc)
    result = CliRunner().invoke(sr_cli.sr, ["grade", "check", scn.SLUG])
    assert result.exit_code == 1
    assert "R00001" in result.output


def test_grade_record_refuses_when_check_fails_and_writes_nothing(appr):
    _root, ws = appr
    assert _grade("init").exit_code == 0
    assert _grade("record").exit_code == 1
    assert [r for r in _rows(ws, "sr.appraisal") if (r.get("data") or {}).get("kind") == "grade"] == []
