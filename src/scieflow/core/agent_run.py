"""Invoke one agent CLI headless, per config/agents.yml.

usage: scieflow agent run <agent> <prompt_file> <transcript_file> [--cwd DIR]
Substitutes {model}/{reasoning}/{prompt}/{root}/{python} in the cmd template,
enforces timeout_min, runs from --cwd (default: repo root), saves stdout to
<transcript_file>. When the prompt lives inside workspace/<slug>/, that run's
`agent_overrides:` (config.yml, alongside status.yml) are merged over the
registry entry. Exit codes: 0 ok, 124 timeout, otherwise the agent's exit code.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from scieflow.core import config

# Linux caps a single argv string around 128 KiB; above this size the prompt
# goes to the agent via stdin (using stdin_cmd when defined) instead of argv.
PROMPT_ARGV_LIMIT = int(os.environ.get("SCIEFLOW_PROMPT_ARGV_LIMIT", "100000"))


def build_argv(agent_cfg: dict, prompt: str, root: Path,
               include_prompt: bool = True, template: str | None = None) -> list[str]:
    model = str(agent_cfg.get("model", ""))
    reasoning = str(agent_cfg.get("reasoning", ""))
    cmd_template = template if template is not None else agent_cfg["cmd"]
    argv = []
    for token in shlex.split(cmd_template):
        if not include_prompt and "{prompt}" in token:
            continue
        token = (
            token.replace("{model}", model)
            .replace("{reasoning}", reasoning)
            .replace("{root}", str(root))
            .replace("{python}", sys.executable)
        )
        argv.append(token.replace("{prompt}", prompt))
    return argv


def owning_run_workspace(prompt_file: Path) -> Path | None:
    """Return the sole structural ``workspace/<slug>`` owner of a prompt.

    A run may place runtime prompts several levels below ``prompts/`` or
    ``logs/``.  Arbitrary closer directories containing config/status files
    are not run roots and must never override the user-approved run config.
    Nested structural workspaces are ambiguous and are rejected rather than
    choosing whichever one happens to be closest.
    """

    resolved = prompt_file.resolve(strict=True)
    candidates = [
        candidate
        for candidate in resolved.parents
        if candidate.parent.name == "workspace"
    ]
    if len(candidates) > 1:
        joined = ", ".join(str(candidate) for candidate in candidates)
        raise ValueError(f"prompt is nested below multiple run workspaces: {joined}")
    return candidates[0] if candidates else None


def load_prompt_override(prompt_file: Path, agent: str) -> dict:
    """Load only the structurally owning run's per-agent override."""

    workspace = owning_run_workspace(prompt_file)
    if workspace is None:
        return {}
    config_path = workspace / "config.yml"
    status_path = workspace / "status.yml"
    present = [path.exists() or path.is_symlink() for path in (config_path, status_path)]
    if not any(present):
        return {}
    if not all(present):
        raise ValueError(
            f"owning run must contain both config.yml and status.yml: {workspace}"
        )
    for path in (config_path, status_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"owning run control must be a regular file: {path}")
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"owning run config must be a mapping: {config_path}")
    overrides = data.get("agent_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"agent_overrides must be a mapping: {config_path}")
    override = overrides.get(agent) or {}
    if not isinstance(override, dict):
        raise ValueError(
            f"agent_overrides.{agent} must be a mapping: {config_path}"
        )
    return override


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow agent run")
    ap.add_argument("agent")
    ap.add_argument("prompt_file", type=Path)
    ap.add_argument("transcript_file", type=Path)
    ap.add_argument("--cwd", type=Path, default=None,
                    help="working directory for the agent (default: repo root)")
    args = ap.parse_args(argv)

    root = config.repo_root()
    agents = config.load_agents(root)
    if args.agent not in agents:
        sys.exit(f"unknown agent: {args.agent} (known: {', '.join(agents)})")
    agent_cfg = agents[args.agent]
    try:
        override = load_prompt_override(args.prompt_file, args.agent)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        sys.exit(f"invalid owning run configuration: {exc}")
    if override:
        agent_cfg = {**agent_cfg, **override}
    cwd = args.cwd or root
    if not cwd.is_absolute():
        cwd = root / cwd

    prompt = args.prompt_file.read_text()
    use_stdin = len(prompt.encode()) > PROMPT_ARGV_LIMIT
    if use_stdin and "stdin_cmd" in agent_cfg:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=agent_cfg["stdin_cmd"])
    else:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=not use_stdin)
    timeout = float(agent_cfg.get("timeout_min", 10)) * 60

    try:
        proc = subprocess.run(argv, input=prompt if use_stdin else None,
                              capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        args.transcript_file.parent.mkdir(parents=True, exist_ok=True)
        args.transcript_file.write_text(f"{args.agent}: timed out after {timeout:.0f}s\n")
        sys.exit(124)
    except OSError as e:
        args.transcript_file.parent.mkdir(parents=True, exist_ok=True)
        args.transcript_file.write_text(f"{args.agent}: failed to launch subprocess: {e}\n")
        sys.exit(f"{args.agent}: failed to launch subprocess: {e}")

    args.transcript_file.parent.mkdir(parents=True, exist_ok=True)
    args.transcript_file.write_text(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode)
    print(f"{args.agent}: done, transcript at {args.transcript_file}")


if __name__ == "__main__":
    main()
