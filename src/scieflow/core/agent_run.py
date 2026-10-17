"""Invoke one agent CLI headless, per config/agents.yml.

usage: scieflow agent run <agent> <prompt_file> <transcript_file> [--cwd DIR]
Substitutes {model}/{reasoning}/{prompt}/{root}/{python}/{session} in the cmd
template, enforces timeout_min, runs from --cwd (default: repo root), saves
stdout to <transcript_file>. `{session}` fills in only for a conversational
dispatch's `resume_cmd` — the id a previous turn reported. When the prompt
lives inside workspace/<slug>/, that run's `agent_overrides:` (config.yml,
alongside status.yml) are merged over the registry entry. Exit codes: 0 ok,
124 timeout, otherwise the agent's exit code.
"""

import argparse
import hashlib
import math
import os
import re
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from scieflow.core import agent_config
from scieflow.core import config
from scieflow.core import events
from scieflow.core import jobs
from scieflow.core import legacy
from scieflow.core import sandbox
from scieflow.core import sessions
from scieflow.core import harness_commands
from scieflow.core.harness_commands import HarnessIdentity
from scieflow.core.turn_input import HumanSubmission, validate_marked_input
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
    layout: sandbox.Layout | None = None  # the allow-list view; verify and launch share it


def _prompt_via_stdin_safe(template: str) -> bool:
    """True when dropping `{prompt}` from `template` still leaves a command
    that reads the prompt from stdin — false when `{prompt}` is a flag's
    required value (e.g. agy's `--print {prompt}`), where simply removing the
    token leaves that flag dangling to eat whatever argv comes next instead.

    `claude`'s session/resume commands and `codex exec resume` both put
    `{prompt}` last, after a plain value (`{session}` or nothing) — dropping
    it just shortens the argv and the CLI reads stdin. `agy`'s session/resume
    commands put it right after `--print`, which needs a value — dropping it
    breaks the invocation instead.
    """
    tokens = shlex.split(template)
    if "{prompt}" not in tokens:
        return True
    idx = tokens.index("{prompt}")
    preceding = tokens[idx - 1] if idx > 0 else ""
    return not preceding.startswith("-")


def build_argv(agent_cfg: dict, prompt: str, root: Path,
               include_prompt: bool = True, template: str | None = None,
               session: str = "") -> list[str]:
    model = str(agent_cfg.get("model", ""))
    reasoning = str(agent_cfg.get("reasoning", ""))
    cmd_template = template if template is not None else agent_cfg["cmd"]
    return _template_argv(cmd_template, prompt, root, model=model, reasoning=reasoning,
                          python=sys.executable, session=session, include_prompt=include_prompt)


def _template_token(token: str, prompt: str, root: Path, *, model: str,
                    reasoning: str, python: str, session: str) -> str:
    """Expand recognized ORIGINAL-token placeholders once; inserted values are literal."""
    values = {'model': model, 'reasoning': reasoning, 'root': str(root),
              'python': python, 'session': session, 'prompt': prompt}
    return re.sub(r'\{(model|reasoning|root|python|session|prompt)\}',
                  lambda match: values[match.group(1)], token)


def _template_argv(cmd_template: str, prompt: str, root: Path, *, model: str,
                   reasoning: str, python: str, session: str, include_prompt: bool) -> list[str]:
    """Split BEFORE literal substitution; all effect-bearing operands are arguments."""
    argv = []
    for token in shlex.split(cmd_template):
        if not include_prompt and "{prompt}" in token:
            continue
        argv.append(_template_token(token, prompt, root, model=model, reasoning=reasoning,
                                    python=python, session=session))
    return argv


def _validate_turn_template(template: object, agent: str, key: str) -> None:
    """Full-send structural bindings only, not generic CLI grammar or certification."""
    if type(template) is not str:
        raise DispatchError(f'{agent} cannot host a conversation: no valid {key}')
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise DispatchError(f'{agent} cannot host a conversation: invalid {key}: {exc}') from exc
    if not tokens:
        raise DispatchError(f'{agent} cannot host a conversation: no valid {key}')
    required = ('{prompt}', '{session}') if key == 'resume_cmd' else ('{prompt}',)
    for binding in required:
        if (sum(token.count(binding) for token in tokens) != 1 or
                binding not in tokens[1:]):
            raise DispatchError(
                f'{agent} cannot host a conversation: {key} requires exactly one '
                f'standalone non-executable {binding} operand')


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
    return _compose_text(agreed, prompt)


