"""K Task8 D7 probe binding (plan r2, DD1-DD11; operator ruling D2 on budget.yml). TEST-SUPPORT ONLY.

Nothing under src/ imports this module. It drives the real production
`service.say` path for the marked /scieflow-menu candidate, with an
in-memory, identity-exact support binding (DD5) and in-memory wrappers that
apply the per-probe controls (DD6). It never writes a policy, a template or a
release receipt. Offline tests run the synthetic stream fixture; the live
invocation is made only by the gen2 harness (tools/k_harness_capability_probe.py,
live mode), which passes its release gate before this module is used.

The report adapter (`to_harness_report`) and the sequence driver (`run_sequence`,
`resume_sequence`) take the harness gate object as a parameter, so every
validation rule and ceiling is the gen2 harness's own, not a copy.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import agent_run, harness_commands, jobs, sandbox, service
from scieflow.core.harness_commands import HarnessIdentity, SupportStatus
from scieflow.core.run import charter, conversation
from scieflow.core.turn_input import HumanSubmission

# ---- pinned constants (DD2-DD4, DD7, DD8, DD10) -------------------------

PINNED_MODEL = 'claude-opus-5-5'
PINNED_EFFORT_ENV = ('MAX_THINKING_TOKENS', '32000')
ALLOWED_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'Skill'})
TOOLS_ARG = '--tools=Read,Glob,Grep,Skill'  # R13-1: launch-time restriction (single token; exact string)
ALLOWED_SKILL = 'scieflow-menu'
FORBIDDEN_SKILL_DIR = 'skills/workspace-sync'
RUN_SLUG = 'k-probe-a1'
CANDIDATE = '/scieflow-menu'
POLICY_REVISION = 'k-task8-g4-probe'
CHARTER_TEXT = 'K Task8 probe charter: capability probe only; no scientific claims.'
CLAUDE_BINARY = Path('/home/ubuntu/.local/share/claude/versions/2.1.293')
CLAUDE_SHA256 = '8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978'
CLAUDE_VERSION = '2.1.293'
# R10-2: the pinned templates are production's claude session_cmd/resume_cmd (K10 config/agents.yml), no env prefix.
SESSION_TEMPLATE = ('claude -p --output-format=stream-json --verbose '
                    '--dangerously-skip-permissions --tools=Read,Glob,Grep,Skill --model {model} {prompt}')
RESUME_TEMPLATE = ('claude -p --output-format=stream-json --verbose '
                   '--dangerously-skip-permissions --tools=Read,Glob,Grep,Skill --model {model} --resume {session} {prompt}')
CREDENTIAL_VAR = 'CLAUDE_CODE_OAUTH_TOKEN'  # R10-4/R10-5: K10 config/sandbox.yml claude credential (env mode)
TOKEN_FILE_NAME = 'claude.token'
TOKEN_MAX_BYTES = 4096
HARD_DENY_RELATIVE = ('.ssh', '.gnupg', '.hermes', '.config/scieflow/credentials', '.claude', '.claude.json',
                      '.codex', '.gemini')  # R10-6: hard-deny set (docs/sandbox.md)
BWRAP_SOURCE_FLAGS = ('--bind', '--ro-bind', '--bind-try')
SYSTEM_SOURCES = ('/usr', '/etc', '/bin', '/sbin', '/lib', '/lib64', '/lib32', '/libx32')
TIMEOUT_MIN = 1
PROBE_ORDER = ('P1', 'P3', 'P4', 'P2', 'P5', 'P6', 'P7', 'P8')
PROBE_SPEC = {
    'P1': dict(marked=True, resume=False, stdin=False, disable=False, truncate=False, cancel=False),
    'P2': dict(marked=True, resume=False, stdin=True, disable=False, truncate=False, cancel=False),
    'P3': dict(marked=True, resume=True, stdin=False, disable=False, truncate=False, cancel=False),
    'P4': dict(marked=False, resume=True, stdin=False, disable=False, truncate=False, cancel=False),
    'P5': dict(marked=True, resume=False, stdin=False, disable=True, truncate=False, cancel=False),
    'P6': dict(marked=True, resume=False, stdin=False, disable=False, truncate=False, cancel=False),
    'P7': dict(marked=True, resume=False, stdin=False, disable=False, truncate=True, cancel=False),
    'P8': dict(marked=True, resume=False, stdin=False, disable=False, truncate=False, cancel=True),
}
CONTROL_FILES = ('skills/scieflow-menu/SKILL.md', 'skills/workspace-sync/SKILL.md',
                 'scripts/hooks/dvc-sync-reminder.py', 'config/agents.yml', 'config/sandbox.yml',
                 '.claude/settings.json')
# budget.yml is NOT in the digest: operator ruling D2 checks it structurally (budget_effect).
RUN_FILES = ('config.yml', 'charter.yml')
BUDGET_FILE = 'budget.yml'
TREE_DIRS = ('src', 'config', 'skills', '.claude', 'scripts/hooks')
PRELAUNCH_RESERVE = sandbox.VERIFY_TIMEOUT_S
KILL_GRACE = jobs.KILL_GRACE
DEFAULT_TRUNCATE_S = 6.0  # R13-4: stop points at 6 s (ceilings unchanged)
DEFAULT_CANCEL_S = 6.0
# C2/R5-3: launch env keys. Mirrors tools/k_harness_capability_probe.py LIVE_ENV_ALLOW / PRODUCTION_ENV_KEYS /
# HARNESS_ENV_KEYS; the harness suite pins the same values.
# R10-4: the launch env allow-list is production's C12_KEPT, plus LC_*, plus the credential var (below).
ENV_ALLOW = frozenset(sandbox.C12_KEPT)
ENV_PRODUCTION = frozenset()  # every production key is already in C12_KEPT
ENV_CREDENTIAL = frozenset({CREDENTIAL_VAR})
ENV_HARNESS = frozenset()  # R10-4: the harness-only PYTHONDONTWRITEBYTECODE key is no longer launch-allowed


# Empty in production. The offline suite sets it to the one key pytest injects into os.environ (C2).
ENV_TEST_RUNNER = frozenset()


CLAUDE_TOKEN_SPLIT = re.compile(r'[\s;&|()<>`$"\'=]+')


def claude_argv_denied(argv, pinned_realpath=None) -> bool:
    """C7/SR4/R5-6: an argv that would start claude: the pinned realpath, a token equal to `claude`, or a path
    component named `claude` (also inside a `sh -c` string). Same rule as the gen2 harness (H:claude_argv_denied)."""
    for arg in argv:
        text = str(arg)
        if pinned_realpath is not None and text == str(pinned_realpath):
            return True
        for token in CLAUDE_TOKEN_SPLIT.split(text):
            if token and (token == 'claude' or 'claude' in Path(token).parts):
                return True
    return False


def path_without_claude(path_value: str) -> str:
    """C7/SR4/R5-6: PATH with every directory that holds a `claude` entry removed."""
    kept = []
    for entry in path_value.split(os.pathsep):
        if entry and not ((Path(entry) / 'claude').exists() or (Path(entry) / 'claude').is_symlink()):
            kept.append(entry)
    return os.pathsep.join(kept)


CANCEL_THREADS: list = []  # C15/R5-15: every cancel thread started by a probe (joined at probe exit)


def env_refusal_keys(env_keys) -> list[str]:
    """Launch env keys outside the allowlist (plus production and harness keys), sorted; LC_* allowed."""
    allowed = ENV_ALLOW | ENV_PRODUCTION | ENV_HARNESS | ENV_CREDENTIAL | ENV_TEST_RUNNER
    return sorted(key for key in env_keys if key not in allowed and not key.startswith('LC_'))
W_ROOT = Path(__file__).resolve().parents[2]
DD1_FILES = ('tests/probes/k_task8_binding.py', 'tests/probes/test_k_task8_binding.py',
             'tests/fixtures/k_task8_stream_fixture.py')
ADMISSION_LOCK = re.compile(r'turn-admission/v1-[0-9a-f]{64}\.lock')
CHECKPOINT_SCHEMA = 'k-task8-binding-checkpoint-v1'


class ProbeRefused(RuntimeError):
    """A pre-launch refusal; the probe never reaches jobs.start."""


def exception_chain_reason(exc) -> str:
    """R6-2/CS2: `exception:<Type>: <msg>`, then ` <- <Type>: <msg>` for up to two causes; messages cut to 300 chars."""
    chain, current = [], exc
    while current is not None and len(chain) < 3:
        chain.append(f'{type(current).__name__}: {str(current)[:300]}')
        current = current.__cause__ or current.__context__
    return 'exception:' + ' <- '.join(chain)


# ---- digests and immutability (T2) ---------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def dd1_digests() -> dict:
    return {rel: sha256_file(W_ROOT / rel) for rel in DD1_FILES}


def _tree_files(root: Path, sub: str):
    base = root / sub
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def tree_digest(root: Path, subdirs=TREE_DIRS) -> str:
    """Sha256 over sorted (relative path, bytes); bytecode is excluded (DD2)."""
    digest = hashlib.sha256()
    for sub in subdirs:
        for path in _tree_files(Path(root), sub):
            digest.update(str(path.relative_to(root)).encode('utf-8') + b'\0')
            digest.update(path.read_bytes())
            digest.update(b'\0')
    return digest.hexdigest()


def pycache_listing(root: Path) -> list[str]:
    """Every __pycache__ path under K/src, for the DD2 unexpected-file check."""
    base = Path(root) / 'src'
    if not base.is_dir():
        return []
    return sorted(str(p.relative_to(root)) for p in base.rglob('__pycache__'))


def control_snapshot(k_root: Path, run_dir: Path, binary=CLAUDE_BINARY) -> dict:
    """Sha256 of every control file, the claude binary, /usr/bin/env and the run files (T2)."""
    snap = {}
    for rel in CONTROL_FILES:
        path = Path(k_root) / rel
        snap[rel] = sha256_file(path) if path.is_file() else None
    if binary is not None:
        snap['binary'] = sha256_file(Path(binary)) if Path(binary).is_file() else None
    for name in RUN_FILES:
        path = Path(run_dir) / name
        snap[f'run:{name}'] = sha256_file(path) if path.is_file() else None
    return snap


def compare_snapshots(before: dict, after: dict) -> list[str]:
    reasons = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            reasons.append(f'unexpected-permission:{key}')
    return reasons


def _read_text(path: Path):
    return Path(path).read_text(encoding='utf-8') if Path(path).is_file() else None


# ---- operator ruling D2: budget.yml structural rule -----------------------

def budget_effect(before_text: str, after_text: str) -> dict:
    """Only spent.* may change, each counter finite, non-negative and non-decreasing; everything else identical.

    C9/R5-9: total. Malformed YAML, a non-mapping document, a non-numeric or non-finite value become
    `unexpected-file:budget.yml:<why>` reasons; nothing here raises on the sandboxed process's bytes."""
    reasons = []

    def load(text: str, label: str) -> dict:
        try:
            data = yaml.safe_load(text) or {}
        except Exception:  # noqa: BLE001 - any parse failure is a refusal reason
            reasons.append(f'unexpected-file:{BUDGET_FILE}:{label}-parse-error')
            return {}
        if type(data) is not dict:
            reasons.append(f'unexpected-file:{BUDGET_FILE}:{label}-not-a-mapping')
            return {}
        return data

    before = load(before_text, 'before')
    after = load(after_text, 'after')
    for key in sorted(set(before) | set(after)):
        if key == 'spent':
            continue
        if key not in before or key not in after or before[key] != after[key]:
            reasons.append(f'unexpected-file:{BUDGET_FILE}:{key}')
    spent_before = before.get('spent') if type(before.get('spent')) is dict else {}
    spent_after = after.get('spent') if type(after.get('spent')) is dict else None
    if spent_after is None or set(spent_after) != set(spent_before):
        reasons.append(f'unexpected-file:{BUDGET_FILE}:spent-keys')
        spent_after = spent_after or {}
    for name in sorted(set(spent_before) & set(spent_after)):
        value = spent_after[name]
        if type(value) in (int, float) and not math.isfinite(value):
            reasons.append(f'unexpected-file:{BUDGET_FILE}:spent.{name}:non-finite')
        elif (type(value) not in (int, float) or value < 0 or type(spent_before[name]) not in (int, float)
              or not math.isfinite(spent_before[name]) or value < spent_before[name]):
            reasons.append(f'unexpected-file:{BUDGET_FILE}:spent.{name}')
    return {'reasons': reasons, 'before': dict(spent_before), 'after': dict(spent_after),
            'budget_before': before, 'budget_after': after}


