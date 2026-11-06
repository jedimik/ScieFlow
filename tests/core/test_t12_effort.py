"""T-12 t1: effort is a real setting for Claude (plan 5.2, T12-D2..D4).

One rule decides what an agent's effort means (`effort_mode`): a command template
with `{reasoning}` (placeholder), a Claude-family agent without one (thinking: the
env prefix is the setting), or nothing (none). The same rule feeds the choices,
the one-shot dispatch (`apply_effort` in `agent_run.prepare`), validation wording
and the terminal menu. Turn paths (F-2) are not changed here; a table test pins that
`harness_identity.turn_effort` and `apply_effort` agree on the Claude env prefix.
"""

from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_config as ac
from scieflow.core import agent_configure as acf
from scieflow.core import agent_run
from scieflow.core import harness_identity as hi
from scieflow.core import menu
from scieflow.core import sandbox
from scieflow.core.project import Project

PREFIX = ac.EXTENDED_THINKING
REPO_REGISTRY = Path(__file__).resolve().parents[2] / "config" / "agents.yml"


def _registry() -> dict:
    return yaml.safe_load(REPO_REGISTRY.read_text())


# -- effort mode ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["codex", "codex-paper", "codex-review", "agy"])
def test_template_agents_are_placeholder_mode(name):
    assert ac.effort_mode(_registry()["agents"][name]) == "placeholder"


@pytest.mark.parametrize("name", ["claude", "claude-paper"])
def test_claude_agents_are_thinking_mode(name):
    assert ac.effort_mode(_registry()["agents"][name]) == "thinking"


def test_stub_has_no_effort_setting():
    assert ac.effort_mode(_registry()["agents"]["stub"]) == "none"


@pytest.mark.parametrize("cfg, mode", [
    ({"model_family": "claude", "cmd": "claude --effort {reasoning} {prompt}"}, "placeholder"),
    ({"family": "claude", "resume_cmd": "claude -r {reasoning} {session}"}, "placeholder"),
    ({"family": "claude", "cmd": "claude -p {prompt}"}, "thinking"),
    ({"model_family": "claude", "cmd": "claude -p {prompt}"}, "thinking"),
    ({"model_family": "openai", "cmd": "codex exec {prompt}"}, "none"),
])
def test_effort_mode_rule(cfg, mode):
    assert ac.effort_mode(cfg) == mode


def test_effort_levels_for_each_mode():
    levels = {"reasoning": {"levels": ["minimal", "low"]}}
    assert ac.effort_levels_for({"cmd": "x {reasoning}"}, levels) == ["minimal", "low"]
    assert ac.effort_levels_for({"cmd": "x {reasoning}"}, {}) == ["low", "medium", "high"]
    assert ac.effort_levels_for({"family": "claude", "cmd": "claude"}, {}) == [
        "default", "extended-thinking"]
    assert ac.effort_levels_for({"model_family": "stub", "cmd": "stub"}, {}) == []
    assert ac.THINKING_LEVELS == ("default", "extended-thinking")
    assert ac.DEFAULT_LEVELS == ("low", "medium", "high")
    assert menu.DEFAULT_LEVELS == ["low", "medium", "high"]


def test_menu_effort_levels_follow_the_mode_for_every_registry_agent():
    reg = _registry()
    eff = ac.resolve_data(reg, {"assignments": {}})
    for name in reg["agents"]:
        expected = ac.effort_levels_for(eff.effort_cfg(name), eff.menus.get(name) or {})
        assert menu.effort_levels(eff, name) == expected, name
    assert menu.effort_levels(eff, "stub") == []
    assert menu.effort_levels(eff, "claude") == ["default", "extended-thinking"]
    assert menu.effort_levels(eff, "codex") == ["low", "medium", "high", "xhigh", "max"]


# -- apply_effort (pure) ------------------------------------------------------------

THINK = {"model_family": "claude", "family": "claude",
         "cmd": "claude -p {prompt}", "stdin_cmd": "claude -p"}


def test_extended_thinking_adds_the_prefix_once_to_both_commands():
    cfg = {**THINK, "reasoning": "extended-thinking"}
    out = ac.apply_effort(cfg)
    assert out["cmd"] == PREFIX + "claude -p {prompt}"
    assert out["stdin_cmd"] == PREFIX + "claude -p"
    assert ac.apply_effort(out) == out
    assert cfg["cmd"] == "claude -p {prompt}"