def _compose_text(agreed: str, prompt: str, *, command_first: bool = False) -> str:
    if not agreed:
        return prompt
    if command_first:
        return f"{prompt}\n\n---\n\n{CHARTER_HEADER}\n\n{agreed}"
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


def refusal_run_dir(prompt_file: Path) -> Path | None:
    """The run a refused prompt belongs to, so its job.refused can be recorded (F13)."""
    try:
        return owning_run_workspace(Path(prompt_file))
    except (OSError, ValueError):
        return None


def load_prompt_override(prompt_file: Path, agent: str) -> dict:
    """Load only the structurally owning run's per-agent override."""

    workspace = owning_run_workspace(prompt_file)
    if workspace is None:
        return {}
    return _run_override(_load_owning_run_config(workspace), workspace, agent)


def _load_owning_run_config(workspace: Path, *, required: bool = False) -> dict:
    """Shared control-pair loader. New turns require the pair, old prompts may predate it."""
    config_path = workspace / "config.yml"
    status_path = workspace / "status.yml"
    present = [path.exists() or path.is_symlink() for path in (config_path, status_path)]
    if not any(present) and not required:
        return {}
    if not all(present):
        raise ValueError(
            f"owning run must contain both config.yml and status.yml: {workspace}"
        )
    for path in (config_path, status_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"owning run control must be a regular file: {path}")
    data = yaml.safe_load(config_path.read_text())
    if data is None or (not required and not data):
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"owning run config must be a mapping: {config_path}")
    return data


def _run_override(data: dict, workspace: Path, agent: str, *, strict: bool = False) -> dict:
    config_path = workspace / 'config.yml'
    if strict and data.get('agent_overrides') is not None and not isinstance(data['agent_overrides'], dict):
        raise ValueError(f'agent_overrides must be a mapping: {config_path}')
    overrides = data.get("agent_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"agent_overrides must be a mapping: {config_path}")
    if strict and overrides.get(agent) is not None and not isinstance(overrides[agent], dict):
        raise ValueError(f'agent_overrides.{agent} must be a mapping: {config_path}')
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
            sandbox_enabled: bool = True,
            session: str | None = None, conversational: bool = False) -> Dispatch:
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
    template = None
    if conversational:
        key = "resume_cmd" if session else "session_cmd"
        template = agent_cfg.get(key)
        if not template:
            raise DispatchError(
                f"{agent} cannot host a conversation: no {key} in its configuration")

    use_stdin = len(prompt.encode()) > PROMPT_ARGV_LIMIT
    if use_stdin and conversational:
        # The conversational template (session_cmd/resume_cmd) must win over
        # a plain stdin_cmd here: stdin_cmd is the ordinary one-shot command
        # and carries neither --resume/--conversation nor the session id, so
        # using it for an oversized turn silently drops both the turn's
        # resume and any id to record (finding 2 of the 2026-09-25 review).
        if not _prompt_via_stdin_safe(template):
            raise DispatchError(
                f"{agent}'s {key} takes the prompt as a flag's value ({template!r}) "
                "with no stdin form of its own, and this turn's composed prompt is "
                f"{len(prompt.encode())} bytes, over the {PROMPT_ARGV_LIMIT}-byte argv "
                "limit — it cannot be sent this way")
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=template, session=session or "")
    elif use_stdin and "stdin_cmd" in agent_cfg:
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=agent_cfg["stdin_cmd"])
    elif use_stdin:
        # No stdin_cmd: drop the {prompt} token from the normal command and
        # send the text on stdin instead (stdin_text below carries it).
        argv = build_argv(agent_cfg, prompt, root, include_prompt=False,
                          template=template, session=session or "")
    else:
        argv = build_argv(agent_cfg, prompt, root, template=template, session=session or "")
    disabled = sandbox_disabled_in_run(project, run_dir)
    use_sandbox = sandbox_enabled and not disabled
    writable = (sandbox.writable_for(project, run_dir=run_dir, coordinator=False)
                if use_sandbox else None)
    # The allow-list view and the dispatch's own HOME (D1, D2). Read-only here:
    # the home directory itself is created by wrap(), next to the writable mkdir.
    layout = (sandbox.dispatch_layout(project, run_dir=run_dir, agent=agent)
              if writable is not None else None)
    env = sandbox.dispatch_environment(writable[0], layout=layout) if layout is not None else None
    return Dispatch(agent=agent, argv=argv, cwd=cwd,
                    stdin_text=prompt if use_stdin else None,
                    timeout_s=float(agent_cfg.get("timeout_min", 10)) * 60,
                    run_dir=run_dir, writable=writable, env=env, layout=layout)