# ---- DD11 K-side effects ----------------------------------------------------

def _listed_paths(base: Path) -> list:
    """Regular files and symlinks under base. Symlinked directories are listed, never descended (C16/R5-15)."""
    found = []
    for root, dirs, files in os.walk(base, followlinks=False):
        found.extend(Path(root) / name for name in files)
        found.extend(Path(root) / name for name in dirs if os.path.islink(os.path.join(root, name)))
    return sorted(found)


def _entry_digest(path: Path) -> str:
    """CS5/R6-5: every entry is lstat-ed first. A regular file is opened without blocking (O_NOFOLLOW|O_NONBLOCK)
    and re-checked with fstat before it is read; a symlink is recorded as link:<target>; anything else is
    special:<octal mode> and is never opened for reading."""
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        return f'link:{os.readlink(path)}'
    if not stat.S_ISREG(mode):
        return f'special:{mode:o}'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return f'special:{mode:o}'
    with os.fdopen(fd, 'rb') as handle:
        current = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(current):
            return f'special:{current:o}'
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read_regular(path) -> bytes:
    """CS5/R6-5: the bytes of a regular file, read without blocking. Any other entry (or a file replaced between the
    check and the open) reads as b'', so a sandboxed FIFO can never hang the harness."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return b''
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return b''
        return handle.read()


def _files_digest(base: Path):
    if not Path(base).is_dir():
        return None
    digest = hashlib.sha256()
    for path in _listed_paths(Path(base)):
        digest.update(str(path.relative_to(base)).encode('utf-8') + b'\0')
        digest.update(_entry_digest(path).encode('utf-8'))
        digest.update(b'\0')
    return digest.hexdigest()


def k_side_listing(project) -> dict:
    root = Path(project.root)
    sf = root / '.scieflow'
    scieflow = (sorted(str(p.relative_to(sf)) for p in sf.rglob('*') if p.is_file())
                if sf.is_dir() else [])
    home = Path(os.environ.get('HOME', ''))
    home_probes = (sorted(p.name for p in [*home.glob('.scieflow-sandbox-probe-*'), *home.glob('.scieflow-verify-marker-*')])
                   if home.is_dir() else [])
    return {'scieflow': scieflow, 'home_probes': home_probes,
            'journals': _files_digest(root / 'config' / 'journals'),
            'pycache': pycache_listing(root)}


def k_side_refusals(before: dict, after: dict) -> list[str]:
    reasons = []
    for rel in sorted(set(after['scieflow']) - set(before['scieflow'])):
        if not ADMISSION_LOCK.fullmatch(rel):
            reasons.append(f'unexpected-file:.scieflow/{rel}')
    reasons += [f'unexpected-file:$HOME/{name}' for name in after['home_probes']]
    if after['journals'] != before['journals']:
        reasons.append('unexpected-file:config/journals')
    reasons += [f'unexpected-file:{rel}' for rel in sorted(set(after['pycache']) - set(before['pycache']))]
    return reasons


# ---- identity, support binding and spies (DD4-DD6) ------------------------

def expected_identity(*, executable: str, session_template: str, resume_template: str,
                      model: str, cwd) -> HarnessIdentity:
    """Exact projection of what resolve_turn captures (plan DD5): no digests, effects unpinned."""
    return HarnessIdentity('claude', 'claude', True, executable, None, None, session_template,
                           resume_template, model, '', str(cwd), None, None, None, False)


def expected_identity_for_fixture(project, *, session_template: str, resume_template: str,
                                  model: str = PINNED_MODEL) -> HarnessIdentity:
    """Offline identity: the claude launcher as production captures it for this project root (R10-2).

    The executable is the REALPATH, the third value of agent_run._capture_execution, exactly as production's
    resolve_turn records it (DD5 original wording; addendum r12 R12-1). The lexical launcher is never projected."""
    root = Path(project.root).resolve(strict=True)
    env = sandbox.environment(project.run_dir(RUN_SLUG))
    located, _facts, target = agent_run._capture_execution('claude', root, env)
    if located is None or target is None:
        raise ProbeRefused('claude executable not capturable')
    return expected_identity(executable=str(target), session_template=session_template,
                             resume_template=resume_template, model=model, cwd=root)


def _identity_dict(fields: dict, *, binary_sha256, agents_sha256, effort_present: bool) -> dict:
    """The gen2 harness identity shape, built from production fields plus real file digests."""
    executable = fields.get('executable')
    exec_sha = sha256_file(Path(executable)) if executable and Path(executable).is_file() else None
    return {'agent': fields.get('agent'), 'family': fields.get('family'), 'executable': executable,
            'executable_sha256': exec_sha, 'binary_sha256': binary_sha256,
            'version': fields.get('version'), 'session_template': fields.get('session_template'),
            'resume_template': fields.get('resume_template'), 'model': fields.get('model'),
            'reasoning': fields.get('reasoning') or '',
            'effort_env': list(PINNED_EFFORT_ENV) if effort_present else None,
            'cwd': fields.get('cwd'), 'agents_sha256': agents_sha256}


def expected_harness_identity(expected: HarnessIdentity, *, binary_sha256, agents_sha256) -> dict:
    return _identity_dict(dataclasses.asdict(expected), binary_sha256=binary_sha256,
                          agents_sha256=agents_sha256, effort_present=True)


@contextlib.contextmanager
def support_bound(expected: HarnessIdentity):
    """Monkeypatch-style binding for ONE identity only; restored in finally (DD5)."""
    if harness_commands.CERTIFIED_POLICIES != ():
        raise ProbeRefused('CERTIFIED_POLICIES must be empty before binding')
    original = harness_commands.support_for

    def support(identity):
        if identity == expected:
            return SupportStatus(True, '', POLICY_REVISION)
        # R12-2: a named refusal. It never falls through to the original support_for, which refuses everything
        # while CERTIFIED_POLICIES is empty. Field NAMES only, never values.
        if not isinstance(identity, HarnessIdentity):
            raise ProbeRefused('binding-identity-mismatch:type')
        fields = sorted(f.name for f in dataclasses.fields(HarnessIdentity)
                        if getattr(identity, f.name) != getattr(expected, f.name))
        raise ProbeRefused('binding-identity-mismatch:' + ','.join(fields))

    harness_commands.support_for = support
    try:
        yield
    finally:
        harness_commands.support_for = original
    if harness_commands.CERTIFIED_POLICIES != ():
        raise ProbeRefused('CERTIFIED_POLICIES changed during binding')


def run_is_unsandboxed(project, slug: str) -> bool:
    return slug in sandbox.unsandboxed_runs(project)


def expected_argv(probe: str, *, session, model: str) -> list[str]:
    """Token list the pinned template must produce for a probe (T3); '{prompt}' is the operand slot (R10-2: no env prefix)."""
    spec = PROBE_SPEC[probe]
    tokens = ['claude', '-p',
              '--output-format=stream-json', '--verbose', '--dangerously-skip-permissions']
    if spec['disable']:
        tokens.append('--disable-slash-commands')
    tokens += [TOOLS_ARG, '--model', model]  # R13-1: after skip-permissions (and disable), before --model
    if spec['resume']:
        if not session:
            raise ValueError('resume probe requires a session id')
        tokens += ['--resume', session]
    if not spec['stdin']:
        tokens.append('{prompt}')
    return tokens


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + '.tmp-k8')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def run_override_ok(project) -> bool:
    """Production strict override validation accepts the written run config (T3d)."""
    ws = project.run_dir(RUN_SLUG)
    data = yaml.safe_load((ws / 'config.yml').read_text(encoding='utf-8')) or {}
    override = agent_run._run_override(data, ws, 'claude', strict=True)
    return bool(override.get('model') == PINNED_MODEL and override.get('session_cmd')
                and override.get('resume_cmd'))


def setup_probe_run(project, *, session_template: str, resume_template: str, timeout_min: float,
                    model: str = PINNED_MODEL) -> Path:
    """First launch only: create the run without any launch, bind claude, write overrides (DD3, DD7)."""
    ws = project.run_dir(RUN_SLUG)
    if ws.exists():
        raise ProbeRefused('run-exists: fresh output required')
    service.create_run(project, RUN_SLUG, 'K Task8 D7 capability probe binding run')
    ws = project.run_dir(RUN_SLUG)
    rev = conversation.selection_revision(conversation.read(ws))
    service.set_conversation_agent(project, RUN_SLUG, 'claude', actor='human', expected_revision=rev)
    cfg_path = ws / 'config.yml'
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) if cfg_path.exists() else None
    cfg = dict(cfg or {})
    cfg['agent_overrides'] = {'claude': {'model': model, 'session_cmd': session_template,
                                         'resume_cmd': resume_template, 'timeout_min': timeout_min}}
    _atomic_write_text(cfg_path, yaml.safe_dump(cfg, sort_keys=True))
    charter.set_text(ws, CHARTER_TEXT)
    return ws


# ---- filesystem listings (T7, DD11) ----------------------------------------

def run_listing(run_dir: Path) -> dict:
    """C16/R5-15, R6-5: a symlink is link:<target> and is never followed; a regular file is hashed without blocking;
    any other entry is special:<octal mode>."""
    run_dir = Path(run_dir)
    return {str(p.relative_to(run_dir)): _entry_digest(p) for p in _listed_paths(run_dir)}


def filesystem_refusals(report: dict) -> list[str]:
    """Any run-dir file not present, identical, in the probe's recorded post-probe listing."""
    current = without_agent_home(run_listing(Path(report['run_dir'])))
    recorded = without_agent_home(report['fs_after'])
    return [f'unexpected-file:{rel}' for rel in sorted(set(current) | set(recorded))
            if current.get(rel) != recorded.get(rel)]


