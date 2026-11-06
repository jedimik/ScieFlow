"""T-12 t6: run health (plan 5.8) on synthetic temporary projects.

The cheap checks come at once; the bounded scan runs only when asked. The scan must agree
with `workspace.doctor` on fixtures that fit its budget, stop at its budget and say so,
never follow a link, never block on a FIFO, never write and never start a subprocess.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scieflow.core import control_read as cr
from scieflow.core import service
from scieflow.core import workspace as wsmod
from scieflow.core import workspace_health as wh
from scieflow.core.project import Project
from scieflow.core.service import ServiceError

pytestmark = pytest.mark.skipif(not cr.supported(), reason="needs O_NOFOLLOW and dir_fd support")

REPO = Path(__file__).resolve().parents[2]
CHEAP = ("missing", "legacy_config_keys", "per_file_dvc_pointer")
WALKED = ("junk_dirs", "absolute_self_links", "scripts_in_logs")


class Clock:
    """A fake monotonic clock: each call returns the next value, then the last one again."""

    def __init__(self, *values):
        self._values = list(values)

    def __call__(self):
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _status(run: str) -> str:
    return yaml.safe_dump({"run": run, "iteration": 2, "stopped": None,
                           "phases": {"hypothesize": "done", "experiment": "running",
                                      "literature": "pending", "synthesize": "pending"}})


@pytest.fixture
def base(tmp_path, monkeypatch):
    """A project root with a loop run, a research run, a bare run, an alias and a DVC pointer.

    The working directory is elsewhere, and there a decoy `archive.py` says every directory
    is junk: a scan that loaded the archive module from the cwd would be wrong.
    """
    root = Path(os.path.realpath(tmp_path)) / "proj"
    elsewhere = Path(os.path.realpath(tmp_path)) / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    _write(root / "config" / "agents.yml", "agents: {}\n")
    _write(root / "scripts" / "sflib" / "archive.py", (REPO / "scripts/sflib/archive.py").read_text())
    _write(elsewhere / "scripts" / "sflib" / "archive.py",
           "def _skip_dir(path, src_dir):\n    return True\n")
    ws = root / "workspace"
    loop = ws / "2026-01-loop"
    _write(loop / "status.yml", _status("2026-01-loop"))
    _write(loop / "config.yml", yaml.safe_dump({"agent": "claude", "lineage": "job1"}))
    _write(loop / "goal.md", "g\n")
    _write(loop / "tmp" / "x", "junk junk junk")
    _write(loop / "logs" / "s1.py", "print(1)\n")
    _write(loop / "logs" / "s2.py", "print(2)\n")
    _write(loop / "logs" / "note.txt", "n\n")
    (loop / "self").symlink_to(loop / "status.yml")
    (loop / "abs").symlink_to(str(loop / "goal.md"))
    (loop / "rel").symlink_to("goal.md")
    lit = ws / "2026-01-lit"
    _write(lit / "status.yml", yaml.safe_dump({"workflow": "lit-review", "phase": "report"}))
    _write(lit / "config.yml", yaml.safe_dump({"agent": "claude"}))
    _write(lit / "log.md", "l\n")
    (ws / "2026-01-lit-research").symlink_to("2026-01-lit")
    _write(ws / "bare" / "log.md", "only a log\n")
    (ws / "_misc" / "notes").mkdir(parents=True)
    (ws / "notes").symlink_to("_misc/notes")
    _write(ws / "2026-01-loop.dvc", "outs: []\n")
    monkeypatch.chdir(elsewhere)
    return root


def _doctor(root: Path, slug: str) -> dict:
    return wsmod.doctor(slug, root=root)


def _tree(path: Path) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        for name in sorted(dirnames + filenames):
            full = Path(dirpath, name)
            st = os.lstat(full)
            out.append((str(full.relative_to(path)), st.st_mtime_ns,
                        full.read_bytes() if full.is_file() else None))
    return sorted(out, key=lambda row: row[0])


def _spy_opens(monkeypatch) -> list:
    seen: list = []
    real = cr.open_subdir

    def spy(fd, name):
        seen.append(name)
        return real(fd, name)

    monkeypatch.setattr(cr, "open_subdir", spy)
    return seen


# -- the helpers shared with doctor ----------------------------------------------------------
def test_shared_helpers_are_the_doctor_rules():
    from scieflow.core import agent_config

    config = {"agent": "x", "reviewer": "y", "lineage": "z"}
    expected = [k for k in (*agent_config.LEGACY_ROLE_KEYS, "agent", "journal_profiler",
                            *agent_config.LEGACY_UNMAPPED_KEYS) if k in config]
    assert wsmod.legacy_keys(config) == expected
    assert wsmod.is_self_link("/a/run/x", "/a/run") is True
    assert wsmod.is_self_link("/a/runner/x", "/a/run") is False
    assert wsmod.expected_files("loop") == wsmod.LOOP_FILES
    assert wsmod.expected_files("research") == wsmod.RESEARCH_FILES
    assert wsmod.expected_files("none") == ()


# -- the cheap checks ------------------------------------------------------------------------
@pytest.mark.parametrize("slug", ["2026-01-loop", "2026-01-lit", "bare"])
def test_cheap_checks_equal_doctor_and_the_walk_is_not_run(base, slug):
    report = _doctor(base, slug)
    health = service.run_health(Project(base), slug)
    assert health["kind"] == report["kind"]
    assert health["state"] == report["state"]
    for name in CHEAP:
        assert health["checks"][name]["status"] == "complete", name
        assert health["checks"][name]["value"] == report[name], name
    for name in WALKED:
        assert health["checks"][name]["status"] == "not-checked", name
        assert "value" not in health["checks"][name], name


def test_health_reports_a_symlinked_config_and_a_fifo_control(base):
    run = base / "workspace" / "2026-01-loop"
    (run / "config.yml").unlink()
    (run / "config.yml").symlink_to(run / "goal.md")
    (run / "budget.yml").symlink_to(run / "goal.md")
    os.mkfifo(run / "notebook.md")
    health = service.run_health(Project(base), "2026-01-loop")
    legacy = health["checks"]["legacy_config_keys"]
    assert legacy["status"] == "refused"
    assert legacy["finding"] == {"file": "config.yml", "status": "symlink"}
    findings = {(f["file"], f["status"]) for f in health["findings"]}
    assert ("budget.yml", "symlink") in findings
    assert ("notebook.md", "not-regular") in findings
    assert "budget.yml" not in health["checks"]["missing"]["value"]


def test_unreadable_status_makes_the_kind_unknown_not_a_default(base):
    (base / "workspace" / "2026-01-loop" / "status.yml").write_text("run: [unclosed\n")
    health = service.run_health(Project(base), "2026-01-loop")
    assert health["kind"] == "unknown"
    assert health["checks"]["missing"]["status"] == "refused"
    assert {"file": "status.yml", "status": "malformed"} in health["findings"]


# -- the bounded scan ------------------------------------------------------------------------
@pytest.mark.parametrize("slug", ["2026-01-loop", "2026-01-lit", "bare"])
def test_scan_equals_doctor_when_the_tree_fits(base, slug):
    report = _doctor(base, slug)
    scan = service.scan_run_health(Project(base), slug)
    assert scan["stop_reason"] == "none"
    assert scan["entries_observed"] > 0 or slug == "bare"
    assert "results may change while a run is active" in scan["note"]
    for name in (*CHEAP, *WALKED):
        assert scan["checks"][name]["status"] == "complete", name
        assert scan["checks"][name]["value"] == report[name], name


def test_depth_four_and_self_links_match_doctor(base):
    run = base / "workspace" / "2026-01-loop"
    _write(run / "deep" / "a" / "b" / "c" / "tmp" / "f", "x" * 9)       # depth 5: junk, counted
    _write(run / "deep" / "a" / "b" / "c" / "d" / "tmp" / "f", "x" * 9)  # depth 6: not visited
    (run / "deep" / "a" / "b" / "c" / "s").symlink_to(str(run / "goal.md"))  # depth 4: counted
    (run / "deep" / "a" / "b" / "c" / "d" / "s").symlink_to(str(run / "goal.md"))  # depth 5
    report = _doctor(base, "2026-01-loop")
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert scan["checks"]["junk_dirs"]["value"] == report["junk_dirs"]
    assert scan["checks"]["absolute_self_links"]["value"] == report["absolute_self_links"]
    assert "deep/a/b/c/tmp" in report["junk_dirs"]
    assert "deep/a/b/c/d/tmp" not in report["junk_dirs"]


def test_a_symlinked_directory_is_never_descended(base, tmp_path, monkeypatch):
    outside = Path(os.path.realpath(tmp_path)) / "outside-tree"
    _write(outside / "tmp" / "big", "x" * 999)
    (base / "workspace" / "2026-01-loop" / "linked").symlink_to(outside)
    seen = _spy_opens(monkeypatch)
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert "linked" not in seen
    assert scan["checks"]["junk_dirs"]["value"] == {"tmp": 14}


def test_a_fifo_inside_the_run_never_blocks(base):
    os.mkfifo(base / "workspace" / "2026-01-loop" / "pipe")
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert scan["stop_reason"] == "none"
    assert scan["checks"]["junk_dirs"]["status"] == "complete"


# -- the budget ------------------------------------------------------------------------------
def test_entry_budget_stops_at_ten_thousand_in_the_ordinary_walk(base):
    run = base / "workspace" / "2026-01-loop"
    many = run / "many"
    many.mkdir()
    for i in range(10_001):
        (many / f"f{i}").touch()
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert scan["stop_reason"] == "entries"
    assert scan["entries_observed"] == 10_000
    assert scan["checks"]["junk_dirs"]["status"] == "partial"
    assert "value" not in scan["checks"]["junk_dirs"]
    assert scan["checks"]["scripts_in_logs"]["status"] == "not-checked"


def test_entries_inside_a_junk_size_walk_count_against_the_same_budget(base):
    tmp = base / "workspace" / "2026-01-loop" / "tmp"
    for i in range(10_500):
        (tmp / f"f{i}").touch()
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert scan["stop_reason"] == "entries"
    assert scan["entries_observed"] == 10_000
    assert scan["checks"]["junk_dirs"]["status"] == "partial"
    assert scan["checks"]["scripts_in_logs"]["status"] == "not-checked"


def test_entries_of_the_logs_recount_count_against_the_budget(base):
    logs = base / "workspace" / "2026-01-loop" / "logs"
    for i in range(6_000):
        (logs / f"s{i}.py").touch()
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert scan["stop_reason"] == "entries"
    assert scan["entries_observed"] == 10_000
    assert scan["checks"]["junk_dirs"]["status"] == "complete"
    assert scan["checks"]["scripts_in_logs"]["status"] == "partial"
    assert 0 < scan["checks"]["scripts_in_logs"]["at_least"] <= 6_002


def test_a_clock_past_two_seconds_stops_before_the_next_operation(base):
    scan = wh.scan(Project(base), "2026-01-loop", clock=Clock(0.0, 2.5))
    assert scan["stop_reason"] == "time"
    assert scan["entries_observed"] == 0
    assert scan["checks"]["junk_dirs"]["status"] == "partial"
    assert scan["checks"]["scripts_in_logs"]["status"] == "not-checked"


def test_partial_results_are_lower_bounds_never_totals(base):
    report = _doctor(base, "2026-01-loop")
    scan = wh.scan(Project(base), "2026-01-loop", max_entries=6)
    assert scan["entries_observed"] == 6
    junk = scan["checks"]["junk_dirs"]
    assert junk["status"] == "partial"
    assert "value" not in junk
    for rel, size in junk["at_least"].items():
        assert size <= report["junk_dirs"][rel]
    assert scan["checks"]["absolute_self_links"]["status"] == "partial"
    assert scan["checks"]["absolute_self_links"]["at_least"] <= report["absolute_self_links"]


# -- the boundaries --------------------------------------------------------------------------
def test_alias_slug_gives_alias_information_only(base, monkeypatch):
    seen = _spy_opens(monkeypatch)
    health = service.run_health(Project(base), "2026-01-lit-research")
    assert health == {"slug": "2026-01-lit-research", "alias_of": "2026-01-lit"}
    assert "2026-01-lit-research" not in seen
    assert service.scan_run_health(Project(base), "2026-01-lit-research") == health


def test_external_alias_is_reported_and_not_followed(base, tmp_path, monkeypatch):
    outside = Path(os.path.realpath(tmp_path)) / "outside-run"
    _write(outside / "status.yml", "run: x\n")
    (base / "workspace" / "ext-run").symlink_to(outside)
    seen = _spy_opens(monkeypatch)
    health = service.run_health(Project(base), "ext-run")
    assert health == {"slug": "ext-run", "external_alias": str(outside), "note": wh.NOTE_EXTERNAL}
    assert "ext-run" not in seen


@pytest.mark.parametrize("slug", ["no-such-run", "../proj", "a/b"])
def test_missing_or_malformed_slugs_are_service_errors(base, slug):
    with pytest.raises(ServiceError):
        service.run_health(Project(base), slug)
    with pytest.raises(ServiceError):
        service.scan_run_health(Project(base), slug)


def test_no_write_no_subprocess_and_the_archive_comes_from_the_project_root(base, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no subprocess on a health path")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    before = _tree(base)
    service.run_health(Project(base), "2026-01-loop")
    scan = service.scan_run_health(Project(base), "2026-01-loop")
    assert _tree(base) == before
    assert scan["checks"]["junk_dirs"]["value"] == {"tmp": 14}   # not the decoy's "all junk"
    assert str(wh.archive_module(base).__file__).startswith(str(base))
