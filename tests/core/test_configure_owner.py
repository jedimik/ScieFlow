"""The shared configurator owner (T-12 t2): preview, revision, locked apply.

Every writer goes through `agent_configure.apply`: the CLI `configure`, the
TUI agent and news saves, and `service.apply_staffing`. Nothing is written
unless the configuration that was previewed is still the one on disk.
"""

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import filelock
import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import agent_configure as acf
from scieflow.core import cli as cli_mod
from scieflow.core import menu, service, store
from scieflow.core.cli import agent
from scieflow.core.project import Project

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path("config") / ".agent-configure.lock"
DEFAULT_OPS = [acf.Op("assign", "loop.experiment", "codex")]


def configure(*args):
    return CliRunner().invoke(agent, ["configure", *args])


def rel(plan, root: Path) -> list[str]:
    return sorted(Path(key).relative_to(root).as_posix() for key in plan.deps)


def snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in (root / "config").glob("*.yml")} | {
        p: p.read_bytes() for p in (root / "workspace").rglob("config.yml")}


@pytest.fixture
def news_repo(repo):
    (repo / "config" / "news.yml").write_text(
        "agent: claude\nlookback_days: 30\n"
        "interests:\n  - name: Snakemake\n    keywords: [workflow]\n")
    return repo


class ScriptedUI:
    """Answers menu questions from a list, in order; fails if the script runs out."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def _next(self, message):
        self.asked.append(message)
        assert self.answers, f"unexpected question: {message}"
        return self.answers.pop(0)

    def select(self, message, choices, back=True):
        answer = self._next(message)
        if answer == "BACK":
            return menu.BACK
        values = [v for _, v in choices]
        if answer in values:
            return answer
        for label, value in choices:
            if isinstance(answer, str) and label.startswith(answer):
                return value
        raise AssertionError(f"{answer!r} not among {values}")

    def checkbox(self, message, choices, checked=()):
        return self._next(message)

    def text(self, message, default=""):
        answer = self._next(message)
        return default if answer == "DEFAULT" else answer

    def confirm(self, message, default=False):
        return self._next(message)


# --- read set and revision ---------------------------------------------------

def test_default_scope_reads_exactly_the_registry_and_the_defaults(repo):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    assert rel(plan, repo) == ["config/agents.yml", "config/defaults.yml"]


def test_workspace_scope_reads_the_run_and_records_absence(repo):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    assert rel(plan, repo) == ["config/agents.yml", "config/defaults.yml", "workspace/run-1",
                               "workspace/run-1/config.yml", "workspace/run-1/status.yml"]
    assert plan.deps[str(repo / "workspace" / "run-1")] == "dir"
    assert plan.deps[str(repo / "workspace" / "run-1" / "config.yml")] == "absent"
    assert plan.deps[str(repo / "workspace" / "run-1" / "status.yml")] == "absent"


def test_news_scope_reads_only_news_yml(news_repo):
    plan = acf.preview("news", news_repo, [acf.Op("set", "model", "claude-x")])
    assert rel(plan, news_repo) == ["config/news.yml"]


def test_a_plan_from_the_owner_carries_a_revision_and_its_scope(repo):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    assert plan.revision and plan.scope == "default" and plan.root == repo
    assert acf.preview("default", repo, DEFAULT_OPS).revision == plan.revision


# --- stale refusal -----------------------------------------------------------

def test_happy_path_writes_exactly_the_previewed_text(repo):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    expected = {c.path: c.after for c in plan.changes}
    assert expected
    written = acf.apply(plan)
    for path, text in expected.items():
        assert path.read_text() == text
    assert sorted(written) == sorted(expected)


def test_a_changed_target_makes_apply_stale_and_writes_nothing(repo):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    defaults = repo / "config" / "defaults.yml"
    defaults.write_text(defaults.read_text() + "# edited\n")
    before = snapshot(repo)
    with pytest.raises(acf.StaleError, match="preview again"):
        acf.apply(plan)
    assert snapshot(repo) == before


def test_a_changed_non_target_dependency_makes_apply_stale(repo):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    agents = repo / "config" / "agents.yml"
    agents.write_text(agents.read_text() + "# edited\n")
    with pytest.raises(acf.StaleError):
        acf.apply(plan)
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


def test_a_removed_dependency_makes_apply_stale(repo):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    (repo / "config" / "defaults.yml").unlink()
    with pytest.raises(acf.StaleError):
        acf.apply(plan)


def test_a_created_status_file_makes_apply_stale(repo):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    (repo / "workspace" / "run-1" / "status.yml").write_text("state: running\n")
    with pytest.raises(acf.StaleError):
        acf.apply(plan)
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


def test_a_different_op_list_makes_apply_stale(repo):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    tampered = dataclasses.replace(plan, ops=(acf.Op("assign", "loop.experiment", "claude"),))
    before = snapshot(repo)
    with pytest.raises(acf.StaleError):
        acf.apply(tampered)
    assert snapshot(repo) == before


def test_a_different_scope_or_slug_makes_apply_stale(repo):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    with pytest.raises(acf.StaleError):
        acf.apply(dataclasses.replace(plan, slug="missing"))
    with pytest.raises(acf.StaleError):
        acf.apply(dataclasses.replace(plan, scope="default"))


def test_a_changed_code_fingerprint_makes_apply_stale(repo, monkeypatch):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    monkeypatch.setattr(acf, "CODE_FINGERPRINT", "a-different-schema")
    with pytest.raises(acf.StaleError):
        acf.apply(plan)


@pytest.mark.parametrize("change", ["rewrite_defaults", "create_status"])
def test_a_dependency_changed_between_replan_and_the_locks_makes_apply_stale(repo, monkeypatch, change):
    """The replan sees the old files; a non-target dependency that changes before the sibling
    locks are held is caught only by the under-lock re-read of every dependency."""
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    run = repo / "workspace" / "run-1"
    defaults = repo / "config" / "defaults.yml"
    status = run / "status.yml"
    real_build = acf._build

    def build_then_change(*args, **kwargs):
        built = real_build(*args, **kwargs)
        if change == "rewrite_defaults":
            defaults.write_text(defaults.read_text() + "# changed after the replan\n")
        else:
            status.write_text("state: running\n")
        return built

    monkeypatch.setattr(acf, "_build", build_then_change)
    with pytest.raises(acf.StaleError, match="preview again"):
        acf.apply(plan)
    assert not (run / "config.yml").exists()
    if change == "create_status":
        assert status.read_text() == "state: running\n"


def test_apply_takes_the_project_lock_then_sorted_sibling_locks_once_in_workspace_scope(repo, monkeypatch):
    plan = acf.preview("workspace", repo, DEFAULT_OPS, slug="run-1")
    run = repo / "workspace" / "run-1"
    order = []
    real_acquire = filelock.FileLock.acquire

    def spy(self, *args, **kwargs):
        order.append(self.lock_file)
        return real_acquire(self, *args, **kwargs)

    monkeypatch.setattr(filelock.FileLock, "acquire", spy)
    acf.apply(plan)
    assert order == [str(repo / LOCK),
                     str(repo / "config" / "agents.yml.lock"),
                     str(repo / "config" / "defaults.yml.lock"),
                     str(run / "config.yml.lock"),
                     str(run / "status.yml.lock")]


def test_cli_yes_with_a_file_changed_after_preview_exits_1_and_writes_nothing(repo, monkeypatch):
    real_print = cli_mod._print_plan
    defaults = repo / "config" / "defaults.yml"

    def tamper(plan, root):
        real_print(plan, root)
        defaults.write_text(defaults.read_text() + "# changed while previewed\n")

    monkeypatch.setattr(cli_mod, "_print_plan", tamper)
    before = defaults.read_text() + "# changed while previewed\n"
    result = configure("--assign", "loop.experiment=codex", "--yes")
    assert result.exit_code == 1, result.output
    assert "preview again" in result.output
    assert defaults.read_text() == before
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


# --- locks -------------------------------------------------------------------

def test_apply_takes_the_project_lock_then_sorted_sibling_locks_once(repo, monkeypatch):
    plan = acf.preview("default", repo, [acf.Op("assign", "loop.experiment", "codex"),
                                         acf.Op("set", "codex.model", "sol-2")])
    assert {c.path.name for c in plan.changes} == {"agents.yml", "defaults.yml"}
    order = []
    real_acquire = filelock.FileLock.acquire

    def spy(self, *args, **kwargs):
        order.append(self.lock_file)
        return real_acquire(self, *args, **kwargs)

    monkeypatch.setattr(filelock.FileLock, "acquire", spy)
    acf.apply(plan)
    assert order == [str(repo / LOCK),
                     str(repo / "config" / "agents.yml.lock"),
                     str(repo / "config" / "defaults.yml.lock")]


def test_apply_never_calls_store_write_text(repo, monkeypatch):
    plan = acf.preview("default", repo, DEFAULT_OPS)

    def forbidden(*args, **kwargs):
        raise AssertionError("apply must replace through the held locks")

    monkeypatch.setattr(store, "write_text", forbidden)
    acf.apply(plan)
    assert "codex" in (repo / "config" / "defaults.yml").read_text()


def test_a_project_lock_held_by_another_process_is_busy(repo, monkeypatch):
    plan = acf.preview("default", repo, DEFAULT_OPS)
    monkeypatch.setattr(acf, "CONFIGURE_LOCK_TIMEOUT", 0.3)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time, filelock\n"
        "lock = filelock.FileLock(sys.argv[1]); lock.acquire()\n"
        "print('held', flush=True); time.sleep(60)\n"), str(repo / LOCK)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(acf.BusyError, match="preview again"):
            acf.apply(plan)
        with pytest.raises(acf.BusyError):
            acf.preview("default", repo, DEFAULT_OPS)
        assert time.monotonic() - started < 5
    finally:
        holder.kill()
        holder.wait()


def test_a_failed_second_write_reports_the_partial_outcome(repo, monkeypatch):
    plan = acf.preview("default", repo, [acf.Op("assign", "loop.experiment", "codex"),
                                         acf.Op("set", "codex.model", "sol-2")])
    first, second = sorted(str(c.path) for c in plan.changes)
    old = {p: Path(p).read_text() for p in (first, second)}
    real_write = store._atomic_write
    calls = []

    def flaky(path, text):
        calls.append(str(path))
        if len(calls) == 2:
            raise OSError("disk full")
        real_write(path, text)

    monkeypatch.setattr(store, "_atomic_write", flaky)
    with pytest.raises(acf.PartialWriteError) as info:
        acf.apply(plan)
    assert info.value.written == [first]
    assert info.value.uncertain == [second]
    assert info.value.unchanged == []
    assert Path(first).read_text() != old[first]
    assert Path(second).read_text() == old[second]


def test_a_hand_built_plan_is_refused_and_writes_nothing(repo):
    path = repo / "config" / "defaults.yml"
    before = snapshot(repo)
    plan = acf.Plan([acf.FileChange(path, path.read_text(), "archive: true\n")])
    with pytest.raises(acf.ConfigureError, match="not produced by a planner"):
        acf.apply(plan)
    assert snapshot(repo) == before


def test_the_configure_lock_file_is_gitignored():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "config/.agent-configure.lock" in lines


# --- callers ----------------------------------------------------------------

def test_cli_configure_writes_through_apply(repo, monkeypatch):
    calls = []
    real_apply = acf.apply
    monkeypatch.setattr(acf, "apply", lambda plan: calls.append(plan) or real_apply(plan))
    result = configure("--assign", "loop.experiment=codex", "--yes")
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].revision


def test_tui_agent_save_writes_through_apply(repo, monkeypatch):
    calls = []
    real_apply = acf.apply
    monkeypatch.setattr(acf, "apply", lambda plan: calls.append(plan) or real_apply(plan))
    ui = ScriptedUI(["default", "role", "research.outline", "codex", False, "save", True])
    menu.agent_settings(ui)
    assert len(calls) == 1
    assert yaml.safe_load((repo / "config" / "defaults.yml").read_text())[
        "assignments"]["research.outline"] == "codex"


def test_tui_stale_save_shows_an_error_and_keeps_the_queued_changes(repo, capsys):
    defaults = repo / "config" / "defaults.yml"

    class Tampering(ScriptedUI):
        def confirm(self, message, default=False):
            if message == "Write these changes?":
                defaults.write_text(defaults.read_text() + "# changed by someone else\n")
            return super().confirm(message, default)

    ui = Tampering(["default", "role", "research.outline", "codex", False,
                    "save", True,          # save, then the tampering confirm
                    "BACK", True])         # back out: queued changes are still there
    menu.agent_settings(ui)
    out = capsys.readouterr().out
    assert "not saved" in out and "preview again" in out
    assert "Discard queued changes?" in ui.asked
    assert "# changed by someone else" in defaults.read_text()
    assert yaml.safe_load(defaults.read_text())["assignments"]["research.outline"] == "claude"


def test_news_save_writes_through_apply(news_repo, monkeypatch):
    calls = []
    real_apply = acf.apply
    monkeypatch.setattr(acf, "apply", lambda plan: calls.append(plan) or real_apply(plan))
    ui = ScriptedUI(["agent", "codex", "save", True])
    menu._news_settings(ui, news_repo)
    assert len(calls) == 1
    assert "agent: codex" in (news_repo / "config" / "news.yml").read_text()


def _project(repo: Path) -> Project:
    return Project(repo)


def test_plan_staffing_returns_a_revision_and_apply_needs_it(repo):
    project = _project(repo)
    before = snapshot(repo)
    shown = service.plan_staffing(project, ["loop.experiment=codex"], None)
    assert shown["revision"]
    with pytest.raises(service.ServiceError, match="preview again"):
        service.apply_staffing(project, ["loop.experiment=codex"], None)
    with pytest.raises(service.ServiceError, match="preview again"):
        service.apply_staffing(project, ["loop.experiment=codex"], None, revision="0" * 64)
    assert snapshot(repo) == before
    result = service.apply_staffing(project, ["loop.experiment=codex"], None,
                                    revision=shown["revision"])
    assert result["written"] == ["config/defaults.yml"]
    assert yaml.safe_load((repo / "config" / "defaults.yml").read_text())[
        "assignments"]["loop.experiment"] == "codex"


def test_staffing_apply_with_a_stale_revision_names_preview_again(repo):
    project = _project(repo)
    shown = service.plan_staffing(project, ["loop.experiment=codex"], None)
    (repo / "config" / "defaults.yml").write_text((repo / "config" / "defaults.yml").read_text()
                                                  + "# edited\n")
    before = snapshot(repo)
    with pytest.raises(service.ServiceError, match="preview again"):
        service.apply_staffing(project, ["loop.experiment=codex"], None,
                               revision=shown["revision"])
    assert snapshot(repo) == before


def test_new_browser_text_has_no_browser_dialogs():
    """HUMAN UI RULE (2026-11-03): no alert, confirm or prompt in templates or scripts."""
    import re

    import scieflow.web as web_pkg

    web_root = Path(web_pkg.__file__).parent
    sources = [*web_root.rglob("*.html"), *web_root.rglob("*.js")]
    assert sources, "expected web templates or scripts to scan"
    dialog = re.compile(r"(?<![\w.])(?:window\.)?(alert|confirm|prompt)\s*\(")
    offenders = [str(p.relative_to(web_root)) for p in sources if dialog.search(p.read_text("utf-8"))]
    assert offenders == []