# Files production legitimately writes per turn, derived from real offline fixture turns
# (scratch/k-task8-binding/derived-turn-files.json). budget.yml is checked by budget_effect.
DERIVED_NEW = (re.compile(r'jobs/[0-9A-Z]{26}\.(?:err|json|json\.lock|log)'),
               re.compile(r'logs/turn-[0-9A-Z]{26}\.(?:composed\.md|md|out\.md)'))
DERIVED_CHANGED = frozenset({'conversation.yml', 'events.jsonl'})


AGENT_HOME_PREFIX = 'scratch/agent-home/claude/'  # R10-7: claude's own HOME; evidence, never a refusal


def without_agent_home(listing: dict) -> dict:
    return {p: v for p, v in listing.items() if not p.startswith(AGENT_HOME_PREFIX)}


def agent_home_evidence(run_dir) -> list[dict]:
    """R10-7: names and sizes only (never contents) of files under the agent home."""
    base = Path(run_dir) / 'scratch' / 'agent-home' / 'claude'
    if not base.is_dir():
        return []
    return [{'name': str(p.relative_to(base)), 'size': p.stat().st_size}
            for p in sorted(base.rglob('*')) if p.is_file() and not p.is_symlink()]


def derived_effect_refusals(report: dict) -> list[str]:
    """Anything a turn adds or changes beyond the derived set is unexpected-file (T7, DD11)."""
    before, after = without_agent_home(report['fs_before']), without_agent_home(report['fs_after'])
    new = [p for p in after if p not in before]
    changed = [p for p in after if p in before and after[p] != before[p]]
    reasons = [f'unexpected-file:{p}' for p in sorted(new)
               if not any(rx.fullmatch(p) for rx in DERIVED_NEW)]
    reasons += [f'unexpected-file:{p}' for p in sorted(changed)
                if p not in DERIVED_CHANGED and p != BUDGET_FILE]
    reasons += [f'unexpected-file:{p}:deleted' for p in sorted(p for p in before if p not in after)]
    reasons += [f'unexpected-file:{p}:special' for p in sorted(after) if str(after[p]).startswith('special:')]
    if BUDGET_FILE in changed:
        budget = report.get('budget')
        if not isinstance(budget, dict) or 'reasons' not in budget:
            reasons.append(f'unexpected-file:{BUDGET_FILE}:no-structural-check')
        else:
            reasons += list(budget['reasons'])
    return reasons


# ---- raw capture (T7) -------------------------------------------------------

def parse_stream(raw: bytes) -> list[dict]:
    events = []
    for line in raw.decode('utf-8', errors='replace').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def usage_of(events: list[dict]):
    result = next((e for e in reversed(events) if e.get('type') == 'result'), None)
    if result is None:
        return None
    usage = result.get('usage') or {}
    return {'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
            'cost_usd': result.get('total_cost_usd')}


def interception_of(events: list[dict]) -> dict:
    hits = [{'source': 'stream-json', 'kind': 'skill_invocation', 'name': b['input']['skill']}
            for e in events for b in (e.get('message') or {}).get('content', [])
            if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Skill'
            and isinstance(b.get('input'), dict) and b['input'].get('skill') == ALLOWED_SKILL]
    return {'evidence': hits}


# ---- transcript interception evidence and init tools (R13-2, R13-3) ----------

TRANSCRIPT_MAX_BYTES = 8 * 1024 * 1024
SESSION_ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
COMMAND_NAME_RE = re.compile(r'<command-name>/([^<]+)</command-name>')
EXPANSION_PREFIX = 'Base directory for this skill: '
TRANSCRIPT_CHAIN = ('scratch', 'agent-home', 'claude', '.claude', 'projects')


