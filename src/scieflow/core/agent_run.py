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
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow.core import agent_config
from scieflow.core import config
from scieflow.core import events
from scieflow.core import jobs
from scieflow.core import legacy
from scieflow.core import sandbox
from scieflow.core.project import Project
from scieflow.core.run import actions
from scieflow.core.run import charter

# Linux caps a single argv string around 128 KiB; above this size the prompt
# goes to the agent via stdin (using stdin_cmd when defined) instead of argv.
PROMPT_ARGV_LIMIT = int(legacy.env("SCIEFLOW_PROMPT_ARGV_LIMIT", "100000"))

BUDGET_EXIT = 75   # EX_TEMPFAIL: refused, not failed — never an agent's own code
SANDBOX_EXIT = 77  # EX_NOPERM: refused for want of a sandbox, not an agent failure


class DispatchError(Exception):
    """A dispatch that cannot be prepared (unknown agent, bad run config)."""


@dataclass
class Dispatch:
    agent: str
    argv: list[str]
    cwd: Path
    stdin_text: str | None
    timeout_s: float
    run_dir: Path | None
    writable: list[Path] | None = None    # None means this dispatch is unsandboxed
    env: dict | None = None               # None means inherit this process's


def build_argv(agent_cfg: dict, prompt: str, root: Path,
               include_prompt: bool = True, template: str | None = None,
               session: str = "") -> list[str]:
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
            .replace("{session}", session)
        )
        argv.append(token.replace("{prompt}", prompt))
    return argv


CHARTER_HEADER = "## The agreed plan for this run"


def compose_prompt(run_dir: Path | None, prompt: str) -> str:
    """The prompt the agent actually receives.

    The charter goes first, every time. That is the whole point of it: a long
    conversation drifts, and because ScieFlow composes each turn's prompt, the
    agent can be handed the goal again each time it speaks rather than being
    trusted to remember it.

    A run with no charter composes exactly as it did before this existed.
    """
    if run_dir is None:
        return prompt
    agreed = charter.current_text(run_dir).strip()
    if not agreed:
        return prompt
    return f"{CHARTER_HEADER}\n\n{agreed}\n\n---\n\n{prompt}"


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


HATCH_MOVED = (
    "the per-run sandbox opt-out moved out of the run's own config.yml, which a "
    f"sandboxed agent can write, into {sandbox.ALLOWLIST_FILE}, which it cannot. "
    "Remove the `sandbox:` key from this run's config.yml; to keep the opt-out, "
    f"add the run's slug under `unsandboxed_runs:` in {sandbox.ALLOWLIST_FILE} "
    "with a reason, or pass --no-sandbox for a one-off dispatch")


def sandbox_disabled_in_run(project: Project, run_dir: Path | None) -> bool:
    """True when config/sandbox.yml lets this run dispatch unsandboxed.

    A `sandbox:` key inside the run's own config.yml is never honoured — that
    file lives in the dispatch's writable bind, so honouring it would let an
    agent switch off its own confinement. A stale one would be silently
    misleading, so it refuses the dispatch instead of being ignored quietly.
    """
    if run_dir is None:
        return False
    path = Path(run_dir) / "config.yml"
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            data = {}          # malformed or unreadable: the loader reports it
        if isinstance(data, dict) and "sandbox" in data:
            raise sandbox.SandboxError(f"{path}: {HATCH_MOVED}")
    return Path(run_dir).name in sandbox.unsandboxed_runs(project)