def test_default_removes_the_prefix_from_both_commands():
    cfg = {**THINK, "reasoning": "default",
           "cmd": PREFIX + "claude -p {prompt}", "stdin_cmd": PREFIX + "claude -p"}
    out = ac.apply_effort(cfg)
    assert out["cmd"] == "claude -p {prompt}"
    assert out["stdin_cmd"] == "claude -p"
    assert cfg["cmd"] == PREFIX + "claude -p {prompt}"


@pytest.mark.parametrize("cfg", [
    {**THINK, "cmd": PREFIX + "claude -p {prompt}"},
    {**THINK, "reasoning": "max"},
    {"model_family": "openai", "cmd": "codex exec {prompt}", "reasoning": "default"},
    {"model_family": "openai", "cmd": "codex -c r={reasoning} {prompt}", "reasoning": "default"},
    {"model_family": "stub", "cmd": "stub {prompt}", "reasoning": "extended-thinking"},
])
def test_other_cases_return_the_configuration_unchanged(cfg):
    out = ac.apply_effort(cfg)
    assert out == cfg
    assert out is not cfg


# -- one-shot dispatch (agent_run.prepare) -----------------------------------------

def _set_registry(repo, agent, **fields):
    reg = _registry_of(repo)
    reg["agents"][agent].update(fields)
    (repo / "config" / "agents.yml").write_text(yaml.safe_dump(reg, sort_keys=False))


def _registry_of(repo) -> dict:
    return yaml.safe_load((repo / "config" / "agents.yml").read_text())


def _prepare(repo, write_ws, run: dict, agent: str = "claude"):
    write_ws(repo, run)
    (repo / "workspace" / "run-1" / "status.yml").write_text("slug: run-1\n")
    prompt = repo / "workspace" / "run-1" / "prompt.md"
    prompt.write_text("hi")
    return agent_run.prepare(Project(repo), agent, prompt, role="loop.experiment",
                             sandbox_enabled=False)


def _is_thinking(argv) -> bool:
    return argv[:2] == ["env", "MAX_THINKING_TOKENS=32000"]


def test_registry_extended_thinking_reaches_the_one_shot_argv(repo, write_ws):
    _set_registry(repo, "claude", reasoning="extended-thinking")
    argv = _prepare(repo, write_ws, {}).argv
    assert _is_thinking(argv)
    assert argv[2] == "claude"


def test_run_override_extended_thinking_reaches_the_one_shot_argv(repo, write_ws):
    run = {"agent_overrides": {"claude": {"reasoning": "extended-thinking"}}}
    assert _is_thinking(_prepare(repo, write_ws, run).argv)


def test_run_default_removes_a_prefixed_registry_command(repo, write_ws):
    _set_registry(repo, "claude", reasoning="extended-thinking",
                  cmd=PREFIX + "claude -p --model {model} {prompt}")
    run = {"agent_overrides": {"claude": {"reasoning": "default"}}}
    assert not _is_thinking(_prepare(repo, write_ws, run).argv)


def test_role_override_beats_the_agent_effort(repo, write_ws):
    _set_registry(repo, "claude", reasoning="extended-thinking")
    run = {"assignments": {"loop.experiment": {"agent": "claude", "reasoning": "default"}}}
    assert not _is_thinking(_prepare(repo, write_ws, run).argv)
    _set_registry(repo, "claude", reasoning="default")
    run = {"assignments": {"loop.experiment": {"agent": "claude",
                                               "reasoning": "extended-thinking"}}}
    assert _is_thinking(_prepare(repo, write_ws, run).argv)


def test_prefixed_command_without_reasoning_keeps_its_prefix(repo, write_ws):
    _set_registry(repo, "claude", cmd=PREFIX + "claude -p --model {model} {prompt}")
    assert _is_thinking(_prepare(repo, write_ws, {}).argv)


def test_codex_argv_is_not_touched_by_effort(repo, write_ws):
    argv = _prepare(repo, write_ws, {"assignments": {"loop.experiment": "codex"}},
                    agent="codex").argv
    assert argv[0] != "env"
    assert "effort=medium" in argv


def test_thinking_prefix_passes_the_credential_env_check(repo, write_ws):
    _set_registry(repo, "claude", reasoning="extended-thinking")
    argv = _prepare(repo, write_ws, {}).argv
    sandbox._check_prefix(argv)


# -- validation ---------------------------------------------------------------------