def init_tools_of(events: list[dict]):
    """R13-2: the `tools` of the first stream-json system init event, or None. Never inferred."""
    init = next((e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'), None)
    return init.get('tools') if init is not None else None


def _real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)  # lstat: a symlink is never a directory here
    except OSError:
        return False


def _projects_dir(run_dir: Path):
    current = Path(run_dir)
    for part in TRANSCRIPT_CHAIN:
        current = current / part
        if not _real_dir(current):
            return None
    return current


def _project_dirs(projects: Path):
    try:
        names = sorted(os.listdir(projects))
    except OSError:
        return
    for name in names:
        if _real_dir(projects / name):
            yield projects / name


def transcript_sizes(run_dir) -> dict:
    """R13-3 offset capture: {relpath: size} for regular, non-symlink *.jsonl files directly under projects/*/.

    Names and sizes only; nothing is read."""
    run_dir = Path(run_dir)
    projects = _projects_dir(run_dir)
    sizes = {}
    if projects is None:
        return sizes
    for proj in _project_dirs(projects):
        try:
            names = os.listdir(proj)
        except OSError:
            continue
        for name in names:
            path = proj / name
            if not name.endswith('.jsonl'):
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                sizes[str(path.relative_to(run_dir))] = st.st_size
    return sizes


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _command_name(record: dict):
    message = record.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    found = COMMAND_NAME_RE.search(content)
    return found.group(1) if found else None


def _expansion_dir(record: dict):
    message = record.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text':
            text = block.get('text')
            if isinstance(text, str) and text.startswith(EXPANSION_PREFIX):
                return text[len(EXPANSION_PREFIX):].split('\n', 1)[0].strip()
    return None


def transcript_interception(run_dir, session_id, started, sizes_before, *, project_root) -> dict:
    """R13-3: CLI-written transcript evidence of the /scieflow-menu slash-command expansion.

    Bounded, non-following, session- and time-bound. The expansion pair (a command record and an isMeta skill
    record with the same promptId, naming the allowed skill dir) is evidence. Only names and counts are kept;
    transcript contents are never recorded."""
    transcript = {'found': False, 'relpath': None, 'size_before': 0, 'size_after': None,
                  'records_considered': 0, 'unexpanded': 0, 'error': None}
    result = {'evidence': [], 'transcript': transcript, 'other_commands': []}
    run_dir = Path(run_dir)
    started_at = _parse_ts(started)
    if started_at is None:
        transcript['error'] = 'no-start-time'
        return result
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        transcript['error'] = 'malformed-session-id'
        return result
    projects = _projects_dir(run_dir)
    if projects is None:
        transcript['error'] = 'chain-not-real-directories'
        return result
    found = []
    for proj in _project_dirs(projects):
        path = proj / f'{session_id}.jsonl'
        try:
            found.append((path, os.lstat(path)))
        except FileNotFoundError:
            continue
        except OSError:
            transcript['error'] = 'unreadable'
            return result
    if not found:
        transcript['error'] = 'no-transcript'
        return result
    if len(found) > 1:
        transcript['error'] = 'ambiguous-candidates'
        return result
    path, st = found[0]
    transcript['found'] = True
    transcript['relpath'] = str(path.relative_to(run_dir))
    if not stat.S_ISREG(st.st_mode):
        transcript['error'] = 'not-regular-file'
        return result
    if st.st_size > TRANSCRIPT_MAX_BYTES:
        transcript['error'] = 'too-large'
        return result
    before = (sizes_before or {}).get(transcript['relpath'], 0)
    transcript['size_before'] = before
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            data = handle.read(TRANSCRIPT_MAX_BYTES + 1)
    except OSError:
        transcript['error'] = 'unreadable'
        return result
    transcript['size_after'] = len(data)
    if len(data) > TRANSCRIPT_MAX_BYTES:
        transcript['error'] = 'too-large'
        return result
    if len(data) < before:
        transcript['error'] = 'truncated'
        return result
    considered = []
    for line in data[before:].decode('utf-8', errors='replace').splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get('type') != 'user' or record.get('sessionId') != session_id:
            continue
        moment = _parse_ts(record.get('timestamp'))
        if moment is None or moment < started_at:
            continue
        considered.append(record)
    transcript['records_considered'] = len(considered)
    allowed_dirs = {str(Path(project_root) / '.claude' / 'skills' / ALLOWED_SKILL),
                    str(Path(project_root) / 'skills' / ALLOWED_SKILL)}
    skills_by_prompt: dict = {}
    for record in considered:
        if record.get('isMeta') is True:
            skill = _expansion_dir(record)
            if skill is not None:
                skills_by_prompt.setdefault(record.get('promptId'), []).append(skill)
    other = set()
    for record in considered:
        name = _command_name(record)
        if name is None:
            continue
        skills = skills_by_prompt.get(record.get('promptId'), [])
        if name == ALLOWED_SKILL:
            matched = [skill for skill in skills if skill in allowed_dirs]
            if matched:
                result['evidence'].append({'source': 'session-transcript', 'kind': 'slash_command_expansion',
                                           'name': ALLOWED_SKILL, 'skill_dir': matched[0]})
            else:
                transcript['unexpanded'] += 1
        else:
            other.add(name)
    for skills in skills_by_prompt.values():
        for skill in skills:
            name = os.path.basename(skill.rstrip('/'))
            if name != ALLOWED_SKILL:
                other.add(name)
    result['other_commands'] = sorted(other)
    return result


# ---- the binding -----------------------------------------------------------

class ProbeBinding:
    def __init__(self, project, *, expected: HarnessIdentity, bind_support: bool = True,
                 origin: str = 'offline-fixture', claude_binary=CLAUDE_BINARY, observe_claude=None):
        self.project = project
        self.expected = expected
        self.bind_support = bind_support
        self.origin = origin
        self.run_dir = project.run_dir(RUN_SLUG)
        self.records: list[dict] = []
        self.claude_binary = Path(claude_binary)
        # Observed claude (realpath, sha256) for the DD4/DD5 pin, read before and after every probe (S5c).
        self.observe_claude = observe_claude or (lambda: observe_claude_file(self.claude_binary))

    def revision(self) -> str:
        return conversation.selection_revision(conversation.read(self.run_dir))

    def reset(self) -> None:
        """Deliberate-recovery fresh session (DD7)."""
        service.set_conversation_agent(self.project, RUN_SLUG, 'claude', actor='human',
                                       expected_revision=self.revision(), reset_session=True)

    def run_probe(self, probe: str, *, truncate_after_s: float | None = None,
                  cancel_after_s: float | None = None) -> dict:
        self.last_seen = {}  # R6-2: a refusal before any launch must not inherit the previous probe's launch state
        spec = PROBE_SPEC[probe]
        if run_is_unsandboxed(self.project, RUN_SLUG):
            raise ProbeRefused('unsandboxed-run: refused before launch')
        if probe == 'P6':
            return self._p6_refusal()
        if spec['resume']:
            # R14-2 (D4-2): a resume probe refuses by name before any launch state is taken.
            doc = conversation.read(self.run_dir)
            if doc.get('recovery_required'):
                raise ProbeRefused('conversation-recovery-required')
            if not doc.get('session'):
                raise ProbeRefused('conversation-no-session')
        if not spec['resume']:
            doc = conversation.read(self.run_dir)
            if doc.get('session') or doc.get('recovery_required') or doc.get('pending_turn_id'):
                self.reset()  # DD7: fresh probes start a new session via the deliberate path
        seen: dict = {}
        self.last_seen = seen  # C9: a failure after a launch is attributed through this dict
        sizes_before = transcript_sizes(self.run_dir)  # R13-3: transcript offset before the turn
        fs_before = run_listing(self.run_dir)
        snap_before = control_snapshot(self.project.root, self.run_dir, binary=self.claude_binary)
        k_before = k_side_listing(self.project)
        budget_before = _read_text(self.run_dir / BUDGET_FILE)
        jobs_before = {j.id for j in jobs.list_jobs(self.project, self.run_dir)}
        with contextlib.ExitStack() as stack:
            if self.bind_support:
                stack.enter_context(support_bound(self.expected))
            stack.enter_context(probe_controls(
                probe, seen, project=self.project,
                truncate_after_s=truncate_after_s or DEFAULT_TRUNCATE_S,
                cancel_after_s=cancel_after_s or DEFAULT_CANCEL_S,
                cancel_enabled=spec['cancel'] and cancel_after_s is not None,
                truncate_enabled=spec['truncate'] and truncate_after_s is not None,
                run_dir=self.run_dir))
            rev = self.revision()
            if spec['marked']:
                message = CANDIDATE
                submission = HumanSubmission(message, 'harness-command', rev)
            else:
                message = 'ordinary chat control'
                submission = HumanSubmission(message, 'chat', None)
            error = None  # N1: the old `'job' not in seen` test was always true (the key is job_id), so a ServiceError
            # always propagated; the dead swallow branch is deleted and behaviour is unchanged.
            try:
                result = service.say(self.project, RUN_SLUG, message, actor='human', human_submission=submission)
            except service.ServiceError as exc:
                if seen.get('launches'):
                    raise  # after a launch, _post_launch_stop keeps the evidence of the probe
                if isinstance(exc.__cause__, ProbeRefused):
                    # This binding's own named refusal, raised inside the production path. Re-raise a FRESH
                    # refusal: `raise exc.__cause__` here would set that object's __context__ to exc, whose
                    # __cause__ is the same object, i.e. a cyclic chain that unbounded walkers never leave
                    # (operator fix 2026-10-22: it OOM-killed every r14 worker run).
                    raise ProbeRefused(str(exc.__cause__)) from exc
                # R14-2 (D4-2): any other refusal before launch is a named refusal, never an exception.
                raise ProbeRefused('service-refused-before-launch') from exc
            state_read = (self._state_read(seen, rev, jobs_before) if spec['cancel'] else None)
        return self._report(probe, seen, fs_before=fs_before, snap_before=snap_before,
                            k_before=k_before, budget_before=budget_before, state_read=state_read,
                            error=error, result=result, sizes_before=sizes_before)

    def launched(self) -> bool:
        return bool(getattr(self, 'last_seen', {}).get('launches'))

    def partial_bytes(self, seen: dict) -> dict:
        """C9/R5-9: the probe's bytes as far as they exist after a launch (stream, stderr, job record)."""
        def read(path) -> bytes:
            return _read_regular(path) if path else b''

        job = jobs.find(self.project, seen['job_id']) if seen.get('job_id') else None
        if job is None:
            return {'stream': b'', 'stderr': b'', 'job_record': b''}
        return {'stream': read(job.log), 'stderr': read(job.err),
                'job_record': read(Path(job.log).with_suffix('.json')) if job.log else b''}

    def _state_read(self, seen: dict, revision_before: str, jobs_before: set) -> dict:
        """P8 state read (DD7): no job in flight, nothing queued, no replay, same agent; the selection revision
        moves exactly once, on finalizing the cancelled turn, and must equal that turn's recorded final_revision
        (operator ruling 2026-10-14, approvals/k-task8-binding-dd7-operator-ruling-20261014.md, option a)."""
        doc = service.conversation_state(self.project, RUN_SLUG)
        run_jobs = jobs.list_jobs(self.project, self.run_dir)
        new_turns = [j for j in run_jobs if j.kind == 'turn' and j.id not in jobs_before]
        cancelled = [t for t in conversation.read(self.run_dir).get('turns', [])
                     if t.get('role') == 'agent' and t.get('state') == 'cancelled'
                     and t.get('job_id') == seen.get('job_id')]
        final_revision = (cancelled[-1].get('audit') or {}).get('final_revision') if cancelled else None
        return {'in_flight': bool(doc['busy']) or any(j.state == 'running' for j in run_jobs),
                'queued': any(j.state == 'queued' for j in run_jobs),
                'replayed': seen.get('launches', 0) > 1 or len(new_turns) > 1,
                'agent': doc['agent'],
                # "before the cancel" is read by the cancel thread just before cancel_job (see _Controls).
                'revision_before': seen.get('revision_before_cancel', revision_before),
                'revision_after': self.revision(), 'final_revision': final_revision,
                'pending_turn_id': doc['pending_turn_id'],
                'recovery_required': bool(doc['recovery_required'])}

    def _p6_refusal(self, constructor=None) -> dict:
        """P6 (S7): the production constructor is called with jobs.start and subprocess.Popen counted.

        Restored in finally. `constructor` is a test seam; the live path always uses HumanSubmission."""
        build = constructor or HumanSubmission
        counts = {'jobs.start': 0, 'Popen': 0}
        real_start = jobs.start
        real_popen = subprocess.Popen

        def counting_start(*args, **kwargs):
            counts['jobs.start'] += 1
            return real_start(*args, **kwargs)

        class CountingPopen(real_popen):
            def __init__(self, *args, **kwargs):
                counts['Popen'] += 1
                super().__init__(*args, **kwargs)

        refusal = {}
        jobs.start = counting_start
        subprocess.Popen = CountingPopen
        try:
            try:
                build('/scieflow-not-a-command', 'harness-command', self.revision())
            except ValueError as exc:
                refusal = {'stage': 'pre-attempt', 'type': type(exc).__name__, 'message': str(exc)}
        finally:
            jobs.start = real_start
            subprocess.Popen = real_popen
        return {'probe': 'P6', 'origin': self.origin, 'run_dir': str(self.run_dir),
                'refusal': refusal, 'live_calls': counts['jobs.start'] + counts['Popen'],
                'job_started': counts['jobs.start'] > 0, 'effect_reasons': [],
                'budget': {'reasons': []}, 'state_read': None, 'job_id': None, 'wall_seconds': 0.0,
                'marked_input': CANDIDATE, 'exit_code': None, 'session_id': None,
                'stream_complete': False, 'interception': {'evidence': []}, 'tool_inputs': [],
                'tools_used': [], 'model_reported': None, 'composed_charter_occurrences': 0,
                'permission_changes': [], 'forks': [], 'background_jobs': [],
                'immutability': {'before': None, 'after': None}, 'job_state': None,
                'auto_resends': 0, 'recovery_required': False, 'stream_text': '', 'stderr_text': '',
                'harness_identity': None, 'launch': None}

    def _report(self, probe, seen, *, fs_before, snap_before, k_before, budget_before, state_read,
                error, result, sizes_before=None) -> dict:
        ws = self.run_dir
        spec = PROBE_SPEC[probe]
        job_id = seen.get('job_id')
        job = jobs.find(self.project, job_id) if job_id else None
        raw = _read_regular(job.log) if job and job.log else b''
        err = _read_regular(job.err) if job and job.err else b''
        events = parse_stream(raw)
        composed = b''
        composed_path = None
        audit = (job.turn_audit or {}) if job else {}
        if audit.get('composed_artifact'):
            path = ws / audit['composed_artifact']
            composed_path = str(path)
            composed = _read_regular(path)
        job_record = None
        if job and job.log and Path(job.log).with_suffix('.json').is_file():
            job_record = _read_regular(Path(job.log).with_suffix('.json'))
        doc = conversation.read(ws)
        snap_after = control_snapshot(self.project.root, ws, binary=self.claude_binary)
        budget = budget_effect(budget_before or '', _read_text(ws / BUDGET_FILE) or '')
        k_after = k_side_listing(self.project)
        blocks = [b for e in events for b in (e.get('message') or {}).get('content', [])
                  if isinstance(b, dict) and b.get('type') == 'tool_use']
        protection = seen.get('protection')
        report = {
            'probe': probe, 'origin': self.origin, 'run_dir': str(ws),
            'marked_input': CANDIDATE if spec['marked'] else 'ordinary chat control',
            'identity_expected': dataclasses.asdict(self.expected),
            'harness_identity': seen.get('identity'), 'policy_revision': seen.get('policy_revision'),
            'launch': seen.get('launch'), 'protection': protection, 'job_id': job_id,
            'job_state': job.state if job else None, 'exit_code': job.exit_code if job else None,
            'wall_seconds': job.duration_s if job and job.duration_s is not None else 0.0,
            'recovery_required': bool(doc.get('recovery_required')),
            # R14-1 (D4-1): the job's turn audit, reduced to parse_complete and reason; None without a job or audit.
            'turn_audit': _turn_audit(job),
            'auto_resends': max(0, seen.get('launches', 0) - 1),
            'error': error, 'result_present': result is not None,
            'session_id': next((e.get('session_id') for e in events if e.get('session_id')), None),
            'stream_complete': any(e.get('type') == 'result' for e in events),
            'composed_path': composed_path,
            'composed_sha256': hashlib.sha256(composed).hexdigest() if composed else None,
            'composed_charter_occurrences': composed.decode('utf-8', 'replace').count(CHARTER_TEXT),
            'raw_stream': {'path': str(job.log) if job else None,
                           'sha256': hashlib.sha256(raw).hexdigest()},
            'stream_text': raw.decode('utf-8', 'replace'), 'stderr_text': err.decode('utf-8', 'replace'),
            'usage': usage_of(events), 'interception': interception_of(events),
            'model_reported': next((e.get('model') for e in events if e.get('type') == 'system'), None),
            'tools_used': [b.get('name') for b in blocks],
            'tool_inputs': [{'name': b.get('name'), 'input': b.get('input') or {}} for b in blocks],
            'forks': [e for e in events if 'background' in str(e.get('subtype', ''))
                      or 'fork' in str(e.get('subtype', ''))],
            'background_jobs': [],
            'immutability': {'before': snap_before, 'after': snap_after},
            'permission_changes': compare_snapshots(snap_before, snap_after),
            'budget': budget,
            'k_side_before': k_before, 'k_side_after': k_after,
            'fs_before': fs_before, 'fs_after': run_listing(ws), 'run_dir_recorded': str(ws),
            'state_read': state_read,
            'discovery': discovery_inputs(self.project.root, str(sandbox.agent_home_path(Path(self.run_dir), 'claude'))),
            'agent_home_evidence': agent_home_evidence(ws),
            # Bytes for persisted evidence (S2); removed from the entry before it is stored.
            '_bytes': {'stream': raw, 'stderr': err,
                       'composed': composed if composed_path else None, 'job_record': job_record},
        }
        # R13-2/R13-3: the CLI's own init tools, and transcript expansion evidence beside the stream evidence.
        init_tools = init_tools_of(events)
        transcript = transcript_interception(ws, report['session_id'], getattr(job, 'started', None) if job else None,
                                             sizes_before or {}, project_root=self.project.root)
        report['interception'] = {'evidence': report['interception']['evidence'] + transcript['evidence']}
        report['transcript'] = transcript['transcript']
        report['other_commands'] = transcript['other_commands']
        report['init_tools'] = init_tools
        report['k_side_refusals'] = k_side_refusals(k_before, k_after)
        if protection is None:
            sandbox_reasons = ['launch-unrecorded']
        else:
            sandbox_reasons = launch_refusals(seen.get('launch') or {}, protection.get('writable') or [])
        report['effect_reasons'] = (report['permission_changes'] + derived_effect_refusals(report)
                                    + report['k_side_refusals']
                                    + [f'unexpected-permission:sandbox:{item}' for item in sandbox_reasons])
        return report


def probe_controls(probe: str, seen: dict, *, project, truncate_after_s: float,
                   cancel_after_s: float, cancel_enabled: bool, truncate_enabled: bool, run_dir=None):
    """In-memory wrappers for resolve_turn / prepare_turn / jobs.start (DD6), restored in finally."""
    spec = PROBE_SPEC[probe]
    return _Controls(probe, spec, seen, project, truncate_after_s, cancel_after_s,
                     cancel_enabled, truncate_enabled,
                     run_dir if run_dir is not None else project.run_dir(RUN_SLUG))


class _Controls(contextlib.AbstractContextManager):
    def __init__(self, probe, spec, seen, project, truncate_after_s, cancel_after_s,
                 cancel_enabled, truncate_enabled, run_dir):
        self.probe, self.spec, self.seen, self.project = probe, spec, seen, project
        self.truncate_after_s, self.cancel_after_s = truncate_after_s, cancel_after_s
        self.cancel_enabled, self.truncate_enabled = cancel_enabled, truncate_enabled
        self.run_dir = Path(run_dir)
        self._saved = None

    def __enter__(self):
        self._saved = (agent_run.resolve_turn, agent_run.prepare_turn, jobs.start)
        resolve_orig, prepare_orig, start_orig = self._saved
        seen, spec, probe = self.seen, self.spec, self.probe

        def resolve(project, agent, **kwargs):
            # R10-2: forward every keyword unchanged (service._say_prepared passes create_home=True).
            resolved = resolve_orig(project, agent, **kwargs)
            seen['identity'] = dataclasses.asdict(resolved.harness_identity)
            if self.truncate_enabled:
                resolved = dataclasses.replace(resolved, timeout_s=self.truncate_after_s)
            return resolved

        def prepare(resolved, submission):
            if spec['stdin']:
                resolved = dataclasses.replace(resolved, prompt_argv_limit=1)
            prepared = prepare_orig(resolved, submission)
            seen['policy_revision'] = prepared.policy_revision
            if spec['disable']:
                argv = list(prepared.argv)
                index = argv.index('--dangerously-skip-permissions') + 1
                argv.insert(index, '--disable-slash-commands')
                prepared = dataclasses.replace(prepared, argv=tuple(argv))
            return prepared

        def verify_spy(writable, cwd, *args, **kwargs):
            # R10-6 observation: the production verify for this turn, passed through unchanged.
            self.verified_layouts.append(kwargs.get('layout'))
            return self._verify_orig(writable, cwd, *args, **kwargs)

        def start(project, argv, **kwargs):
            # C2/R5-3: refuse any launch env key outside the allowlist before the process starts.
            if seen.get('launches', 0):
                raise ProbeRefused('second-launch')  # C12/R5-13: one production launch per probe
            if kwargs.get('env') is None:  # R6-4/CS4: an inherited environment is never launched
                raise ProbeRefused('unexpected-permission:env:inherited')
            env_in = dict(kwargs['env'])
            if PINNED_EFFORT_ENV[0] in env_in:  # R10-4: the binding is the only source of the effort key
                raise ProbeRefused('unexpected-permission:env:effort-preset')
            if CREDENTIAL_VAR not in env_in:  # R10-4: the credential var must be present (env mode)
                raise ProbeRefused('unexpected-permission:env:credential-missing')
            if env_in.get('HOME') != str(sandbox.agent_home_path(self.run_dir, 'claude')):  # R10-4
                raise ProbeRefused('unexpected-permission:env:home')
            env_keys = sorted(env_in.keys())  # C2/R5-3: the names only, never values
            bad_env = env_refusal_keys(env_keys)
            if bad_env:
                raise ProbeRefused('unexpected-permission:env:' + bad_env[0])
            token_bad = token_file_refusals(sandbox.credentials_dir())  # R10-5: metadata only, never opened
            if token_bad:
                raise ProbeRefused('unexpected-permission:credential:' + token_bad[0])
            # S4: confinement is asserted before the launch call, from a fresh protection capture.
            protection = sandbox.resolve_turn_protection(project, run_dir=self.run_dir)
            # C5/R5-8: the kwargs production hands to jobs.start must equal the fresh protection, before launch.
            launch_writable = kwargs.get('sandbox_writable')
            if (launch_writable is None or list(launch_writable) != list(protection.writable)
                    or list(kwargs.get('sandbox_mask') or []) != list(protection.masks)):
                raise ProbeRefused('unexpected-permission:sandbox:kwargs')
            pre = confinement_refusals(protection, self.run_dir)
            if pre:
                raise ProbeRefused('confinement-before-launch: ' + ','.join(pre))
            pre = layout_refusals(project, kwargs.get('sandbox_layout'), protection, self.run_dir,
                                  self.verified_layouts)  # R10-6
            if pre:
                raise ProbeRefused('confinement-before-launch: ' + ','.join(pre))
            seen['protection'] = protection_envelope(protection)
            seen['launches'] = seen.get('launches', 0) + 1
            # R10-3: the effort key goes into a COPY of production's env, after the gate; production's dict is untouched.
            env_out = dict(env_in)
            env_out[PINNED_EFFORT_ENV[0]] = PINNED_EFFORT_ENV[1]
            kwargs_out = dict(kwargs, env=env_out)
            prefix: list = []
            real_wrap = sandbox.wrap

            def wrap_spy(argv_in, **wrap_kwargs):
                # R10-6 observation only: record the bwrap prefix (before '--'); the argv is never altered.
                wrapped = real_wrap(argv_in, **wrap_kwargs)
                cut = list(wrapped).index('--') if '--' in wrapped else len(wrapped)
                prefix[:] = list(wrapped[:cut])
                return wrapped

            sandbox.wrap = wrap_spy
            try:
                job, proc = start_orig(project, argv, **kwargs_out)
            finally:
                sandbox.wrap = real_wrap
            seen['job_id'] = job.id
            stdin_text = kwargs.get('stdin_text')
            seen['launch'] = {
                'argv': list(argv),
                'env_keys': sorted(env_out.keys()),  # C2: names only, never values
                'stdin_used': stdin_text is not None,
                'stdin_sha256': hashlib.sha256(stdin_text.encode('utf-8')).hexdigest()
                if stdin_text is not None else None,
                'env_has_effort': env_out.get(PINNED_EFFORT_ENV[0]) == PINNED_EFFORT_ENV[1],
                # R10-3: the pinned effort travels in the launch env, never in argv.
                'effort_in_argv': f'{PINNED_EFFORT_ENV[0]}={PINNED_EFFORT_ENV[1]}' in list(argv),
                'sandboxed': bool(job.sandboxed),
                'writable': [str(p) for p in kwargs.get('sandbox_writable') or []],
                'sandbox_prefix': list(prefix),  # R10-6 observation: flags and paths only
                'prefix_refusals': prefix_refusals(prefix, kwargs.get('sandbox_layout'), protection, self.run_dir),
            }
            if self.cancel_enabled:
                stop = threading.Event()

                def cancel():
                    if stop.wait(self.cancel_after_s):
                        return  # the probe ended before its stop point: nothing to cancel
                    # The selection generation moves when the session id is recorded, so "before the
                    # cancel" is read here, immediately before cancel_job, not at launch.
                    seen['revision_before_cancel'] = conversation.selection_revision(
                        conversation.read(self.run_dir))
                    service.cancel_job(self.project, job.id)
                thread = threading.Thread(target=cancel, name='k8-cancel', daemon=True)
                self._cancel = (stop, thread)
                CANCEL_THREADS.append(thread)
                thread.start()
            return job, proc

        self.verified_layouts = []
        self._verify_orig = sandbox.verify
        sandbox.verify = verify_spy
        agent_run.resolve_turn = resolve
        agent_run.prepare_turn = prepare
        jobs.start = start
        return self

    def __exit__(self, *exc):
        agent_run.resolve_turn, agent_run.prepare_turn, jobs.start = self._saved
        sandbox.verify = self._verify_orig
        if getattr(self, '_cancel', None):  # C15/R5-15: the cancel thread ends with the probe, never outlives it
            stop, thread = self._cancel
            stop.set()
            thread.join(timeout=30)
        return False


# ---- confinement, launch envelope, discovery and evidence (r4: S2, S4, S8) ---------

def protection_envelope(protection) -> dict:
    return {'enabled': protection.sandbox_enabled,
            'writable': [str(p) for p in (protection.writable or ())],
            'masks': [str(p) for p in protection.masks],
            'allowlist': [str(p) for p in protection.allowlist],
            'policy_sha256': protection.policy_sha256}


def confinement_refusals(protection, run_dir) -> list[str]:
    """S4: the protection must enable the sandbox and grant exactly (run_dir, *allowlist) as writable."""
    if protection.sandbox_enabled is not True:
        return ['sandbox-disabled']
    expected = (Path(run_dir).resolve(), *protection.allowlist)
    if tuple(protection.writable or ()) != tuple(expected):
        return ['writable-mismatch']
    return []


def launch_refusals(launch: dict, expected_writable) -> list[str]:
    """S4 after the launch: the launch must report sandboxed=True and the same writable list."""
    reasons = []
    if launch.get('sandboxed') is not True:
        reasons.append('launch-unsandboxed')
    if [str(p) for p in launch.get('writable') or []] != [str(p) for p in expected_writable]:
        reasons.append('launch-writable-mismatch')
    reasons.extend(launch.get('prefix_refusals') or [])  # R10-6 post-launch shape refusals
    return reasons


def token_file_refusals(cred_dir) -> list[str]:
    """R10-5: metadata only (os.lstat). The token file is never opened, read, copied or hashed."""
    try:
        st = os.lstat(Path(cred_dir) / TOKEN_FILE_NAME)
    except OSError:
        return ['token-file-missing']
    reasons = []
    if not stat.S_ISREG(st.st_mode):
        reasons.append('token-file-not-regular')
    if stat.S_IMODE(st.st_mode) != 0o600:
        reasons.append('token-file-mode')
    if st.st_uid != os.getuid():
        reasons.append('token-file-owner')
    if not 1 <= st.st_size <= TOKEN_MAX_BYTES:
        reasons.append('token-file-size')
    return reasons


def layout_refusals(project, layout, protection, run_dir, verified) -> list[str]:
    """R10-6 pre-launch: a Layout, verified as the same object, equal to the fresh read-only dispatch layout."""
    if not isinstance(layout, sandbox.Layout):
        return ['layout-missing']
    if not any(layout is v for v in verified):
        return ['verify-not-run']
    root = Path(project.root).resolve()
    launcher = sandbox.host_launcher('claude', root)
    fresh = sandbox.dispatch_layout(project, run_dir=Path(run_dir), agent='claude',
                                    readable=protection.readable, launcher=launcher)
    reasons = []
    if layout != fresh:
        reasons.append('layout-mismatch')
    if layout.home != sandbox.agent_home_path(Path(run_dir), 'claude'):
        reasons.append('layout-home')
    if layout.binds != ():
        reasons.append('layout-binds')
    if launcher is None:
        reasons.append('launcher-missing')
    else:
        real = Path(launcher).resolve()
        installs = [Path(i).resolve() for i in layout.installs]
        if not any(real == i or i in real.parents for i in installs):
            reasons.append('launcher-not-in-installs')
    return reasons


def prefix_refusals(prefix, layout, protection, run_dir) -> list[str]:
    """R10-6 post-launch: no root bind, a tmpfs /run, and only allow-listed bind sources (never hard-deny paths)."""
    if not isinstance(layout, sandbox.Layout) or not prefix:
        return ['prefix-missing']
    reasons = []
    if any(prefix[i:i + 3] == ['--ro-bind', '/', '/'] for i in range(len(prefix) - 2)):
        reasons.append('ro-bind-root')
    if ('--tmpfs', '/run') not in list(zip(prefix, prefix[1:])):
        reasons.append('tmpfs-run-absent')
    home = os.path.expanduser('~')
    hard = [str(Path(home) / rel) for rel in HARD_DENY_RELATIVE]
    allowed = set(SYSTEM_SOURCES) | {str(Path('/etc/resolv.conf').resolve())}
    allowed |= {str(p) for p in layout.readable} | {str(p) for p in layout.installs}
    allowed |= {str(p) for p in (protection.writable or ())} | {str(layout.home)}
    scratch = str(Path(run_dir).resolve() / 'scratch')
    for i, flag in enumerate(prefix):
        if flag in BWRAP_SOURCE_FLAGS and i + 2 < len(prefix):
            src = prefix[i + 1]
            if src == home or any(src == h or src.startswith(h + '/') for h in hard):
                reasons.append('hard-deny-source')
            elif src in allowed or src == scratch or src.startswith(scratch + '/'):
                continue
            else:
                reasons.append('source-not-allowed')
    return sorted(set(reasons))


def observe_claude_file(path) -> tuple:
    path = Path(path)
    if not path.is_file():
        return (None, None)
    return (str(path.resolve()), sha256_file(path))


def observe_claude_live(project) -> tuple:
    """DD4 live resolution: claude located by production _capture_execution (read-only; never executed)."""
    env = sandbox.environment(project.run_dir(RUN_SLUG))
    located, _facts, target = agent_run._capture_execution('claude', Path(project.root).resolve(), env)
    if located is None or target is None:
        return (None, None)
    return (target, sha256_file(Path(target)))


def discovery_inputs(k_root, home) -> dict:
    """S8: evidence only, never a gate: presence and digest of the files a claude run may discover."""
    root, home_dir = Path(k_root), Path(home)

    def digest_or_none(path: Path):
        return sha256_file(path) if path.is_file() and not path.is_symlink() else None  # r10b F7: no symlink hashed

    settings_dir = home_dir / '.claude'
    settings = ({p.name: sha256_file(p) for p in sorted(settings_dir.glob('settings*.json'))
                 if p.is_file() and not p.is_symlink()}  # r10b F7
                if settings_dir.is_dir() else {})
    return {'CLAUDE.md': digest_or_none(root / 'CLAUDE.md'), 'AGENTS.md': digest_or_none(root / 'AGENTS.md'),
            '.mcp.json': digest_or_none(root / '.mcp.json'), 'home-claude-settings': settings}


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, default=str) + '\n').encode('utf-8')