def prepare(project: Project, agent: str, prompt_file: Path,
            cwd: Path | None = None, role: str | None = None, *,
            sandbox_enabled: bool = True) -> Dispatch:
    root = project.root
    agents = config.load_agents(root)
    if agent not in agents:
        raise DispatchError(f"unknown agent: {agent} (known: {', '.join(agents)})")
    agent_cfg = agents[agent]
    try:
        override = load_prompt_override(prompt_file, agent)
        run_dir = owning_run_workspace(prompt_file)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DispatchError(f"invalid owning run configuration: {exc}") from exc
    if override:
        agent_cfg = {**agent_cfg, **override}
    if role:
        eff = agent_config.resolve_dir(root, run_dir)
        assigned = eff.value(role)
        names = assigned if isinstance(assigned, list) else [assigned]
        if agent not in names:
            raise DispatchError(f"{agent} is not assigned to {role} "
                                f"(assigned: {', '.join(map(str, names))})")
        override_setting = eff.role_overrides.get(role, {}).get(agent)
        if override_setting:
            agent_cfg = agent_config.apply_role_override(agent_cfg, override_setting.value)
    cwd = cwd or root
    if not cwd.is_absolute():
        cwd = root / cwd
    try:
        prompt = compose_prompt(run_dir, prompt_file.read_text())
    except (OSError, ValueError, yaml.YAMLError, charter.CharterError) as exc:
        raise DispatchError(f"invalid run charter: {exc}") from exc
    use_stdin = len(prompt.encode()) > PROMPT_ARGV_LIMIT
    if use_stdin and "stdin_cmd" in agent_cfg:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=agent_cfg["stdin_cmd"])
    elif use_stdin:
        # No stdin_cmd: drop the {prompt} token from the normal command and
        # send the text on stdin instead (stdin_text below carries it).
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False)
    else:
        argv = build_argv(agent_cfg, prompt, root)
    disabled = sandbox_disabled_in_run(project, run_dir)
    use_sandbox = sandbox_enabled and not disabled
    writable = (sandbox.writable_for(project, run_dir=run_dir, coordinator=False)
                if use_sandbox else None)
    # A sandboxed dispatch gets its own uv cache, inside the run it may write;
    # sharing the host's would let it hardlink into the host virtualenv.
    env = sandbox.environment(writable[0]) if writable is not None else None
    return Dispatch(agent=agent, argv=argv, cwd=cwd,
                    stdin_text=prompt if use_stdin else None,
                    timeout_s=float(agent_cfg.get("timeout_min", 10)) * 60,
                    run_dir=run_dir, writable=writable, env=env)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _refuse(transcript_file: Path, text: str) -> None:
    """Record a refusal both on the run's transcript and on stderr, so a
    human running this from a terminal sees why without opening a file."""
    _write(transcript_file, text)
    sys.stderr.write(text)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scieflow agent run")
    ap.add_argument("agent")
    ap.add_argument("prompt_file", type=Path)
    ap.add_argument("transcript_file", type=Path)
    ap.add_argument("--cwd", type=Path, default=None,
                    help="working directory for the agent (default: repo root)")
    ap.add_argument("--role", default=None,
                    help="the role this dispatch performs; applies that role's model/effort "
                         "for the agent")
    ap.add_argument("--no-sandbox", action="store_true",
                    help="run without filesystem confinement; recorded on the run's timeline")
    args = ap.parse_args(argv)

    project = Project.discover()
    try:
        d = prepare(project, args.agent, args.prompt_file, args.cwd, args.role,
                    sandbox_enabled=not args.no_sandbox)
    except DispatchError as e:
        sys.exit(str(e))
    except sandbox.SandboxError as e:
        _refuse(args.transcript_file, f"{args.agent}: sandbox refused, {e}\n")
        sys.exit(SANDBOX_EXIT)

    if d.writable is None:
        if d.run_dir is not None:
            events.emit(d.run_dir, "sandbox.disabled", "human", agent=d.agent,
                        why="--no-sandbox" if args.no_sandbox
                            else f"{sandbox.ALLOWLIST_FILE} unsandboxed_runs")
    else:
        try:
            sandbox.verify(d.writable, d.cwd)
        except sandbox.SandboxError as e:
            _refuse(args.transcript_file, f"{d.agent}: sandbox refused, {e}\n")
            if d.run_dir is not None:
                events.emit(d.run_dir, "job.refused", "system", reason="sandbox",
                            detail=str(e))
            sys.exit(SANDBOX_EXIT)

    if d.run_dir is not None:
        try:
            actions.guard_budget(d.run_dir, ("wall_minutes",))
        except actions.BudgetExhausted as e:
            _write(args.transcript_file,
                   f"{d.agent}: refused, {e} (run checkpointed; resume after raising the budget)\n")
            sys.exit(BUDGET_EXIT)

    try:
        job = jobs.run_blocking(project, d.argv, kind="agent", cwd=d.cwd, run_dir=d.run_dir,
                                label=d.agent, timeout_s=d.timeout_s, stdin_text=d.stdin_text,
                                sandbox_writable=d.writable, env=d.env)
    except OSError as e:
        _write(args.transcript_file, f"{d.agent}: failed to launch subprocess: {e}\n")
        sys.exit(f"{d.agent}: failed to launch subprocess: {e}")

    if d.run_dir is not None and job.duration_s is not None:
        actions.record_spend(d.run_dir, wall_minutes=round(job.duration_s / 60, 3))

    output = Path(job.log).read_text()
    if job.state == "timeout":
        _write(args.transcript_file,
               output + f"\n{d.agent}: timed out after {d.timeout_s:.0f}s\n")
        sys.exit(124)
    _write(args.transcript_file, output)
    if job.exit_code != 0:
        sys.stderr.write(Path(job.err).read_text())
        sys.exit(job.exit_code)
    print(f"{d.agent}: done, transcript at {args.transcript_file}")


if __name__ == "__main__":
    main()