def _claude_warnings(value):
    reg = _registry()
    reg["agents"]["claude"]["reasoning"] = value
    return ac.resolve_data(reg, {"assignments": {}}).warnings


@pytest.mark.parametrize("value", ["default", "extended-thinking"])
def test_claude_choices_draw_no_label_warning(value):
    assert not any("only a label" in w for w in _claude_warnings(value))


def test_other_claude_values_are_labels_with_the_new_wording():
    warnings = _claude_warnings("max")
    assert ("agent claude: Claude effort is 'default' or 'extended-thinking'; "
            "reasoning 'max' is only a label") in warnings


@pytest.mark.parametrize("raw", [".inf", "-.inf", ".nan"])
def test_non_finite_timeout_is_a_problem(raw):
    reg = yaml.safe_load(
        "agents:\n  claude:\n    cmd: 'claude -p {prompt}'\n    tier: primary\n"
        f"    timeout_min: {raw}\n")
    problems = ac.resolve_data(reg, {"assignments": {}}).problems
    assert any("agent claude: timeout_min" in p for p in problems), problems


@pytest.mark.parametrize("raw", ["inf", "nan", "-inf", "1e400"])
def test_coerce_refuses_non_finite_numbers(raw):
    with pytest.raises(acf.ConfigureError, match="finite"):
        acf.coerce("timeout_min", raw)


def test_coerce_keeps_finite_numbers():
    assert acf.coerce("timeout_min", "30") == 30
    assert acf.coerce("timeout_min", "1.5") == 1.5


# -- agreement with the turn path (F-2) ---------------------------------------------

@pytest.mark.parametrize("cfg", [
    {"family": "claude", "model_family": "claude", "cmd": "claude -p {prompt}",
     "reasoning": "extended-thinking"},
    {"family": "claude", "model_family": "claude", "cmd": "claude -p {prompt}",
     "reasoning": "default"},
    {"family": "claude", "model_family": "claude", "cmd": PREFIX + "claude -p {prompt}"},
    {"family": "claude", "model_family": "claude", "cmd": PREFIX + "claude -p {prompt}",
     "reasoning": "default"},
    {"family": "claude", "model_family": "claude", "cmd": PREFIX + "claude -p {prompt}",
     "reasoning": "extended-thinking"},
    {"family": "claude", "model_family": "claude", "cmd": "claude -p {prompt}"},
])
def test_turn_and_one_shot_agree_on_the_claude_env_prefix(cfg):
    turn = hi.turn_effort(cfg) is not None
    one_shot = ac.apply_effort(cfg)["cmd"].startswith(PREFIX)
    assert turn == one_shot


# -- terminal menu --------------------------------------------------------------------

class RecordUI:
    """Answers menu questions in order and records every choice list it was shown."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.shown = []

    def _next(self, message):
        assert self.answers, f"unexpected question: {message}"
        return self.answers.pop(0)

    def select(self, message, choices, back=True):
        self.shown.append((message, list(choices)))
        answer = self._next(message)
        if answer == "BACK":
            return menu.BACK
        values = [value for _, value in choices]
        assert answer in values, (answer, values)
        return answer

    def checkbox(self, message, choices, checked=()):
        return self._next(message)

    def text(self, message, default=""):
        answer = self._next(message)
        return default if answer == "DEFAULT" else answer

    def confirm(self, message, default=False):
        return self._next(message)


def test_menu_claude_extended_choice_sets_reasoning_and_keeps_the_command(repo):
    before = _registry_of(repo)["agents"]["claude"]["cmd"]
    menu.agent_settings(RecordUI(["default", "effort", "claude", "extended-thinking", "save", True]))
    reg = _registry_of(repo)
    assert reg["agents"]["claude"]["reasoning"] == "extended-thinking"
    assert reg["agents"]["claude"]["cmd"] == before


def test_menu_current_choice_reads_the_stored_reasoning(repo):
    _set_registry(repo, "claude", reasoning="extended-thinking")
    ui = RecordUI(["default", "effort", "claude", "default", "save", True])
    menu.agent_settings(ui)
    message, choices = next(c for c in ui.shown if c[0] == "claude: effort")
    labels = {value: label for label, value in choices}
    assert "(current)" in labels["extended-thinking"]
    assert "(current)" not in labels["default"]
    assert _registry_of(repo)["agents"]["claude"]["reasoning"] == "default"
