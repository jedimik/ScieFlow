"""M-H t6 (plan 5.8, 5.16 row 4): `sr verify` is read-only, fail closed and names the rule it refuses.

Each test tampers with the committed ledger of the plan 5.12 scenario and checks that verify reports it,
that every writer refuses with sr-ledger-invalid, and that the CLI exits 1. Synthetic data only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr import screening, search, verify
from scieflow.research.sr.errors import SRError
from tests.research.sr import t6_scenario as scn


@pytest.fixture
def run(tmp_path, monkeypatch):
    proj, ws, box = scn.scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(sr_cli, "_root", lambda: tmp_path)
    return proj, ws


def _rows(ws: Path) -> list[dict]:
    return [json.loads(line) for line in (ws / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]


def _write_rows(ws: Path, rows: list[dict]) -> None:
    (ws / "events.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                                     encoding="utf-8")


def _commit(ws: Path, kind: str) -> dict:
    found = [dict(r.get("data") or {}) for r in events.read(ws, types=("sr.committed",))
             if (r.get("data") or {}).get("kind") == kind]
    return found[0]


def _verify(ws: Path) -> dict:
    return verify.verify(Project(ws.parent.parent), ws)


def test_clean_scenario_verifies_with_no_errors(run):
    proj, ws = run
    doc = _verify(ws)
    assert doc["errors"] == [], doc["errors"]
    assert doc["warnings"] == [], doc["warnings"]


def test_edited_committed_file_is_an_error_and_every_writer_refuses(run):
    proj, ws = run
    records = ws / "sr" / "rounds" / "1" / "records.jsonl"
    records.write_text(records.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    doc = _verify(ws)
    assert any("differs from its committed sha256" in e for e in doc["errors"]), doc["errors"]
    with pytest.raises(SRError, match=r"^sr-ledger-invalid: "):
        screening.prepare(proj, ws, stage=scn.TA)
    with pytest.raises(SRError, match=r"^sr-ledger-invalid: "):
        search.run_round(proj, ws, slug=scn.SLUG)


def test_missing_committed_file_is_an_error(run):
    proj, ws = run
    (ws / "sr" / "rounds" / "1" / "round.json").unlink()
    doc = _verify(ws)
    assert any("round.json is missing or not a plain file" in e for e in doc["errors"]), doc["errors"]


def test_symlinked_committed_file_is_an_error(run):
    proj, ws = run
    target = ws / "sr" / "rounds" / "1" / "round.json"
    target.unlink()
    target.symlink_to(ws / "sr" / "rounds" / "1" / "hits.jsonl")
    doc = _verify(ws)
    assert any("round.json is missing or not a plain file" in e for e in doc["errors"]), doc["errors"]


def test_duplicate_commit_for_one_op_is_an_error(run):
    proj, ws = run
    first = _commit(ws, "search")
    events.emit(ws, "sr.committed", "human", op=first["op"], kind="search", round=1, files={},
                protocol_version=first["protocol_version"], protocol_hash=first["protocol_hash"])
    doc = _verify(ws)
    assert any("duplicate sr.committed" in e for e in doc["errors"]), doc["errors"]


def test_committed_screening_decision_that_differs_from_the_output_is_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    for row in rows:
        if row.get("type") == "sr.screening_decision":
            row["data"]["decision"] = "exclude" if row["data"]["decision"] == "include" else "include"
            break
    _write_rows(ws, rows)
    doc = _verify(ws)
    assert any("screening decisions" in e and "differ" in e for e in doc["errors"]), doc["errors"]


def test_committed_adjudication_that_differs_from_decisions_is_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    for row in rows:
        if row.get("type") == "sr.adjudication":
            row["data"]["decision"] = "exclude" if row["data"]["decision"] == "include" else "include"
            break
    _write_rows(ws, rows)
    doc = _verify(ws)
    assert any("adjudication" in e and "differ" in e for e in doc["errors"]), doc["errors"]


def test_adjudication_gate_not_answered_by_a_human_is_an_error(run):
    proj, ws = run
    gate_id = _commit(ws, "adjudication")["gate"]
    path = ws / "gates" / f"{gate_id}.json"
    gate = json.loads(path.read_text(encoding="utf-8"))
    gate["answered_by"] = "agent"
    path.write_text(json.dumps(gate), encoding="utf-8")
    doc = _verify(ws)
    assert any("gate" in e and "human" in e for e in doc["errors"]), doc["errors"]


def test_commit_protocol_hash_matching_no_lock_record_is_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    for row in rows:
        if row.get("type") == "sr.committed" and row["data"].get("kind") == "screening":
            row["data"]["protocol_hash"] = "0" * 64
            break
    _write_rows(ws, rows)
    doc = _verify(ws)
    assert any("protocol" in e and "design ledger" in e for e in doc["errors"]), doc["errors"]


def test_round_json_totals_that_disagree_with_the_events_are_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    rdir = ws / "sr" / "rounds" / "1"
    doc = json.loads((rdir / "round.json").read_text(encoding="utf-8"))
    doc["identified"] = doc["identified"] + 1
    (rdir / "round.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in rows:
        if row.get("type") == "sr.committed" and row["data"].get("kind") == "search":
            row["data"]["files"]["sr/rounds/1/round.json"] = scn.sha256(rdir / "round.json")
    _write_rows(ws, rows)
    verdict = _verify(ws)
    assert any("round.json" in e and "events" in e for e in verdict["errors"]), verdict["errors"]


def test_a_gap_in_the_corpus_ids_is_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    rdir = ws / "sr" / "rounds" / "1"
    lines = (rdir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if '"id": "R00024"' not in line]
    assert len(kept) == len(lines) - 1
    (rdir / "records.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    for row in rows:
        if row.get("type") == "sr.committed" and row["data"].get("kind") == "search":
            row["data"]["files"]["sr/rounds/1/records.jsonl"] = scn.sha256(rdir / "records.jsonl")
    _write_rows(ws, rows)
    doc = _verify(ws)
    assert any("gapless" in e for e in doc["errors"]), doc["errors"]


def test_uncommitted_detail_events_are_a_warning_not_an_error(run):
    proj, ws = run
    rows = _rows(ws)
    kept = []
    dropped = False
    for row in rows:
        if not dropped and row.get("type") == "sr.committed" and row["data"].get("kind") == "screening":
            dropped = True
            continue
        kept.append(row)
    assert dropped
    _write_rows(ws, kept)
    doc = _verify(ws)
    assert doc["errors"] == [], doc["errors"]
    assert any("uncommitted" in w for w in doc["warnings"]), doc["warnings"]


def test_cli_exits_0_when_clean_and_1_with_the_errors(run, monkeypatch):
    proj, ws = run
    runner = CliRunner()
    clean = runner.invoke(sr_cli.sr, ["verify", scn.SLUG, "--json"])
    assert clean.exit_code == 0, clean.output
    (ws / "sr" / "rounds" / "1" / "records.jsonl").write_text("tampered\n", encoding="utf-8")
    bad = runner.invoke(sr_cli.sr, ["verify", scn.SLUG, "--json"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["errors"]


def test_verify_changes_no_file(run):
    proj, ws = run

    def digest():
        return {str(p): scn.sha256(p) for p in sorted(ws.rglob("*")) if p.is_file()}

    before = digest()
    _verify(ws)
    assert digest() == before
