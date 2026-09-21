"""The menu is a front end: every write goes through agent_configure, with a
diff and a confirmation, and cancelling writes nothing."""

import json

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import menu


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
        # match by label prefix, for objects
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


def _snapshot(repo):
    return {p: p.read_text() for p in (repo / "config").glob("*.yml")} | {
        p: p.read_text() for p in (repo / "workspace").rglob("config.yml")}


def test_cancel_writes_nothing(repo):
    before = _snapshot(repo)
    ui = ScriptedUI(["default", "role", "research.outline", "codex", "save", False,
                     "BACK", True])  # back out, confirm discarding the queued change
    menu.agent_settings(ui)
    assert _snapshot(repo) == before


def test_role_change_for_all_projects(repo):
    ui = ScriptedUI(["default", "role", "research.outline", "codex", "save", True])
    menu.agent_settings(ui)
    doc = yaml.safe_load((repo / "config" / "defaults.yml").read_text())
    assert doc["assignments"]["research.outline"] == "codex"


def test_invalid_combination_is_refused_before_queueing(repo, capsys):
    before = _snapshot(repo)
    # reviewer == submitter is invalid; nothing is queued, so save has nothing
    ui = ScriptedUI(["default", "role", "research.reviewer", "claude", "save", "BACK"])
    menu.agent_settings(ui)
    assert "must be different agents" in capsys.readouterr().out
    assert _snapshot(repo) == before


def test_workspace_scope_touches_only_that_run(repo):
    defaults_before = (repo / "config" / "defaults.yml").read_text()
    ui = ScriptedUI(["workspace", "run-1", "effort", "codex", "high", "save", True])
    menu.agent_settings(ui)
    assert (repo / "config" / "defaults.yml").read_text() == defaults_before
    ws = yaml.safe_load((repo / "workspace" / "run-1" / "config.yml").read_text())
    assert ws["agent_overrides"]["codex"]["reasoning"] == "high"


def test_claude_extended_thinking_prefixes_cmd(repo):
    ui = ScriptedUI(["default", "effort", "claude", "extended", "save", True])
    menu.agent_settings(ui)
    reg = yaml.safe_load((repo / "config" / "agents.yml").read_text())
    assert reg["agents"]["claude"]["cmd"].startswith(menu.EXTENDED_THINKING)


def test_support_agent_on_primary_role_needs_explicit_exception(repo):
    before = _snapshot(repo)
    # decline the exception -> nothing queued -> save says nothing queued -> back out
    ui = ScriptedUI(["default", "role", "research.reviewer", "agy", False, "save", "BACK"])
    menu.agent_settings(ui)
    assert _snapshot(repo) == before


def test_model_other_is_typed(repo):
    ui = ScriptedUI(["default", "model", "codex", "__other__", "gpt-9", "save", True])
    menu.agent_settings(ui)
    reg = yaml.safe_load((repo / "config" / "agents.yml").read_text())
    assert reg["agents"]["codex"]["model"] == "gpt-9"


def test_start_workflow_hands_over_with_prompt(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(menu.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(menu.os, "execvp", lambda prog, argv: calls.append(argv))
    ui = ScriptedUI(["globus pallidus parcellation", "DEFAULT", False, "claude"])
    menu.start_workflow(ui, "lit-review")
    (argv,) = calls
    assert argv[0] == "claude"
    assert "lit-review" in argv[1] and "globus-pallidus-parcellation" in argv[1]
    assert "run configuration gate" in argv[1]


def test_suggest_slug_is_dated_kebab():
    slug = menu.suggest_slug("Why does GPi mask sensitivity differ?")
    assert slug.split("-", 2)[2] == "why-does-gpi-mask-sensitivity-differ"


def test_menu_json_is_the_skill_contract(repo):
    result = CliRunner().invoke(menu.menu, ["--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [s["key"] for s in data["sections"]] == [
        "research", "experiment", "news", "continue", "settings", "workspace", "chats"]
    assert set(data["agent_settings"]["scopes"]) == {"default", "workspace", "news"}
    assert data["agent_settings"]["agents"]["codex"]["effort_levels"] == [
        "minimal", "low", "medium", "high"]
    assert data["agent_settings"]["agents"]["claude"]["effort_levels"] == [
        "default", "extended-thinking"]
    assert "lit-review" in data["workflows"]


def test_menu_refuses_without_a_terminal(repo):
    result = CliRunner().invoke(menu.menu, [])
    assert result.exit_code != 0
    assert "menu --json" in result.output


def test_bare_scieflow_without_tty_prints_help():
    from scieflow.cli import main

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0
    assert "Run with no command in a terminal" in result.output
