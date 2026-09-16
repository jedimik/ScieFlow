from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

VALID_AGENTS = ("claude", "codex", "agy")

THINKING_TOKENS = {"low": "4096", "medium": "16384", "high": "31999"}

CURATED_MODELS: dict[str, list[str]] = {
    "claude": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
    "codex": ["gpt-5.2-codex", "gpt-5.2"],
    "agy": [],  # always discovered live via `agy models`
}


class AgentError(Exception):
    """Agent CLI invocation failed."""


def agent_available(agent: str) -> bool:
    return shutil.which(agent) is not None


def build_command(
    agent: str, prompt: str, model: str | None = None, reasoning: str | None = None
) -> tuple[list[str], dict[str, str]]:
    if reasoning is not None and reasoning not in THINKING_TOKENS:
        raise AgentError(f"invalid reasoning level: {reasoning!r}")
    if agent == "claude":
        argv = ["claude", "--allowedTools", "WebSearch", "WebFetch"]
        if model:
            argv += ["--model", model]
        argv += ["-p", prompt]
        env = {"MAX_THINKING_TOKENS": THINKING_TOKENS[reasoning]} if reasoning else {}
        return argv, env
    if agent == "codex":
        argv = ["codex", "exec"]
        if model:
            argv += ["-m", model]
        if reasoning:
            argv += ["-c", f'model_reasoning_effort="{reasoning}"']
        argv.append(prompt)
        return argv, {}
    if agent == "agy":
        argv = ["agy"]
        if model:
            argv += ["--model", model]
        argv += ["-p", prompt]
        return argv, {}
    raise AgentError(f"unknown agent: {agent}")


# Kept for backward compat; thin wrappers delegating to build_command with no
# model/reasoning override.
AGENT_COMMANDS: dict[str, Callable[[str], list[str]]] = {
    name: (lambda prompt, name=name: build_command(name, prompt)[0]) for name in VALID_AGENTS
}


def run_agent(
    agent: str,
    prompt: str,
    timeout: int,
    model: str | None = None,
    reasoning: str | None = None,
) -> str:
    if agent not in VALID_AGENTS:
        raise AgentError(f"unknown agent: {agent}")
    if not agent_available(agent):
        raise AgentError(
            f"agent CLI '{agent}' not found on PATH; "
            f"install it or pick another backend with --agent"
        )
    cmd, extra_env = build_command(agent, prompt, model=model, reasoning=reasoning)
    run_kwargs = {}
    if extra_env:
        run_kwargs["env"] = {**os.environ, **extra_env}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            **run_kwargs,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"'{agent}' timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise AgentError(
            f"'{agent}' exited with {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout


def discover_models(agent: str, timeout: int = 30) -> list[str]:
    """Best-effort list of model names for the agent; curated fallback.

    Step 1 (empirical, see report): neither `claude --help` nor
    `codex --help` / `codex exec --help` expose a model-listing subcommand
    or flag, so only `agy` supports live discovery via `agy models`. claude
    and codex always fall back to CURATED_MODELS.
    """
    if agent not in VALID_AGENTS:
        raise AgentError(f"unknown agent: {agent}")
    if agent == "agy" and agent_available("agy"):
        try:
            proc = subprocess.run(
                ["agy", "models"], capture_output=True, text=True, timeout=timeout
            )
            if proc.returncode == 0:
                lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
                if lines:
                    return lines
        except (subprocess.TimeoutExpired, OSError):
            pass
    return list(CURATED_MODELS.get(agent, []))