def _write_new(path: Path, data: bytes) -> None:
    """C15/C16/R5-15: create a new file only; O_EXCL refuses an existing name and O_NOFOLLOW refuses a symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)


def write_probe_evidence(evidence_dir, raw) -> dict:
    """S2: persist one probe's evidence into a fresh directory; returns relative names and sha256 only."""
    target = Path(evidence_dir)
    if target.exists():
        raise ProbeRefused(f'evidence-dir-exists: {target.name}')
    target.mkdir(parents=True)
    blobs = raw['_bytes']
    files = {}

    def put(name: str, data: bytes) -> None:
        _write_new(target / name, data)
        files[name] = hashlib.sha256(data).hexdigest()

    put('stream.jsonl', blobs['stream'])
    put('stderr.txt', blobs['stderr'])
    if blobs['composed'] is not None:
        put('composed.md', blobs['composed'])
    if blobs['job_record'] is not None:
        put('job.json', blobs['job_record'])
    budget = raw.get('budget') or {}
    put('usage.json', _json_bytes({'usage': raw.get('usage'),
                                   'budget': {'before': budget.get('budget_before'),
                                              'after': budget.get('budget_after')},
                                   'charter_occurrences': raw.get('composed_charter_occurrences')}))
    put('launch.json', _json_bytes({'launch': raw.get('launch'), 'protection': raw.get('protection')}))
    put('fs-before.json', _json_bytes(raw.get('fs_before')))
    put('fs-after.json', _json_bytes(raw.get('fs_after')))
    put('discovery.json', _json_bytes(raw.get('discovery')))
    return {'dir': target.name, 'files': files, 'usage': raw.get('usage')}