def _validate_turn_record(record) -> None:
    """Reject mutable/wrong-shaped constructor inputs, not privileged Python tampering."""
    for f in fields(record):
        value = getattr(record, f.name)
        if f.name in ('root', 'run_dir', 'cwd'):
            valid = isinstance(value, Path) and value.is_absolute()
        elif f.name == 'execution_path':
            valid = value is None or (isinstance(value, Path) and value.is_absolute())
        elif f.name == 'execution_facts':
            valid = type(value) is tuple and all(type(p) is sandbox.ProtectionPath for p in value)
        elif f.name == 'protection':
            valid = type(value) is sandbox.TurnProtection
        elif f.name == 'harness_identity':
            valid = type(value) is HarnessIdentity
        elif f.name == 'env_items':
            valid = value is None or (type(value) is tuple and all(type(pair) is tuple and
                    len(pair) == 2 and all(type(item) is str for item in pair) for pair in value))
        elif f.name == 'layout':
            valid = value is None or type(value) is sandbox.Layout
        elif f.name == 'argv':
            valid = type(value) is tuple and bool(value) and all(type(item) is str for item in value)
        elif f.name in ('accepted_bytes', 'composed_bytes'):
            valid = type(value) is bytes
        elif f.name in ('session', 'stdin_text', 'selection_revision'):
            valid = value is None or type(value) is str
        elif f.name in ('prompt_argv_limit', 'charter_version'):
            valid = type(value) is int and value >= 0
        elif f.name == 'timeout_s':
            valid = type(value) is float and math.isfinite(value) and value > 0
        else:
            valid = type(value) is str
        if not valid:
            raise TypeError(f'{f.name} must have its immutable declared shape')


@dataclass(frozen=True, slots=True)
class ResolvedTurn:
    """No-artifact conversational inputs, captured once; not admission authority."""
    agent: str
    family: str
    root: Path
    run_dir: Path
    cwd: Path
    session: str | None
    session_template: str
    resume_template: str
    model: str
    reasoning: str
    python_executable: str
    prompt_argv_limit: int
    timeout_s: float
    protection: sandbox.TurnProtection
    env_items: tuple[tuple[str, str], ...] | None = field(repr=False)
    harness_identity: HarnessIdentity
    charter_text: str
    charter_version: int
    charter_sha256: str
    execution_path: Path | None = None
    execution_facts: tuple[sandbox.ProtectionPath, ...] = ()
    layout: sandbox.Layout | None = None

    def __post_init__(self):
        _validate_turn_record(self)


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    """Pure accepted/composed bytes and immutable launch inputs, never a Dispatch alias.

    The `layout` is the one resolve_turn captured: verify and launch both use it.
    """
    agent: str
    family: str
    run_dir: Path
    cwd: Path
    session: str | None
    argv: tuple[str, ...]
    stdin_text: str | None
    timeout_s: float
    protection: sandbox.TurnProtection
    env_items: tuple[tuple[str, str], ...] | None = field(repr=False)
    harness_identity: HarnessIdentity
    accepted_bytes: bytes
    composed_bytes: bytes
    input_kind: str
    selection_revision: str | None
    composition_order: str
    charter_version: int
    charter_sha256: str
    policy_revision: str
    execution_path: Path | None = None
    execution_facts: tuple[sandbox.ProtectionPath, ...] = ()
    layout: sandbox.Layout | None = None

    def __post_init__(self):
        _validate_turn_record(self)


def _capture_executable(token: str, cwd: Path) -> tuple[str | None, str | None]:
    """Optional local executable PATH identity, never its unreviewed contents.

    No trusted digest/version/effect inputs are approved yet. Decline those
    capability fields rather than reading an override-selected arbitrary file.
    G4/G5 must establish safe bounded byte readers for a deployment explicitly.
    """
    if not token or '\0' in token:
        return None, None
    located = str(cwd / token) if '/' in token and not Path(token).is_absolute() else token
    located = shutil.which(located)
    if located is None:
        return None, None
    try:
        path = Path(located).resolve(strict=True)
        if stat.S_ISREG(path.stat().st_mode):
            return str(path), None
        return None, None
    except (OSError, ValueError, RuntimeError):
        return None, None


