"""Harness identity capture for a marked Claude turn (F-2).

Reproduces reviews/k-g5-identity-argument.md section 2 (reference: scratch/k-g5/t4_identity_check.py
lines 54-102) plus the fail-closed rules of F-2 section 5.3. Reads bounded project and agent-home files,
hashes the admitted program file, and never starts a program or writes a file. A violation makes a
record null or the identity incomplete, so it never matches a certified identity by accident.
Enablement stays in harness_commands.support_for. Not wired into the turn path before F-2 t2.
"""
import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from scieflow.core.agent_config import EXTENDED_THINKING
from scieflow.core.harness_commands import HarnessIdentity

EXTENDED_THINKING_PREFIX = EXTENDED_THINKING
SCHEMA_DISCOVERY = 'scieflow.harness-identity.discovery/1'
SCHEMA_EXTENSIONS = 'scieflow.harness-identity.extensions/1'
SCHEMA_SETTINGS = 'scieflow.harness-identity.settings/1'
EXTENSIONS_DIR = '.claude/skills'
SETTINGS_FILES = ('.claude/settings.json', 'scripts/hooks/dvc-sync-reminder.py', 'config/sandbox.yml')
KNOWN_HOOK_COMMANDS = ('python3 "$CLAUDE_PROJECT_DIR/scripts/hooks/dvc-sync-reminder.py"',)
# The one evidenced hash -> version pairing (G5P-2); a new row needs its own review.
VERSION_PAIRINGS = {'8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978': '2.1.293'}
EFFORT_ENV = ('MAX_THINKING_TOKENS', '32000')
MAX_EXECUTABLE_BYTES = 1 << 30
MAX_TEXT_BYTES = 1 << 20
MAX_DIR_ENTRIES = 64
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CHUNK = 1 << 20


class _Bad(Exception):
    """A rule violation: the record that needs the file becomes None."""


def canonical_digest(record: dict) -> str:
    text = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def claude_versions_dir() -> Path:
    return Path(os.path.realpath(os.path.expanduser('~'))) / '.local' / 'share' / 'claude' / 'versions'


def derive_version(executable, executable_sha256, *, versions_dir, pairings=None) -> str | None:
    """Basename only for an absolute path directly in versions_dir with an evidenced pairing."""
    table = VERSION_PAIRINGS if pairings is None else pairings
    if not isinstance(executable, str) or not isinstance(executable_sha256, str):
        return None
    path = Path(executable)
    if not path.is_absolute() or path.parent != Path(versions_dir):
        return None
    if re.fullmatch(r'\d+\.\d+\.\d+', path.name) is None or table.get(executable_sha256) != path.name:
        return None
    return path.name


def reasoning_label(env_items, templates, configured: str) -> str:
    """'env:MAX_THINKING_TOKENS=32000' only when the env delivers it and no argv token carries it."""
    token = f'{EFFORT_ENV[0]}={EFFORT_ENV[1]}'
    delivered = dict(env_items).get(EFFORT_ENV[0]) == EFFORT_ENV[1]
    if delivered and not any(token in str(t).split() for t in templates):
        return 'env:' + token
    return configured or ''


def turn_effort(cfg: dict):
    """EFFORT_ENV for a claude conversation turn in extended thinking, else None (F2-D2)."""
    if cfg.get('family') != 'claude':
        return None
    if any('{reasoning}' in str(cfg.get(key, '')) for key in ('session_cmd', 'resume_cmd')):
        return None
    reasoning = cfg.get('reasoning')
    if reasoning == 'extended-thinking' or (not reasoning and str(cfg.get('cmd', '')).startswith(EXTENDED_THINKING_PREFIX)):
        return EFFORT_ENV
    return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fd_key(fd: int) -> tuple:
    st = os.fstat(fd)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _read_regular(path: Path, limit: int = MAX_TEXT_BYTES) -> bytes | None:
    """Bytes of a regular non-symlink file; None if absent, a symlink or not regular; _Bad if too big."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise _Bad from exc
    with os.fdopen(fd, 'rb') as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > limit:
            raise _Bad
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise _Bad
    return data


def _file_sha(path: Path) -> str | None:
    data = _read_regular(path)
    return None if data is None else _sha(data)


def _real_dir(path: Path) -> None:
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise _Bad


def _home_settings(home_claude: Path) -> dict:
    try:
        mode = os.lstat(home_claude).st_mode
    except FileNotFoundError:
        return {}
    if not stat.S_ISDIR(mode) or len(os.listdir(home_claude)) > MAX_DIR_ENTRIES:
        raise _Bad
    out = {}
    for name in sorted(os.listdir(home_claude)):
        if re.fullmatch(r'settings[^/]*\.json', name):
            data = _read_regular(home_claude / name)
            if data is None:  # a symlinked settings file is never silently dropped
                raise _Bad
            out[name] = _sha(data)
    return out


def read_discovery(cwd, agent_home, *, forbidden=()) -> dict | None:
    """Project instruction files and the agent home's settings; None without a sandboxed home."""
    if agent_home is None:
        return None
    home_claude = Path(agent_home) / '.claude'
    if any(Path(t) == home_claude or home_claude in Path(t).parents for t in forbidden):
        return None
    try:
        record: dict = {name: _file_sha(Path(cwd) / name) for name in ('AGENTS.md', 'CLAUDE.md', '.mcp.json')}
        record['home-claude-settings'] = _home_settings(home_claude)
        return record
    except (_Bad, OSError, ValueError, UnicodeError):
        return None