def _probe_record(report: dict, raw: dict) -> dict:
    """SR2/R5-14: the harness entry plus the decision fields of one probe; never raw bytes."""
    record = {key: value for key, value in report.items() if not str(key).startswith('_bytes')}
    record.update(state_read=raw.get('state_read'), immutability=raw.get('immutability'),
                  effect_reasons=list(raw.get('effect_reasons') or []), k_side_before=raw.get('k_side_before'),
                  k_side_after=raw.get('k_side_after'), refusal=raw.get('refusal'))
    return record


def write_probe_report(evidence_dir, record: dict) -> str:
    """SR2/R5-14: <evidence>/report.json, created exclusively; returns its sha256."""
    target = Path(evidence_dir)
    target.mkdir(parents=True, exist_ok=True)
    data = _json_bytes(record)
    _write_new(target / 'report.json', data)
    return hashlib.sha256(data).hexdigest()


# ---- report adapter (impl checkpoint step 1) -------------------------------

def _turn_audit(job):
    """R14-1 (D4-1): the job's turn audit, reduced to the two keys the harness reads (parse_complete, reason).

    None when there is no job or the job recorded no audit; production records an audit for every marked turn."""
    audit = getattr(job, 'turn_audit', None) if job is not None else None
    if not audit:
        return None
    return {'parse_complete': audit.get('parse_complete'), 'reason': audit.get('reason')}


