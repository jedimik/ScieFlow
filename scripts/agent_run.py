#!/usr/bin/env python3
"""Invoke one agent CLI headless, per config/agents.yml.

usage: agent_run.py <agent> <prompt_file> <transcript_file> [--cwd DIR]
Substitutes {model}/{prompt}/{root} in the cmd template, enforces timeout_min,
runs from --cwd (default: ScieFlow repo root), saves stdout to <transcript_file>.
Exit codes: 0 ok, 124 timeout, otherwise the agent's exit code.
Adapted from ResearchX scripts/agent_run.py; adds --cwd and {root}.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from sflib import config

# Linux caps a single argv string around 128 KiB; above this size the prompt
# goes to the agent via stdin (using stdin_cmd when defined) instead of argv.
PROMPT_ARGV_LIMIT = int(os.environ.get("SCIEFLOW_PROMPT_ARGV_LIMIT", "100000"))


def build_argv(agent_cfg: dict, prompt: str, root: Path,
               include_prompt: bool = True, template: str | None = None) -> list[str]:
    model = str(agent_cfg.get("model", ""))
    cmd_template = template if template is not None else agent_cfg["cmd"]
    argv = []
    for token in shlex.split(cmd_template):
        if not include_prompt and "{prompt}" in token:
            continue
        token = token.replace("{model}", model).replace("{root}", str(root))
        argv.append(token.replace("{prompt}", prompt))
    return argv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent")
    ap.add_argument("prompt_file", type=Path)
    ap.add_argument("transcript_file", type=Path)
    ap.add_argument("--cwd", type=Path, default=None,
                    help="working directory for the agent (e.g. vendors/ExperimentX)")
    args = ap.parse_args()

    root = config.repo_root()
    agents = config.load_agents(root)
    if args.agent not in agents:
        sys.exit(f"unknown agent: {args.agent} (known: {', '.join(agents)})")
    agent_cfg = agents[args.agent]
    cwd = args.cwd or root

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
        sys.exit(f"{args.agent}: failed to launch subprocess: {e}")

    args.transcript_file.parent.mkdir(parents=True, exist_ok=True)
    args.transcript_file.write_text(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode)
    print(f"{args.agent}: done, transcript at {args.transcript_file}")


if __name__ == "__main__":
    main()
