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