def _capture_execution(token: str, cwd: Path, env: dict):
    """Select once using captured PATH/cwd, preserving lexical invocation semantics."""
    if not token or '\0' in token:
        return None, (), None
    if '/' in token:
        located = Path(token) if Path(token).is_absolute() else cwd / token
        if not located.is_file() or not os.access(located, os.X_OK):
            return None, (), None
    else:
        located = None
        for entry in os.get_exec_path(env):
            base = Path(entry) if entry else cwd
            if not base.is_absolute():
                base = cwd / base
            candidate = base / token
            if candidate.is_file() and os.access(candidate, os.X_OK):
                located = candidate
                break
        if located is None:
            return None, (), None
    located = located.absolute()  # Never resolve the invocation spelling.
    facts = sandbox._capture_path_facts([located])
    target = located.resolve(strict=True)
    if not stat.S_ISREG(target.stat().st_mode):
        return None, (), None
    return located, facts, str(target)


def resolve_turn(project: Project, agent: str, *, run_dir: Path,
                 session: str | None, cwd: Path | None = None,
                 create_home: bool = False) -> ResolvedTurn:
    """Full conversational resolution without a prompt file or any writes/launch.

    `create_home` (the live turn path only) creates the agent's sandboxed HOME inside the
    one protection capture, before its facts are recorded (F3). Without it nothing is written.
    Later consumers must reserve BEFORE calling and validate the SAME protection.
    """
    try:
        root = project.root.resolve(strict=True)
        run = Path(run_dir).resolve(strict=True)
        owners = [p for p in (run, *run.parents) if p.parent.name == 'workspace']
        if owners != [run] or run.parent != root / 'workspace' or not run.is_dir():
            raise ValueError('explicit run must be the unique structural owner in this project')
        data = _load_owning_run_config(run, required=True)
        override = _run_override(data, run, agent, strict=True)
        agents = config.load_agents(root)
        if not isinstance(agents, dict) or any(type(name) is not str for name in agents):
            raise ValueError('agent registry must be a mapping with text names')
        if type(agent) is not str or agent not in agents:
            raise DispatchError(f"unknown agent: {agent} (known: {', '.join(agents)})")
        cfg = agents[agent]
        if not isinstance(cfg, dict):
            raise ValueError('agent configuration must be a mapping')
        cfg = {**cfg, **override}
        if type(cfg.get('enabled', True)) is not bool or not cfg.get('enabled', True):
            raise DispatchError(f'{agent} is disabled')
        family = cfg.get('family', '')
        if type(family) is not str or family not in sessions.FAMILIES:
            raise DispatchError(f'{agent} cannot host a conversation: unregistered family')
        templates = []
        for key in ('session_cmd', 'resume_cmd'):
            template = cfg.get(key)
            _validate_turn_template(template, agent, key)
            templates.append(template)
        model, reasoning = cfg.get('model', ''), cfg.get('reasoning', '')
        if type(model) is not str or type(reasoning) is not str:
            raise ValueError('model and reasoning must be text')
        if session is not None and type(session) is not str:
            raise ValueError('session must be text or None')
        timeout = cfg.get('timeout_min', 10)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a positive finite number')
        work = Path(cwd) if cwd is not None else root
        if not work.is_absolute():
            work = root / work
        work = work.resolve(strict=True)
        if not work.is_dir():
            raise ValueError('cwd must be a directory')
        if 'sandbox' in data:
            raise sandbox.SandboxError(f'{run / "config.yml"}: {HATCH_MOVED}')
        protection = (sandbox.resolve_turn_protection(project, run_dir=run) if not create_home else
                      sandbox.resolve_turn_protection(
                          project, run_dir=run,
                          prepare_home=lambda home_run: sandbox.agent_home(home_run, agent)))
        # The allow-list view and the turn's own HOME, computed read-only (A12).
        layout = (sandbox.dispatch_layout(project, run_dir=run, agent=agent)
                  if protection.sandbox_enabled else None)
        env = (sandbox.dispatch_environment(protection.writable[0], layout=layout)
               if layout is not None else dict(os.environ))
        env_items = tuple(env.items())
        agreed = charter.snapshot(run)
        text, version = agreed['text'].strip(), agreed['current']
        if type(version) is not int or version < 0:
            raise ValueError('charter version must be nonnegative integer')
        charter_digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        python = sys.executable
        first_token = shlex.split(templates[1] if session else templates[0])[0]
        first_token = _template_token(first_token, '', root, model=model, reasoning=reasoning,
                                      python=python, session=session or '')
        execution_path, execution_facts, executable = _capture_execution(first_token, work, env)
        identity = HarnessIdentity(agent, family, True, executable, None, None,
                                   templates[0], templates[1], model, reasoning, str(work),
                                   None, None, None, False)
        return ResolvedTurn(agent, family, root, run, work, session, templates[0], templates[1],
                            model, reasoning, python, PROMPT_ARGV_LIMIT, float(timeout) * 60,
                            protection, env_items, identity, text, version, charter_digest,
                            execution_path, execution_facts, layout)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError, charter.CharterError) as exc:
        raise DispatchError(f'invalid conversational turn configuration: {exc}') from exc


