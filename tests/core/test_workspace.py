import json

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import workspace as wsmod


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    root = tmp_path / "workspace"
    loop = root / "2026-01-loop"
    loop.mkdir(parents=True)
    (loop / "status.yml").write_text(yaml.safe_dump({
        "run": "2026-01-loop", "iteration": 2, "stopped": None,
        "phases": {"hypothesize": "done", "experiment": "running",
                   "literature": "pending", "synthesize": "pending"}}))
    (loop / "config.yml").write_text(yaml.safe_dump({"agent": "claude", "lineage": "job1"}))
    lit = root / "2026-01-lit"
    lit.mkdir()
    (lit / "status.yml").write_text(yaml.safe_dump({"workflow": "lit-review", "phase": "report"}))
    (root / "2026-01-lit-research").symlink_to("2026-01-lit")
    (root / "_misc" / "notes").mkdir(parents=True)
    (root / "notes").symlink_to("_misc/notes")
    (root / "news").mkdir()
    (loop / "tmp").mkdir()
    (loop / "tmp" / "x").write_text("junk")
    (loop / "self").symlink_to(loop / "status.yml")
    monkeypatch.chdir(tmp_path)
    return root


def test_list_runs_kinds_states_and_aliases(ws):
    runs = {r.slug: r for r in wsmod.list_runs()}
    assert set(runs) == {"2026-01-loop", "2026-01-lit"}
    assert runs["2026-01-loop"].kind == "loop"
    assert runs["2026-01-loop"].state == "iteration 2, experiment (running)"
    assert runs["2026-01-lit"].kind == "lit-review"
    assert runs["2026-01-lit"].aliases == ["2026-01-lit-research"]
    assert runs["2026-01-loop"].lineage == "job1"


def test_moved_entries_are_reported(ws):
    assert wsmod.moved_entries() == [("notes", "_misc/notes")]


def test_doctor_finds_junk_legacy_keys_and_self_links(ws):
    report = wsmod.doctor("2026-01-loop")
    assert "tmp" in report["junk_dirs"]
    assert report["legacy_config_keys"] == ["agent"]
    assert report["absolute_self_links"] == 1
    assert "notebook.md" in report["missing"]


def test_doctor_on_alias(ws):
    assert wsmod.doctor("2026-01-lit-research")["alias_of"] == "2026-01-lit"


def test_cli_list_json_and_index(ws):
    runner = CliRunner()
    rows = json.loads(runner.invoke(wsmod.workspace, ["list", "--json"]).output)
    assert {r["slug"] for r in rows} == {"2026-01-loop", "2026-01-lit"}
    result = runner.invoke(wsmod.workspace, ["index"])
    assert result.exit_code == 0, result.output
    text = (ws / "INDEX.md").read_text()
    assert "## Lineage" in text and "`notes` → `workspace/_misc/notes`" in text


# -- sync status ----------------------------------------------------------
def _run_with_files(ws, slug="2026-01-loop"):
    run = ws / slug
    (run / "results").mkdir(parents=True, exist_ok=True)
    (run / "results" / "small.txt").write_text("x")
    return run


def test_sync_status_counts_everything_when_never_synced(ws):
    run = _run_with_files(ws)
    (run / "big.bin").write_bytes(b"0" * 2048)
    report = wsmod.sync_status("2026-01-loop", big_bytes=1024)
    assert report["tracked"] is False and report["last_sync"] is None
    assert report["new_files"] == report["files"] > 0
    assert [e["path"] for e in report["big_files"]] == ["big.bin"]


def test_sync_status_counts_only_files_newer_than_the_pointer(ws, tmp_path):
    import os
    import time

    run = _run_with_files(ws)
    old = run / "results" / "old.txt"
    old.write_text("old")
    pointer = ws / "_archives" / "2026-01-loop.zip.dvc"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("outs: []\n")
    stamp = time.time()
    os.utime(pointer, (stamp, stamp))
    os.utime(old, (stamp - 100, stamp - 100))
    fresh = run / "results" / "new.txt"
    fresh.write_text("new")
    os.utime(fresh, (stamp + 100, stamp + 100))

    report = wsmod.sync_status("2026-01-loop")
    assert report["tracked"] is True and report["last_sync"]
    assert "results/new.txt" in report["new_sample"]
    assert "results/old.txt" not in report["new_sample"]


def test_sync_status_ignores_rebuildable_dirs(ws):
    run = _run_with_files(ws)
    (run / "scratch").mkdir(exist_ok=True)
    (run / "scratch" / "huge.bin").write_bytes(b"0" * 4096)
    report = wsmod.sync_status("2026-01-loop", big_bytes=1024)
    assert report["big_files"] == []
    assert all("scratch" not in p for p in report["new_sample"])


def test_sync_status_cli_json_and_missing_run(ws):
    runner = CliRunner()
    rows = json.loads(runner.invoke(wsmod.workspace, ["sync-status", "--json"]).output)
    assert {r["slug"] for r in rows} == {"2026-01-loop", "2026-01-lit"}
    bad = runner.invoke(wsmod.workspace, ["sync-status", "nope"])
    assert bad.exit_code != 0 and "no run workspace/nope" in bad.output
