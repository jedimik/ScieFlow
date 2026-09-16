import subprocess

import pytest

import scieflow.news.agents as agents_mod
from scieflow.news.agents import (
    AGENT_COMMANDS,
    CURATED_MODELS,
    THINKING_TOKENS,
    AgentError,
    build_command,
    discover_models,
    run_agent,
)


def test_command_shapes():
    assert AGENT_COMMANDS["claude"]("hi") == ["claude", "--allowedTools", "WebSearch", "WebFetch", "-p", "hi"]
    assert AGENT_COMMANDS["codex"]("hi") == ["codex", "exec", "hi"]
    assert AGENT_COMMANDS["agy"]("hi") == ["agy", "-p", "hi"]


def test_unknown_agent():
    with pytest.raises(AgentError, match="unknown agent"):
        run_agent("gemini", "hi", timeout=5)


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", lambda name: None)
    with pytest.raises(AgentError, match="--agent"):
        run_agent("claude", "hi", timeout=5)


def _fake_which(name):
    return f"/usr/bin/{name}"


def test_success(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", _fake_which)

    def fake_run(cmd, **kwargs):
        assert cmd == ["claude", "--allowedTools", "WebSearch", "WebFetch", "-p", "hi"]
        assert kwargs["timeout"] == 7
        return subprocess.CompletedProcess(cmd, 0, stdout="## Answer\n", stderr="")

    monkeypatch.setattr(agents_mod.subprocess, "run", fake_run)
    assert run_agent("claude", "hi", timeout=7) == "## Answer\n"


def test_nonzero_exit(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", _fake_which)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom")

    monkeypatch.setattr(agents_mod.subprocess, "run", fake_run)
    with pytest.raises(AgentError, match="exited with 2.*boom"):
        run_agent("codex", "hi", timeout=5)


def test_timeout(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", _fake_which)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(agents_mod.subprocess, "run", fake_run)
    with pytest.raises(AgentError, match="timed out after 5s"):
        run_agent("agy", "hi", timeout=5)


def test_build_command_defaults_match_legacy():
    argv, env = build_command("claude", "hi")
    assert argv == ["claude", "--allowedTools", "WebSearch", "WebFetch", "-p", "hi"]
    assert env == {}
    assert build_command("codex", "hi") == (["codex", "exec", "hi"], {})
    assert build_command("agy", "hi") == (["agy", "-p", "hi"], {})


def test_build_command_with_model_and_reasoning():
    argv, env = build_command("claude", "hi", model="claude-fable-5", reasoning="high")
    assert "--model" in argv and "claude-fable-5" in argv
    assert env == {"MAX_THINKING_TOKENS": THINKING_TOKENS["high"]}

    argv, env = build_command("codex", "hi", model="gpt-5.2", reasoning="low")
    assert argv[:2] == ["codex", "exec"]
    assert "-m" in argv and "gpt-5.2" in argv
    assert '-c' in argv and 'model_reasoning_effort="low"' in argv
    assert argv[-1] == "hi"
    assert env == {}

    argv, env = build_command("agy", "hi", model="Gemini 3.1 Pro (High)", reasoning="high")
    assert "--model" in argv and "Gemini 3.1 Pro (High)" in argv
    assert env == {}  # agy: reasoning folded into model names, env untouched


def test_run_agent_passes_model_env(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", lambda name: "/usr/bin/claude")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(agents_mod.subprocess, "run", fake_run)
    run_agent("claude", "hi", timeout=5, model="m1", reasoning="low")
    assert "m1" in captured["cmd"]
    assert captured["env"]["MAX_THINKING_TOKENS"] == THINKING_TOKENS["low"]


def test_discover_models_agy_parses_lines(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["agy", "models"]
        return subprocess.CompletedProcess(cmd, 0, stdout="A (High)\n\nB (Low)\n", stderr="")

    monkeypatch.setattr(agents_mod.shutil, "which", lambda name: "/usr/bin/agy")
    monkeypatch.setattr(agents_mod.subprocess, "run", fake_run)
    assert discover_models("agy") == ["A (High)", "B (Low)"]


def test_discover_models_fallback_curated(monkeypatch):
    monkeypatch.setattr(agents_mod.shutil, "which", lambda name: None)
    from scieflow.news.agents import curated_models

    assert discover_models("codex") == curated_models("codex")
    assert discover_models("claude") == curated_models("claude")


def test_curated_models_come_from_the_shared_registry_menu():
    from scieflow.core import config
    from scieflow.news.agents import curated_models

    registry = config.load_agents(config.repo_root())
    assert curated_models("claude") == registry["claude"]["menu"]["models"]
    assert curated_models("codex") == registry["codex"]["menu"]["models"]


def test_curated_models_fall_back_without_a_registry(monkeypatch):
    from scieflow.core import config
    from scieflow.news.agents import curated_models

    def broken_root():
        raise FileNotFoundError("no repo")

    monkeypatch.setattr(config, "repo_root", broken_root)
    assert curated_models("codex") == CURATED_MODELS["codex"]


def test_discover_models_unknown_agent():
    with pytest.raises(AgentError, match="unknown agent"):
        discover_models("gemini")


def test_build_command_invalid_reasoning_raises():
    with pytest.raises(AgentError, match="invalid reasoning level"):
        build_command("claude", "hi", reasoning="extreme")
