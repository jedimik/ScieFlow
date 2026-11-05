"""T-12 t5: the workspace index (plan 5.7) on synthetic temporary projects.

Read-only, top-level only, bounded to 10,000 inspected entries, alias grouping
without following an alias, and findings (never defaults) for unreadable controls.
"""

import os
from pathlib import Path

import pytest
import yaml

from scieflow.core import control_read as cr
from scieflow.core import workspace as wsmod
from scieflow.core import workspace_health
from scieflow.core.project import Project

pytestmark = pytest.mark.skipif(not cr.supported(), reason="needs O_NOFOLLOW and dir_fd support")

ELSEWHERE = "SPY-ELSEWHERE-target"


@pytest.fixture
def project(tmp_path, monkeypatch):
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
    (loop / "log.md").write_text("log\n")
    lit = root / "2026-01-lit"
    lit.mkdir()
    (lit / "status.yml").write_text(yaml.safe_dump({"workflow": "lit-review", "phase": "report"}))
    (lit / "config.yml").write_text(yaml.safe_dump({"agent": "claude"}))
    (root / "2026-01-lit-research").symlink_to("2026-01-lit")
    (root / "_misc" / "notes").mkdir(parents=True)
    (root / "notes").symlink_to("_misc/notes")
    (root / "news").mkdir()
    (root / "chats").mkdir()
    (root / "_archives").mkdir()
    (root / "_x").mkdir()
    (root / ".hidden").mkdir()
    broken = root / "broken"
    broken.mkdir()
    (broken / "status.yml").write_text("run: [unclosed\n")
    bigcfg = root / "bigcfg"
    bigcfg.mkdir()
    (bigcfg / "status.yml").write_text(yaml.safe_dump({"run": "bigcfg", "phases": {}}))
    (bigcfg / "config.yml").write_text("k: " + "v" * (cr.CONTROL_CAP + 10) + "\n")
    no_status = root / "no-status"
    no_status.mkdir()
    (no_status / "log.md").write_text("only a log\n")
    elsewhere = tmp_path / ELSEWHERE
    elsewhere.mkdir()
    (elsewhere / "status.yml").write_text("run: elsewhere\n")
    (root / "external").symlink_to(elsewhere)
    (root / "relative-out").symlink_to("../../outside-target")
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def _tree(path: Path) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        for name in sorted(dirnames + filenames):
            full = Path(dirpath, name)
            st = os.lstat(full)
            out.append((str(full.relative_to(path)), st.st_mtime_ns,
                        full.read_bytes() if full.is_file() else None))
    return sorted(out, key=lambda row: row[0])


def _row(index, slug):
    return next(r for r in index["runs"] if r["slug"] == slug)


def test_result_shape(project):
    index = workspace_health.index(project)
    for key in ("observed_at", "complete", "inspected", "limit", "omitted_note", "runs",
                "external_aliases", "moved", "lineage"):
        assert key in index, key
    assert index["limit"] == 10_000
    assert index["complete"] is True
    assert index["omitted_note"] is None


def test_clean_runs_match_list_runs(project):
    index = workspace_health.index(project)
    listed = {r.slug: r for r in wsmod.list_runs(project.root)}
    for slug in ("2026-01-loop", "2026-01-lit"):
        row, run = _row(index, slug), listed[slug]
        assert row["kind"] == run.kind
        assert row["state"] == run.state
        assert row["phase"] == run.phase
        assert row["lineage"] == run.lineage
        assert row["findings"] == []


def test_aliases_are_grouped_under_their_run(project):
    index = workspace_health.index(project)
    assert _row(index, "2026-01-lit")["aliases"] == ["2026-01-lit-research"]
    assert _row(index, "2026-01-loop")["aliases"] == []


def test_external_aliases_are_shown_and_never_followed(project, monkeypatch):
    opened = []
    followed_stats = []
    real_open, real_stat, real_scandir = os.open, os.stat, os.scandir

    def spy_open(path, *a, **k):
        opened.append(path)
        return real_open(path, *a, **k)

    def spy_stat(path, *a, **k):
        if k.get("follow_symlinks", True):
            followed_stats.append(path)
        return real_stat(path, *a, **k)

    def spy_scandir(path=".", *a, **k):
        opened.append(path)
        return real_scandir(path, *a, **k)

    monkeypatch.setattr(workspace_health.os, "open", spy_open)
    monkeypatch.setattr(workspace_health.os, "stat", spy_stat)
    monkeypatch.setattr(workspace_health.os, "scandir", spy_scandir)
    index = workspace_health.index(project)
    names = {entry["name"]: entry for entry in index["external_aliases"]}
    assert set(names) == {"external", "relative-out"}
    assert names["external"]["note"] == "points outside workspace/; not followed"
    assert "external" not in [r["slug"] for r in index["runs"]]
    assert all(ELSEWHERE not in str(p) for p in opened)
    assert "external" not in opened
    assert "relative-out" not in opened
    assert "external" not in followed_stats
    assert "relative-out" not in followed_stats


def test_moved_entry_is_listed_as_moved(project):
    index = workspace_health.index(project)
    assert index["moved"] == [{"name": "notes", "target": "_misc/notes"}]


def test_modules_underscore_and_hidden_names_are_skipped_but_counted(project):
    index = workspace_health.index(project)
    slugs = {r["slug"] for r in index["runs"]}
    for skipped in ("news", "chats", "_archives", "_x", ".hidden", "_misc"):
        assert skipped not in slugs
    top_level = [p for p in project.root.joinpath("workspace").iterdir()]
    assert index["inspected"] == len(top_level)


def test_malformed_status_is_a_finding_not_a_default(project):
    row = _row(workspace_health.index(project), "broken")
    assert row["findings"] and {"file": "status.yml", "status": "malformed"} in row["findings"]
    assert row["kind"] == "unknown"
    assert row["state"] == "unknown"


def test_oversized_config_is_a_finding_and_lineage_is_unknown(project):
    row = _row(workspace_health.index(project), "bigcfg")
    assert {"file": "config.yml", "status": "oversized"} in row["findings"]
    assert row["lineage"] == "unknown"


def test_missing_status_is_a_finding(project):
    row = _row(workspace_health.index(project), "no-status")
    assert {"file": "status.yml", "status": "missing"} in row["findings"]
    assert row["kind"] == "unknown"


def test_the_bound_stops_at_ten_thousand_without_listing_the_directory(project, monkeypatch):
    root = project.root / "workspace"
    for i in range(10_005 - len(list(root.iterdir()))):
        (root / f"bulk{i:05d}").mkdir()

    def refuse(*a, **k):
        raise AssertionError("the whole directory must not be listed")

    monkeypatch.setattr(Path, "iterdir", refuse)
    monkeypatch.setattr(os, "listdir", refuse)
    index = workspace_health.index(project)
    assert index["complete"] is False
    assert index["inspected"] == 10_000
    assert index["omitted_note"]
    assert len(index["runs"]) <= 10_000


def test_the_index_writes_nothing(project):
    before = _tree(project.root)
    workspace_health.index(project)
    assert _tree(project.root) == before
    assert not (project.root / "workspace" / "INDEX.md").exists()


def test_describe_from_gives_describe_for_the_same_status(project):
    loop = project.root / "workspace" / "2026-01-loop"
    status = yaml.safe_load((loop / "status.yml").read_text())
    config = yaml.safe_load((loop / "config.yml").read_text())
    direct = wsmod.describe(loop)
    via = wsmod.describe_from("2026-01-loop", status, config, direct.updated, direct.updated_at)
    assert via == direct