def prepare_turn(resolved: ResolvedTurn, submission: HumanSubmission) -> PreparedTurn:
    """Pure preparation from the SAME accepted submission and captured resolution.

    No artifacts, config/charter/policy reads, path discovery or process-global inputs.
    """
    if type(resolved) is not ResolvedTurn or type(submission) is not HumanSubmission:
        raise TypeError('prepare_turn requires exact immutable ResolvedTurn and HumanSubmission')
    raw = submission.message.encode('utf-8')
    marked = submission.mode == 'harness-command'
    revision = ''
    if marked:
        raw = validate_marked_input(submission.message)
        harness_commands.validate_candidate_raw(submission.message)
        support = harness_commands.support_for(resolved.harness_identity)
        if not resolved.protection.sandbox_enabled or not support.enabled:
            raise DispatchError(support.reason or 'marked turns require confinement')
        revision = support.policy_revision
    composed = _compose_text(resolved.charter_text, submission.message, command_first=marked)
    use_stdin = len(composed.encode('utf-8')) > resolved.prompt_argv_limit
    template = resolved.resume_template if resolved.session else resolved.session_template
    _validate_turn_template(template, resolved.agent, 'resume_cmd' if resolved.session else 'session_cmd')
    if use_stdin and not _prompt_via_stdin_safe(template):
        raise DispatchError(f'{resolved.agent} cannot send oversized conversational input safely via stdin')
    argv = _template_argv(template, composed, resolved.root, model=resolved.model,
                          reasoning=resolved.reasoning, python=resolved.python_executable,
                          include_prompt=not use_stdin, session=resolved.session or '')
    if resolved.execution_path is not None:
        argv[0] = str(resolved.execution_path)
    return PreparedTurn(resolved.agent, resolved.family, resolved.run_dir, resolved.cwd,
                        resolved.session, tuple(argv), composed if use_stdin else None,
                        resolved.timeout_s, resolved.protection, resolved.env_items,
                        resolved.harness_identity, raw, composed.encode('utf-8'), submission.mode,
                        submission.selection_revision, 'command-first' if marked else 'charter-first', resolved.charter_version,
                        resolved.charter_sha256, revision, resolved.execution_path, resolved.execution_facts,
                        resolved.layout)


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
        run_dir = refusal_run_dir(args.prompt_file)
        if run_dir is not None:
            events.emit(run_dir, "job.refused", "system", reason="sandbox", detail=str(e))
        sys.exit(SANDBOX_EXIT)

    if d.writable is None:
        if d.run_dir is not None:
            events.emit(d.run_dir, "sandbox.disabled", "human", agent=d.agent,
                        why="--no-sandbox" if args.no_sandbox
                            else f"{sandbox.ALLOWLIST_FILE} unsandboxed_runs")
    else:
        try:
            sandbox.verify(d.writable, d.cwd, mask=sandbox.masks_for(d.writable, run_dir=d.run_dir),
                           layout=d.layout)
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
                                sandbox_writable=d.writable, sandbox_layout=d.layout, env=d.env)
    except sandbox.SandboxError as e:
        # A11: the launch itself was refused after verify passed.
        _refuse(args.transcript_file, f"{d.agent}: sandbox refused, {e}\n")
        if d.run_dir is not None:
            events.emit(d.run_dir, "job.refused", "system", reason="sandbox", detail=str(e))
        sys.exit(SANDBOX_EXIT)
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
