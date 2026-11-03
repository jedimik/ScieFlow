"""workspace doctor reports invalid event and job records, read-only (M-I t2)."""

import json

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import workspace as wsmod

GOOD_EVENT = {
    "id": "0123456789ABCDEFGHJKMNPQRS",
    "ts": "2026-11-03T12:00:00.000+00:00",
    "run": "run-1",
    "slug": "2026-01-loop",
    "type": "note.idea",
    "actor": "agent",
    "data": {"text": "x"},
}
GOOD_JOB = {
    "id": "0123456789ABCDEFGHJKMNPQRS", "kind": "turn", "argv": ["x"], "cwd": "/tmp",
    "label": "", "run_dir": None, "state": "done", "pid": None,
    "queued": "2026-11-03T12:00:00.000+00:00", "started": None,
    "finished": "2026-11-03T12:00:01.000+00:00", "exit_code": 0, "timeout_s": None,
    "log": "/tmp/a.log", "err": "/tmp/a.err", "sandboxed": False, "turn_audit": None,
}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    root = tmp_path / "workspace"
    loop = root / "2026-01-loop"
    loop.mkdir(parents=True)
    (loop / "status.yml").write_text(yaml.safe_dump({
        "run": "2026-01-loop", "iteration": 1, "stopped": None, "phases": {"hypothesize": "pending"}}))
    (loop / "config.yml").write_text(yaml.safe_dump({"slug": "2026-01-loop"}))
    clean = root / "2026-01-clean"
    clean.mkdir()
    (clean / "status.yml").write_text(yaml.safe_dump({
        "run": "2026-01-clean", "iteration": 1, "stopped": None, "phases": {}}))
    (clean / "config.yml").write_text(yaml.safe_dump({"slug": "2026-01-clean"}))
    (loop / "events.jsonl").write_text(
        "\n".join([json.dumps(GOOD_EVENT), "not json", json.dumps([1, 2])]) + "\n")
    (loop / "jobs").mkdir()
    (loop / "jobs" / "a.json").write_text("{")
    (loop / "jobs" / "b.json").write_text(json.dumps(GOOD_JOB))
    monkeypatch.chdir(tmp_path)
    return root


def test_doctor_reports_invalid_events_and_jobs(ws):
    report = wsmod.doctor("2026-01-loop")
    assert report["invalid_events"] == [{"line": 2, "rule": "json"}, {"line": 3, "rule": "object"}]
    assert report["invalid_events_total"] == 2
    assert report["invalid_jobs"] == [{"file": "a.json", "rule": "json"}]
    assert report["invalid_jobs_total"] == 1


def test_doctor_clean_run_has_empty_lists(ws):
    report = wsmod.doctor("2026-01-clean")
    assert report["invalid_events"] == [] and report["invalid_events_total"] == 0
    assert report["invalid_jobs"] == [] and report["invalid_jobs_total"] == 0


def test_doctor_caps_each_list_at_twenty_entries(ws):
    events_file = ws / "2026-01-loop" / "events.jsonl"
    events_file.write_text("".join("bad\n" for _ in range(25)))
    report = wsmod.doctor("2026-01-loop")
    assert len(report["invalid_events"]) == 20
    assert report["invalid_events_total"] == 25


def test_doctor_is_read_only(ws):
    def snapshot():
        return {str(p): (p.read_bytes() if p.is_file() else None)
                for p in sorted(ws.rglob("*"))}

    before = snapshot()
    wsmod.doctor("2026-01-loop")
    assert snapshot() == before


def test_doctor_text_output_names_each_problem_kind(ws):
    result = CliRunner().invoke(wsmod.workspace, ["doctor", "2026-01-loop"])
    assert result.exit_code == 0, result.output
    assert "invalid events" in result.output and "json" in result.output
    assert "invalid jobs" in result.output