def to_harness_report(raw: dict, *, agents_sha256) -> dict:
    """Map one binding report to the gen2 harness report shape (validate_report input).

    The binary digest is the one observed in this probe's own snapshot (S5), never a caller value."""
    observed = raw.get('harness_identity')
    identity = None
    if observed:
        effort_present = bool((raw.get('launch') or {}).get('env_has_effort'))  # R10-3
        binary = ((raw.get('immutability') or {}).get('before') or {}).get('binary')
        identity = _identity_dict(observed, binary_sha256=binary, agents_sha256=agents_sha256,
                                  effort_present=effort_present)
    return {
        'probe': raw['probe'], 'origin': raw['origin'], 'identity': identity,
        'marked_input': raw['marked_input'], 'exit_code': raw['exit_code'],
        'wall_seconds': raw['wall_seconds'], 'session_id': raw['session_id'],
        'stream_complete': raw['stream_complete'], 'interception': raw['interception'],
        'init_tools': raw['init_tools'], 'other_commands': raw['other_commands'],
        'tools_used': raw['tools_used'], 'tool_inputs': raw['tool_inputs'],
        'model_reported': raw['model_reported'],
        'charter': {'sha256': hashlib.sha256(CHARTER_TEXT.encode('utf-8')).hexdigest(),
                    'occurrences': raw['composed_charter_occurrences']},
        'permission_changes': raw['permission_changes'], 'forks': raw['forks'],
        'background_jobs': raw['background_jobs'], 'immutability': raw['immutability'],
        'refusal': None, 'job_state': raw['job_state'], 'auto_resends': raw['auto_resends'],
        'recovery_required': raw['recovery_required'], 'state_read': raw['state_read'],
        'turn_audit': raw.get('turn_audit'),
    }


# ---- sequence driver, checkpoint and resume (DD7-DD9; r4 S1, S6, S9, S11) ----------

def _stop(entries: list, ceiling, status: str) -> dict:
    return {'status': status, 'matrix_complete': _matrix_complete(entries), 'reports': entries,
            'ceiling': {'invocations': ceiling.invocations, 'completing': ceiling.completing,
                        'active_s': round(ceiling.active_s, 3)}}


def _matrix_complete(entries: list) -> bool:
    """R4-5: every probe attempted and usable, with no not-run entry."""
    by = {e.get('probe'): e for e in entries}
    return all(by.get(p, {}).get('usable') is True
               and not str(by.get(p, {}).get('status', '')).startswith('not-run') for p in PROBE_ORDER)


def _p6_entry(raw: dict, gate, expected) -> dict:
    entry = {'probe': 'P6', 'origin': raw['origin'], 'invoked': False, 'completing': False,
             'refusal': raw['refusal'], 'live_calls': raw['live_calls'],
             'job_started': raw['job_started'], 'status': 'usable'}
    reasons = gate.validate_report(entry, expected, 'P6')
    entry.update(usable=not reasons, reasons=reasons, status='usable' if not reasons else 'refused')
    return entry


def turn_job_count(project, run_dir) -> int:  # the live caller passes this as turn_jobs
    """Turn jobs recorded in the run (C10): a checkpoint may not understate the invocations already made."""
    return sum(1 for job in jobs.list_jobs(project, run_dir) if job.kind == 'turn')


def _running_jobs(project, run_dir) -> bool:
    return any(j.state == 'running' for j in jobs.list_jobs(project, run_dir))