def _skill_files(folder: Path) -> dict:
    names = os.listdir(folder)
    if len(names) > MAX_DIR_ENTRIES:
        raise _Bad
    files = {}
    for name in sorted(names):
        if not stat.S_ISREG(os.lstat(folder / name).st_mode):  # subfolder, symlinked file or other
            raise _Bad
        digest = _file_sha(folder / name)
        if digest is None:
            raise _Bad
        files[name] = digest
    return files


def read_extensions(cwd) -> dict | None:
    """Every .claude/skills entry is a link to a directory inside the project; its files are hashed."""
    try:
        root = os.path.realpath(cwd)
        _real_dir(Path(cwd) / '.claude')
        folder = Path(cwd) / EXTENSIONS_DIR
        _real_dir(folder)
        names = os.listdir(folder)
        if len(names) > MAX_DIR_ENTRIES:
            raise _Bad
        entries = {}
        for name in sorted(names):
            link = folder / name
            if not stat.S_ISLNK(os.lstat(link).st_mode):
                raise _Bad
            target = os.path.realpath(link)
            if not os.path.isdir(target) or not target.startswith(root + os.sep):
                raise _Bad
            entries[name] = {'symlink': os.readlink(link), 'files': _skill_files(Path(target))}
        return {'dir': EXTENSIONS_DIR, 'entries': entries}
    except (_Bad, OSError, ValueError, UnicodeError):
        return None


def read_settings(cwd) -> dict | None:
    """The one known hook, the settings file and the sandbox policy; local overrides void the record."""
    try:
        root = Path(cwd)
        _real_dir(root / '.claude')
        if os.path.lexists(root / '.claude' / 'settings.local.json') or os.path.lexists(root / 'CLAUDE.local.md'):
            return None
        raw = _read_regular(root / '.claude' / 'settings.json')
        data = json.loads(raw.decode('utf-8')) if raw is not None else None
        if not isinstance(data, dict):
            return None
        hooks = data.get('hooks', {})
        commands = [h['command'] for grp in hooks.values() for e in grp for h in e['hooks']]
        if commands != list(KNOWN_HOOK_COMMANDS):
            return None
        files = {rel: _file_sha(root / rel) for rel in SETTINGS_FILES}
        return None if None in files.values() else {'files': files}
    except (_Bad, OSError, ValueError, UnicodeError, TypeError, KeyError, AttributeError):
        return None


def executable_digest(executable, facts) -> str | None:
    """sha256 of the admitted program, bound to its captured path facts before and after hashing."""
    if not isinstance(executable, str):
        return None
    path = Path(executable)
    fact = next((f for f in facts if f.path == path), None)
    if fact is None or None in (fact.device, fact.inode, fact.size, fact.mtime_ns, fact.ctime_ns):
        return None
    expected = (fact.device, fact.inode, fact.size, fact.mtime_ns, fact.ctime_ns)
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        return None
    with os.fdopen(fd, 'rb') as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_EXECUTABLE_BYTES or _fd_key(handle.fileno()) != expected:
            return None
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(_CHUNK), b''):
            digest.update(block)
        if _fd_key(handle.fileno()) != expected:
            return None
    return digest.hexdigest()


def derive(*, agent, family, executable, executable_sha256, version, session_template, resume_template,
           model, reasoning, cwd, discovery, extensions, settings, sandbox_enabled, policy_sha256) -> HarnessIdentity:
    """Pure: the 15 fields from captured records; effects_pinned needs all three and the sandbox policy."""
    discovery_sha = None if discovery is None else canonical_digest({'schema': SCHEMA_DISCOVERY, **discovery})
    extensions_sha = None if extensions is None else canonical_digest({'schema': SCHEMA_EXTENSIONS, **extensions})
    settings_sha = None if settings is None else canonical_digest({'schema': SCHEMA_SETTINGS, **settings})
    pinned = (bool(sandbox_enabled) and None not in (discovery_sha, extensions_sha, settings_sha)
              and settings is not None and policy_sha256 is not None
              and policy_sha256 == settings['files'].get('config/sandbox.yml'))
    return HarnessIdentity(agent=agent, family=family, enabled=True, executable=executable,
                           executable_sha256=executable_sha256, version=version,
                           session_template=session_template, resume_template=resume_template,
                           model=model, reasoning=reasoning, cwd=cwd, discovery_sha256=discovery_sha,
                           extensions_sha256=extensions_sha, settings_sha256=settings_sha,
                           effects_pinned=pinned)


def capture(*, agent, family, executable, session_template, resume_template, model, configured_reasoning,
            cwd, env_items, execution_facts, protection, agent_home, credential_targets=(),
            versions_dir=None) -> HarnessIdentity:
    """Marked turns only: bounded reads and one hash; writes and executes nothing."""
    sandboxed = bool(protection.sandbox_enabled)
    sha = executable_digest(executable, execution_facts)
    version = derive_version(executable, sha, versions_dir=versions_dir or claude_versions_dir())
    home = Path(agent_home) if sandboxed and agent_home is not None else None
    return derive(agent=agent, family=family, executable=executable, executable_sha256=sha, version=version,
                  session_template=session_template, resume_template=resume_template, model=model,
                  reasoning=reasoning_label(env_items, (session_template, resume_template), configured_reasoning),
                  cwd=str(cwd), discovery=read_discovery(cwd, home, forbidden=credential_targets),
                  extensions=read_extensions(cwd), settings=read_settings(cwd),
                  sandbox_enabled=sandboxed, policy_sha256=protection.policy_sha256)


def uncaptured(*, agent, family, executable, session_template, resume_template, model, configured_reasoning,
               cwd, **_capture_only) -> HarnessIdentity:
    """Today's production shape (agent_run.py resolve_turn), used for ordinary turns."""
    return HarnessIdentity(agent, family, True, executable, None, None, session_template, resume_template,
                           model, configured_reasoning, str(cwd), None, None, None, False)