def run_sequence(binding, gate, *, mode_for, agents_sha256, claude_pin, truncate_after_s=None,
                 cancel_after_s=None, checkpoint_path=None, reports=None, ceiling_state=None,
                 evidence_root=None, clock=time.monotonic, now=time.time, progress=None) -> dict:
    """Run P1-P8 in DD7 order through the real binding, validated by the gen2 harness `gate`.

    `claude_pin` is (realpath, sha256) of the pinned claude; it is checked before and after every probe.
    The active-time clock wraps each whole probe call, including verification, evidence and resets (S6).
    Status is 'attempted-all' when every probe was attempted (R4-5); matrix_complete says whether all usable.
    A usage-limited probe ends the sequence with a checkpoint and is never re-invoked (DD9).

    R6-2: `progress` is the caller's shared object. Its one live Ceiling and its entries list are updated in place,
    so the caller's outer handler reports the same accounting. Without a caller ceiling one is made from
    `ceiling_state`. A preflight the caller recorded is carried into the checkpoint (N3).
    """
    progress = progress if progress is not None else {}
    expected = expected_harness_identity(binding.expected, binary_sha256=claude_pin[1], agents_sha256=agents_sha256)
    entries = [dict(e) for e in (reports or [])]
    progress['entries'] = entries
    done = {e['probe'] for e in entries}
    usable = {e['probe'] for e in entries if e.get('usable')}
    ceiling = progress.get('ceiling')
    if ceiling is None:
        ceiling = gate.Ceiling()
        if ceiling_state:
            ceiling.invocations = int(ceiling_state['invocations'])
            ceiling.completing = int(ceiling_state['completing'])
            ceiling.active_s = float(ceiling_state['active_s'])
        progress['ceiling'] = ceiling
    preflight = progress.get('preflight')
    for probe in PROBE_ORDER:
        if probe in done:
            continue
        if probe == 'P6':
            entries.append(_p6_entry(binding.run_probe('P6'), gate, expected))
            if evidence_root is not None:
                write_probe_report(Path(evidence_root) / 'P6', entries[-1])
            if entries[-1]['usable']:
                usable.add('P6')
                continue
            return _stop(entries, ceiling, f"refused:{entries[-1]['reasons'][0]}")
        if (probe == 'P3' and 'P1' not in usable) or (probe == 'P4' and 'P3' not in usable):
            entries.append({'probe': probe, 'status': 'not-run-dependency', 'invoked': False,
                            'completing': False, 'usable': False})
            continue
        try:
            ceiling.check(probe)
        except gate.CeilingRefusal as exc:
            return _stop(entries, ceiling, f'refused:{exc}')
        if tuple(binding.observe_claude()) != tuple(claude_pin):
            return _stop(entries, ceiling, 'refused:identity-drift:claude')
        mode_for(probe)
        started = clock()
        recorded = False
        reasons = []
        try:
            raw = binding.run_probe(probe, truncate_after_s=truncate_after_s, cancel_after_s=cancel_after_s)
            evidence = None
            if evidence_root is not None:
                evidence = write_probe_evidence(Path(evidence_root) / probe, raw)
            if tuple(binding.observe_claude()) != tuple(claude_pin):
                raw['effect_reasons'] = list(raw['effect_reasons']) + ['unexpected-permission:claude-identity']
            ceiling.record(probe, clock() - started)
            recorded = True
            report = to_harness_report(raw, agents_sha256=agents_sha256)
            report.update(probe=probe, invoked=True, completing=gate.PROBE_SPEC[probe]['completing'],
                          job_id=raw['job_id'], budget=raw['budget'], k_side_refusals=raw['k_side_refusals'],
                          effect_reasons=raw['effect_reasons'], evidence=evidence)
            limit = gate.classify_usage_limit(raw['stream_text'], raw['stderr_text'])
            if limit['limited']:
                evidence_line = next((line for line in f"{raw['stream_text']}\n{raw['stderr_text']}".splitlines()
                                      if limit['pattern'] and limit['pattern'] in line.lower()), None)
                report.update(status='not-run-usage-limited', usable=False, usage_limit=limit)
                if evidence_root is not None:
                    write_probe_report(Path(evidence_root) / probe, _probe_record(report, raw))
                entries.append(report)
                try:
                    return _pause(binding, entries, ceiling, probe, limit, evidence_line, checkpoint_path, gate, now,
                                  preflight, progress=progress)
                except Exception as exc:  # noqa: BLE001 - R6-2: a pause that cannot be checkpointed is a stop, not 76
                    return _post_launch_stop(binding, entries, ceiling, probe, started, exc, evidence_root, clock,
                                             recorded=True, status='stopped',
                                             reason=f'checkpoint-write-failed:{type(exc).__name__}')
            reasons = list(raw['effect_reasons']) + gate.validate_report(report, expected, probe)
            report.update(usable=not reasons, status='usable' if not reasons else 'refused', reasons=reasons)
            if evidence_root is not None:
                write_probe_report(Path(evidence_root) / probe, _probe_record(report, raw))
            entries.append(report)
        except ProbeRefused as exc:
            if not binding.launched():
                return _stop(entries, ceiling, f'refused:{exc}')
            return _post_launch_stop(binding, entries, ceiling, probe, started, exc, evidence_root, clock,
                                     recorded=recorded)
        except Exception as exc:  # noqa: BLE001 - C9/R6-2: once a launch happened, every failure keeps its evidence
            if not binding.launched():
                raise
            return _post_launch_stop(binding, entries, ceiling, probe, started, exc, evidence_root, clock,
                                     recorded=recorded)
        if reasons:
            return _stop(entries, ceiling, f'refused:{reasons[0]}')
        usable.add(probe)
    return _stop(entries, ceiling, 'attempted-all')


def _config_digest(run_dir):
    """R6-3: the config digest through the non-blocking reader, so a FIFO written into the run dir cannot hang a pause."""
    path = Path(run_dir) / 'config.yml'
    return _entry_digest(path) if path.is_file() else None


def _post_launch_stop(binding, entries, ceiling, probe, started, exc, evidence_root, clock, *, recorded=False,
                      status='stopped-after-launch', reason=None) -> dict:
    """C9/R5-9/R6-2: a failure after a launch keeps the probe's bytes, counts the invocation once with its elapsed
    time, writes <out>/stopped.json and stops the matrix. Idempotent: the ceiling is recorded only when the caller has
    not recorded this probe, and an existing evidence directory, file or stopped.json is kept, never replaced."""
    if not recorded:
        ceiling.record(probe, clock() - started)
    if reason is None:
        reason = f'refused:{exc}' if isinstance(exc, ProbeRefused) else exception_chain_reason(exc)
    out = _stop(entries, ceiling, status)
    out['reasons'] = [reason]
    if evidence_root is not None:
        partial = binding.partial_bytes(binding.last_seen)
        root = Path(evidence_root)
        target = root / probe
        root.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        for name, key in (('stream.jsonl', 'stream'), ('stderr.txt', 'stderr'), ('job.json', 'job_record')):
            if not (target / name).exists():
                _write_new(target / name, partial[key])
        if not (root / 'stopped.json').exists():
            _write_new(root / 'stopped.json', _json_bytes({
                'probe': probe, 'reason': reason, 'ceiling': out['ceiling'],
                'elapsed_s': round(clock() - started, 3)}))
    return out


def _pause(binding, entries, ceiling, probe, limit, evidence, checkpoint_path, gate, now, preflight=None,
           progress=None) -> dict:
    doc = conversation.read(binding.run_dir)
    data = {'attempted': [e['probe'] for e in entries], 'reports': entries,
            'ceiling': {'invocations': ceiling.invocations, 'completing': ceiling.completing,
                        'active_s': round(ceiling.active_s, 3)},
            'limited_probe': probe, 'reset_evidence': evidence, 'reset_epoch': limit['reset_epoch'],
            'revision': binding.revision(), 'session': doc['session'],
            'config_sha256': _config_digest(binding.run_dir),
            'preflight': preflight,  # R6-3/N3: the preflight is carried, so a resumed segment never repeats it
            'job_ids': [e['job_id'] for e in entries if e.get('job_id')],
            # S9: both digests are computed here from the files, never taken from a caller.
            'dd1_sha256': gate.dd1_receipt_digests(), 'gen2_sha256': gate.gen2_digest(),
            'paused_at': now()}
    checkpoint_sha = None
    if checkpoint_path is not None:
        checkpoint_sha = gate.write_checkpoint(Path(checkpoint_path), data)
        if progress is not None:  # R7-4/CR4: the published checkpoint is recorded at once, for every exit path
            progress.update(checkpoint_path=str(checkpoint_path), checkpoint_sha256=checkpoint_sha)
    out = _stop(entries, ceiling, 'paused-usage-limit')
    out.update(limited_probe=probe, checkpoint_sha256=checkpoint_sha)
    return out


def resume_preflight(binding, gate, checkpoint_path, *, receipt_checkpoint_sha256, turn_jobs=None) -> list[str]:
    """R7-3/CR3: the pure validation resume_sequence applies, run by the caller BEFORE the single-use record is
    written. It writes nothing and runs nothing. resume_sequence repeats the same check (the race window)."""
    doc = conversation.read(binding.run_dir)
    return gate.resume_refusals(
        Path(checkpoint_path), config_sha256=_config_digest(binding.run_dir),
        revision=binding.revision(), session=doc['session'],
        job_running=_running_jobs(binding.project, binding.run_dir),
        receipt_checkpoint_sha256=receipt_checkpoint_sha256,
        turn_jobs=turn_jobs)


def resume_sequence(binding, gate, *, checkpoint_path, receipt_checkpoint_sha256, mode_for, agents_sha256,
                    claude_pin, truncate_after_s=None, cancel_after_s=None, evidence_root=None,
                    clock=time.monotonic, now=time.time, turn_jobs=None, progress=None,
                    next_checkpoint_path=None) -> dict:
    """Resume after a usage-limit pause only when the checkpoint is bound by a receipt (DD9).

    The preflight is not repeated: it was counted before the pause and is carried in the checkpoint. R6-3: a pause in
    this segment writes to `next_checkpoint_path`, never over the checkpoint that was resumed. R6-2: `progress` is the
    caller's shared accounting object."""
    progress = progress if progress is not None else {}
    doc = conversation.read(binding.run_dir)
    reasons = gate.resume_refusals(
        Path(checkpoint_path), config_sha256=_config_digest(binding.run_dir),
        revision=binding.revision(), session=doc['session'],
        job_running=_running_jobs(binding.project, binding.run_dir),
        receipt_checkpoint_sha256=receipt_checkpoint_sha256,
        turn_jobs=turn_jobs)  # C10/R5-10: the live flow counts the preflight; binding-only callers pass None
    if reasons:
        try:
            saved = json.loads(Path(checkpoint_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        ceiling = progress.get('ceiling')
        ceiling_out = ({'invocations': ceiling.invocations, 'completing': ceiling.completing,
                        'active_s': round(ceiling.active_s, 3)} if ceiling is not None else saved.get('ceiling'))
        return {'status': f'refused:{reasons[0]}', 'reasons': reasons, 'matrix_complete': False,
                'reports': list(saved.get('reports', []) if type(saved.get('reports')) is list else []),
                'ceiling': ceiling_out}
    data = json.loads(Path(checkpoint_path).read_text(encoding='utf-8'))
    return run_sequence(binding, gate, mode_for=mode_for, agents_sha256=agents_sha256, claude_pin=claude_pin,
                        truncate_after_s=truncate_after_s, cancel_after_s=cancel_after_s,
                        checkpoint_path=next_checkpoint_path or checkpoint_path, reports=data['reports'],
                        ceiling_state=data['ceiling'], evidence_root=evidence_root,
                        clock=clock, now=now, progress=progress)
