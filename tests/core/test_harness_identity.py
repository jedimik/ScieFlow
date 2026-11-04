"""F-2 t1: the harness identity recipe (scieflow.core.harness_identity).

Hermetic: every filesystem input is a synthetic tree under tmp_path. The only evidence read is the
r14 fixture (tests/fixtures/harness_identity_r14.json). No host home, no probe tree, no subprocess.
"""
import dataclasses
import errno
import hashlib
import json
import os
import shutil
import stat
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scieflow.core import harness_commands as hc
from scieflow.core import harness_identity as hi
from scieflow.core import sandbox

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'harness_identity_r14.json').read_text())
HOOK = hi.KNOWN_HOOK_COMMANDS[0]
PRODUCTION_TUPLE = hc.CERTIFIED_POLICIES


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _settings_json(commands=(HOOK,)) -> bytes:
    hooks = [{'type': 'command', 'command': c} for c in commands]
    return json.dumps({'hooks': {'PostToolUse': [{'matcher': '', 'hooks': hooks}]}}).encode()


def _project(root: Path) -> Path:
    """A synthetic project with every r14-shaped input; links point inside the project."""
    files = {
        'AGENTS.md': b'agents\n',
        'CLAUDE.md': b'claude\n',
        '.claude/settings.json': _settings_json(),
        'scripts/hooks/dvc-sync-reminder.py': b'print("hook")\n',
        'config/sandbox.yml': b'credentials: {}\n',
        'skills/scieflow-menu/SKILL.md': b'menu\n',
        'skills/workspace-sync/SKILL.md': b'sync\n',
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / '.claude' / 'skills').mkdir(parents=True)
    os.symlink('../../skills/scieflow-menu', root / '.claude' / 'skills' / 'scieflow-menu')
    os.symlink('../../skills/workspace-sync', root / '.claude' / 'skills' / 'workspace-sync')
    return root


def _expected_settings(root: Path) -> dict:
    return {'files': {rel: _sha((root / rel).read_bytes()) for rel in hi.SETTINGS_FILES}}


def _expected_extensions(root: Path) -> dict:
    return {'dir': '.claude/skills', 'entries': {
        'scieflow-menu': {'symlink': '../../skills/scieflow-menu',
                          'files': {'SKILL.md': _sha((root / 'skills/scieflow-menu/SKILL.md').read_bytes())}},
        'workspace-sync': {'symlink': '../../skills/workspace-sync',
                           'files': {'SKILL.md': _sha((root / 'skills/workspace-sync/SKILL.md').read_bytes())}},
    }}


def _r14_derived() -> hc.HarnessIdentity:
    inputs = FIXTURE['inputs']
    records = FIXTURE['recipe_records']
    version = hi.derive_version(inputs['executable'], inputs['executable_sha256'],
                                versions_dir=Path(FIXTURE['versions_dir']))
    return hi.derive(agent=inputs['agent'], family=inputs['family'], executable=inputs['executable'],
                     executable_sha256=inputs['executable_sha256'], version=version,
                     session_template=inputs['session_template'], resume_template=inputs['resume_template'],
                     model=inputs['model'], reasoning=inputs['reasoning'], cwd=inputs['cwd'],
                     discovery=records['discovery'], extensions=records['extensions'],
                     settings=records['settings'], sandbox_enabled=inputs['sandbox_enabled'],
                     policy_sha256=inputs['policy_sha256'])


# ---- r14 equality (plan t1 RED 1) ----------------------------------------------------------

def test_fixture_is_pinned_to_the_t4_evidence_file():
    assert len(FIXTURE['source']) == 64 and FIXTURE['source'] == 'b4fef519fe0d8c6466cab95a87ed48987f1d665097412f3b760125d850b15f6a'
    assert FIXTURE['source_path'] == 'scratch/k-g5/t4-identity.json'


def test_recipe_digests_reproduce_the_three_evidence_digests():
    records = FIXTURE['recipe_records']
    digests = FIXTURE['digests']
    assert hi.canonical_digest(records['discovery']) == digests['discovery_sha256']
    assert hi.canonical_digest(records['extensions']) == digests['extensions_sha256']
    assert hi.canonical_digest(records['settings']) == digests['settings_sha256']


def test_r14_derivation_equals_t4_identity_and_the_certified_entry():
    derived = _r14_derived()
    assert derived == hc.HarnessIdentity(**FIXTURE['identity_t4'])
    assert derived == hc.CERTIFIED_POLICIES[0].identity


def test_r14_identity_is_enabled_only_under_the_production_tuple(monkeypatch):
    derived = _r14_derived()
    assert hc.support_for(derived).enabled is True
    monkeypatch.setattr(hc, 'CERTIFIED_POLICIES', ())
    assert hc.support_for(derived).enabled is False


# ---- version rule (plan t1 RED 2) ----------------------------------------------------------

def test_version_is_the_basename_only_for_the_evidenced_pairing():
    vd = Path(FIXTURE['versions_dir'])
    sha = FIXTURE['inputs']['executable_sha256']
    assert hi.derive_version(str(vd / '2.1.293'), sha, versions_dir=vd) == '2.1.293'


@pytest.mark.parametrize('executable, sha, expected', [
    ('/home/ubuntu/.local/share/claude/versions/2.1.306', '520bc6c1f40af466bd03a278b5ae1af1a2938bc7de5b9e5cb15526da20d71cfe', None),
    ('/home/ubuntu/.local/bin/claude', '8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978', None),
    ('/home/ubuntu/.local/share/claude/versions/2.1.293', '0' * 64, None),
    ('/home/ubuntu/.local/share/claude/versions/2.1', '8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978', None),
    ('relative/2.1.293', '8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978', None),
    (None, '8968405e26db478af44eabc4635ab5ca557057b702a54460a59c13e1b253e978', None),
])
def test_version_rule_refuses_every_other_input(executable, sha, expected):
    vd = Path(FIXTURE['versions_dir'])
    assert hi.derive_version(executable, sha, versions_dir=vd) is expected


def test_a_monkeypatched_pairing_is_honoured_only_for_its_own_hash(tmp_path):
    vd = tmp_path / 'versions'
    exe = str(vd / '9.9.9')
    table = {'b' * 64: '9.9.9'}
    assert hi.derive_version(exe, 'b' * 64, versions_dir=vd, pairings=table) == '9.9.9'
    assert hi.derive_version(exe, 'c' * 64, versions_dir=vd, pairings=table) is None


# ---- single-field mutations (plan t1 RED 3) ------------------------------------------------

@pytest.mark.parametrize('name', [f.name for f in fields(hc.HarnessIdentity)])
def test_every_single_field_mutation_stays_disabled(name, monkeypatch):
    base = _r14_derived()
    assert hc.support_for(base).enabled is True
    value = getattr(base, name)
    changed = (not value) if isinstance(value, bool) else (value or '') + 'x'
    mutated = replace(base, **{name: changed})
    assert mutated != base
    assert hc.support_for(mutated).enabled is False


def test_there_are_fifteen_identity_fields():
    assert len(fields(hc.HarnessIdentity)) == 15


# ---- reasoning label and effort rule (plan t1 RED 6) ---------------------------------------

def test_reasoning_label_reports_the_delivered_effort_only():
    templates = ('claude -p {prompt}', 'claude -p --resume {session} {prompt}')
    delivered = (('MAX_THINKING_TOKENS', '32000'),)
    assert hi.reasoning_label(delivered, templates, '') == 'env:MAX_THINKING_TOKENS=32000'
    assert hi.reasoning_label((), templates, '') == ''
    assert hi.reasoning_label((), templates, 'high') == 'high'
    token_template = ('env MAX_THINKING_TOKENS=32000 claude -p {prompt}', templates[1])
    assert hi.reasoning_label(delivered, token_template, 'high') == 'high'


@pytest.mark.parametrize('cfg, expected', [
    ({'family': 'claude', 'reasoning': 'extended-thinking', 'cmd': 'claude -p {prompt}'}, True),
    ({'family': 'claude', 'cmd': hi.EXTENDED_THINKING_PREFIX + 'claude -p {prompt}'}, True),
    ({'family': 'claude', 'reasoning': '', 'cmd': hi.EXTENDED_THINKING_PREFIX + 'claude -p {prompt}'}, True),
    ({'family': 'claude', 'reasoning': 'default', 'cmd': hi.EXTENDED_THINKING_PREFIX + 'claude -p {prompt}'}, False),
    ({'family': 'claude', 'reasoning': 'extended-thinking', 'session_cmd': 'claude {reasoning} {prompt}'}, False),
    ({'family': 'claude', 'cmd': 'claude -p {prompt}'}, False),
    ({'family': 'codex', 'reasoning': 'extended-thinking', 'cmd': 'codex {prompt}'}, False),
])
def test_turn_effort_branches(cfg, expected):
    result = hi.turn_effort(cfg)
    assert (result == ('MAX_THINKING_TOKENS', '32000')) is expected
    assert (result is None) is (not expected)


def test_claude_versions_dir_follows_the_process_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    assert hi.claude_versions_dir() == Path(os.path.realpath(home)) / '.local' / 'share' / 'claude' / 'versions'


# ---- readers on a synthetic project (plan t1 RED 4) ----------------------------------------

def test_discovery_record_matches_the_recipe(tmp_path):
    root = _project(tmp_path / 'proj')
    home = tmp_path / 'agent-home'
    assert hi.read_discovery(root, home) == {
        'AGENTS.md': _sha(b'agents\n'), 'CLAUDE.md': _sha(b'claude\n'), '.mcp.json': None,
        'home-claude-settings': {},
    }
    (home / '.claude').mkdir(parents=True)
    (home / '.claude' / 'settings.json').write_bytes(b'{}')
    (home / '.claude' / 'settings.local.json').write_bytes(b'{}')
    (home / '.claude' / 'notes.txt').write_bytes(b'x')
    record = hi.read_discovery(root, home)
    assert record['home-claude-settings'] == {'settings.json': _sha(b'{}'), 'settings.local.json': _sha(b'{}')}


def test_discovery_needs_an_agent_home(tmp_path):
    assert hi.read_discovery(_project(tmp_path / 'proj'), None) is None


def test_extensions_record_matches_the_recipe(tmp_path):
    root = _project(tmp_path / 'proj')
    assert hi.read_extensions(root) == _expected_extensions(root)


def test_settings_record_matches_the_recipe(tmp_path):
    root = _project(tmp_path / 'proj')
    assert hi.read_settings(root) == _expected_settings(root)


def _symlink_skill_dir(root: Path) -> Path:
    return root / 'skills' / 'scieflow-menu'


def test_discovery_symlinked_agents_file_yields_a_null_value(tmp_path):
    root = _project(tmp_path / 'proj')
    (root / 'AGENTS.md').unlink()
    os.symlink(root / 'CLAUDE.md', root / 'AGENTS.md')
    record = hi.read_discovery(root, tmp_path / 'agent-home')
    assert record is not None and record['AGENTS.md'] is None


def test_discovery_oversized_file_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    (root / 'AGENTS.md').write_bytes(b'a' * (hi.MAX_TEXT_BYTES + 1))
    assert hi.read_discovery(root, tmp_path / 'agent-home') is None


def test_discovery_symlinked_home_settings_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    home = tmp_path / 'agent-home' / '.claude'
    home.mkdir(parents=True)
    os.symlink(root / 'AGENTS.md', home / 'settings.json')
    assert hi.read_discovery(root, tmp_path / 'agent-home') is None


def test_discovery_credential_under_the_agent_claude_folder_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    home = tmp_path / 'agent-home'
    assert hi.read_discovery(root, home, forbidden=[home / '.claude' / 'token.json']) is None
    assert hi.read_discovery(root, home, forbidden=[tmp_path / 'elsewhere' / 'token.json']) is not None


def test_discovery_unreadable_file_voids_the_record(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('root reads mode-000 files')
    root = _project(tmp_path / 'proj')
    (root / 'CLAUDE.md').chmod(0)
    try:
        assert hi.read_discovery(root, tmp_path / 'agent-home') is None
    finally:
        (root / 'CLAUDE.md').chmod(0o644)


def test_extensions_missing_folder_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    (root / '.claude' / 'skills' / 'scieflow-menu').unlink()
    (root / '.claude' / 'skills' / 'workspace-sync').unlink()
    (root / '.claude' / 'skills').rmdir()
    assert hi.read_extensions(root) is None


def test_extensions_subfolder_in_a_linked_skill_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    (_symlink_skill_dir(root) / 'nested').mkdir()
    assert hi.read_extensions(root) is None


def test_extensions_symlinked_file_in_a_linked_skill_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    os.symlink(root / 'AGENTS.md', _symlink_skill_dir(root) / 'extra.md')
    assert hi.read_extensions(root) is None


def test_extensions_link_escaping_the_project_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    outside = tmp_path / 'outside' / 'skill'
    outside.mkdir(parents=True)
    (outside / 'SKILL.md').write_bytes(b'outside\n')
    os.symlink(outside, root / '.claude' / 'skills' / 'escape')
    assert hi.read_extensions(root) is None


def test_extensions_regular_entry_or_file_link_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    (root / '.claude' / 'skills' / 'plain').mkdir()
    assert hi.read_extensions(root) is None
    (root / '.claude' / 'skills' / 'plain').rmdir()
    os.symlink(root / 'AGENTS.md', root / '.claude' / 'skills' / 'to-file')
    assert hi.read_extensions(root) is None


def test_extensions_listing_above_the_bound_voids_the_record(tmp_path):
    root = _project(tmp_path / 'proj')
    for i in range(hi.MAX_DIR_ENTRIES):
        os.symlink('../../skills/scieflow-menu', root / '.claude' / 'skills' / f'extra-{i}')
    assert hi.read_extensions(root) is None


@pytest.mark.parametrize('mutate', [
    lambda root: (root / '.claude' / 'settings.json').unlink() or os.symlink(root / 'AGENTS.md', root / '.claude' / 'settings.json'),
    lambda root: (root / '.claude' / 'settings.json').write_bytes(_settings_json(('python3 evil.py',))),
    lambda root: (root / '.claude' / 'settings.json').write_bytes(b'[]'),
    lambda root: (root / '.claude' / 'settings.json').write_bytes(b'{not json'),
    lambda root: (root / '.claude' / 'settings.local.json').write_bytes(b'{}'),
    lambda root: (root / 'CLAUDE.local.md').write_bytes(b'local\n'),
    lambda root: (root / 'scripts' / 'hooks' / 'dvc-sync-reminder.py').unlink(),
    lambda root: (root / 'config' / 'sandbox.yml').unlink() or os.symlink(root / 'AGENTS.md', root / 'config' / 'sandbox.yml'),
    lambda root: (root / '.claude' / 'settings.json').write_bytes(b'{"x": "' + b'a' * hi.MAX_TEXT_BYTES + b'"}'),
])
def test_settings_violations_void_the_record(tmp_path, mutate):
    root = _project(tmp_path / 'proj')
    mutate(root)
    assert hi.read_settings(root) is None


# ---- executable digest (plan t1 RED 5) -----------------------------------------------------

def _executable(folder: Path, name: str = 'prog') -> tuple[Path, bytes]:
    folder.mkdir(parents=True, exist_ok=True)
    exe = (folder / name).resolve()
    data = b'#!/bin/sh\necho synthetic\n'
    exe.write_bytes(data)
    exe.chmod(0o755)
    return exe, data


def test_executable_digest_hashes_only_the_admitted_file(tmp_path):
    exe, data = _executable(tmp_path / 'bin')
    facts = sandbox._capture_path_facts([exe])
    assert hi.executable_digest(str(exe), facts) == _sha(data)


def test_executable_digest_needs_a_matching_fact(tmp_path):
    exe, _ = _executable(tmp_path / 'bin')
    other = (tmp_path / 'other').resolve()
    other.write_bytes(b'x')
    assert hi.executable_digest(str(exe), sandbox._capture_path_facts([other])) is None


@pytest.mark.parametrize('field', ['size', 'inode', 'device', 'mtime_ns', 'ctime_ns'])
def test_executable_digest_refuses_a_fact_that_disagrees(tmp_path, field):
    exe, _ = _executable(tmp_path / 'bin')
    facts = sandbox._capture_path_facts([exe])
    fact = next(f for f in facts if f.path == exe)
    bad = dataclasses.replace(fact, **{field: getattr(fact, field) + 1})
    assert hi.executable_digest(str(exe), [bad]) is None


def test_executable_digest_refuses_a_symlink(tmp_path):
    exe, _ = _executable(tmp_path / 'bin')
    link = tmp_path / 'link'
    os.symlink(exe, link)
    assert hi.executable_digest(str(link), sandbox._capture_path_facts([link])) is None


def test_executable_digest_refuses_a_file_rewritten_while_hashed(tmp_path, monkeypatch):
    exe, _ = _executable(tmp_path / 'bin')
    facts = sandbox._capture_path_facts([exe])
    real = hi._fd_key
    calls = {'n': 0}

    def moving(fd):
        calls['n'] += 1
        key = real(fd)
        return key if calls['n'] == 1 else (key[0], key[1], key[2], key[3] + 1, key[4])

    monkeypatch.setattr(hi, '_fd_key', moving)
    assert hi.executable_digest(str(exe), facts) is None


# ---- capture, uncaptured, and the no-process guard -----------------------------------------

def _capture_fixture(tmp_path: Path, monkeypatch, *, sandboxed=True):
    root = _project(tmp_path / 'proj')
    exe, data = _executable(tmp_path / 'versions-root', '9.9.9')
    versions = exe.parent
    monkeypatch.setattr(hi, 'VERSION_PAIRINGS', {_sha(data): exe.name})
    protection = SimpleNamespace(sandbox_enabled=sandboxed, policy_sha256=_sha(b'credentials: {}\n'))
    kwargs = dict(agent='claude', family='claude', executable=str(exe),
                  session_template='claude -p --model {model} {prompt}',
                  resume_template='claude -p --model {model} --resume {session} {prompt}',
                  model='fixture-model', configured_reasoning='', cwd=root,
                  env_items=(('MAX_THINKING_TOKENS', '32000'),),
                  execution_facts=sandbox._capture_path_facts([exe]), protection=protection,
                  agent_home=tmp_path / 'agent-home', versions_dir=versions)
    return root, exe, kwargs


def test_capture_fills_all_fifteen_fields_on_a_synthetic_turn(tmp_path, monkeypatch):
    root, exe, kwargs = _capture_fixture(tmp_path, monkeypatch)
    identity = hi.capture(**kwargs)
    assert identity.effects_pinned is True and identity.enabled is True
    assert identity.version == exe.name
    assert identity.reasoning == 'env:MAX_THINKING_TOKENS=32000'
    assert identity.executable_sha256 == _sha(exe.read_bytes())
    assert identity.discovery_sha256 and identity.extensions_sha256 and identity.settings_sha256
    assert hc.support_for(identity).enabled is False  # no exact certified policy for this tree


@pytest.mark.parametrize('case', ['unsandboxed', 'settings-local', 'credential-under-claude', 'no-skills'])
def test_capture_violations_leave_the_identity_disabled(tmp_path, monkeypatch, case):
    root, exe, kwargs = _capture_fixture(tmp_path, monkeypatch, sandboxed=case != 'unsandboxed')
    if case == 'settings-local':
        (root / '.claude' / 'settings.local.json').write_bytes(b'{}')
    if case == 'credential-under-claude':
        kwargs['credential_targets'] = (tmp_path / 'agent-home' / '.claude' / 'token.json',)
    if case == 'no-skills':
        (root / '.claude' / 'skills' / 'scieflow-menu').unlink()
        (root / '.claude' / 'skills' / 'workspace-sync').unlink()
        (root / '.claude' / 'skills').rmdir()
    identity = hi.capture(**kwargs)
    assert identity.effects_pinned is False
    assert hc.support_for(identity).enabled is False


def test_uncaptured_is_todays_production_shape(tmp_path):
    identity = hi.uncaptured(agent='claude', family='claude', executable='/x/claude',
                             session_template='s {prompt}', resume_template='r {session} {prompt}',
                             model='m', configured_reasoning='', cwd='/x')
    assert identity == hc.HarnessIdentity('claude', 'claude', True, '/x/claude', None, None,
                                          's {prompt}', 'r {session} {prompt}', 'm', '', '/x',
                                          None, None, None, False)


def test_capture_runs_no_process(tmp_path, monkeypatch):
    root, exe, kwargs = _capture_fixture(tmp_path, monkeypatch)

    def refuse(*args, **kw):
        raise AssertionError('harness_identity must not start a process')

    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    for name in ('execv', 'execve', 'execvp', 'execvpe', 'posix_spawn', 'posix_spawnp', 'system', 'fork'):
        monkeypatch.setattr(os, name, refuse)
    assert hi.capture(**kwargs).effects_pinned is True


def test_module_source_has_no_process_or_write_entry_points():
    source = Path(hi.__file__).read_text(encoding='utf-8')
    for banned in ('import subprocess', 'os.exec', 'os.spawn', 'os.system', 'os.fork', 'os.write', 'open(' + "'w"):
        assert banned not in source, banned


def test_module_stays_within_the_production_line_budget():
    lines = [line for line in Path(hi.__file__).read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(lines) <= 260, len(lines)


# ---- F-2 t1 review r1 fixes (reviews/f-2-t1-review-r1.md findings 1-4) -----------------------

def test_deeply_nested_settings_yields_none_not_an_exception(tmp_path):
    root = _project(tmp_path / 'proj')
    (root / '.claude' / 'settings.json').write_bytes(b'[' * 900000)
    assert hi.read_settings(root) is None


def test_deeply_nested_settings_gives_an_incomplete_identity(tmp_path, monkeypatch):
    root, _, kwargs = _capture_fixture(tmp_path, monkeypatch)
    (root / '.claude' / 'settings.json').write_bytes(b'[' * 900000)
    identity = hi.capture(**kwargs)
    assert identity.settings_sha256 is None and identity.effects_pinned is False


class _ReadFails:
    """Stands in for the handle os.fdopen returns: every read raises an I/O error."""

    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handle.close()
        return False

    def fileno(self):
        return self._handle.fileno()

    def read(self, size=-1):
        raise OSError(errno.EIO, 'injected read error')


def _reads_fail(monkeypatch):
    real_fdopen = os.fdopen
    monkeypatch.setattr(hi.os, 'fdopen', lambda fd, mode: _ReadFails(real_fdopen(fd, mode)))


def test_executable_digest_read_error_returns_none(tmp_path, monkeypatch):
    exe, _ = _executable(tmp_path / 'bin')
    facts = sandbox._capture_path_facts([exe])
    _reads_fail(monkeypatch)
    assert hi.executable_digest(str(exe), facts) is None


def test_capture_survives_an_executable_read_error(tmp_path, monkeypatch):
    _, _, kwargs = _capture_fixture(tmp_path, monkeypatch)
    _reads_fail(monkeypatch)
    identity = hi.capture(**kwargs)
    assert identity.executable_sha256 is None and identity.version is None
    assert identity.effects_pinned is False


@pytest.mark.parametrize('folder', ['scripts', 'scripts/hooks', 'config'])
def test_settings_linked_directory_component_voids_the_record(tmp_path, folder):
    root = _project(tmp_path / 'proj')
    moved = tmp_path / ('moved-' + folder.replace('/', '-'))
    shutil.move(str(root / folder), str(moved))
    os.symlink(moved, root / folder)
    assert hi.read_settings(root) is None


def test_home_settings_listing_bound(tmp_path):
    root = _project(tmp_path / 'proj')
    home = tmp_path / 'agent-home' / '.claude'
    home.mkdir(parents=True)
    for i in range(hi.MAX_DIR_ENTRIES):
        (home / f'note-{i}.txt').write_bytes(b'x')
    assert hi.read_discovery(root, tmp_path / 'agent-home') is not None
    (home / 'one-more.txt').write_bytes(b'x')
    assert hi.read_discovery(root, tmp_path / 'agent-home') is None


def test_skill_folder_listing_bound(tmp_path):
    root = _project(tmp_path / 'proj')
    folder = _symlink_skill_dir(root)
    for i in range(hi.MAX_DIR_ENTRIES - 1):  # SKILL.md is already one of the 64 entries
        (folder / f'extra-{i}.md').write_bytes(b'x')
    assert hi.read_extensions(root) is not None
    (folder / 'one-more.md').write_bytes(b'x')
    assert hi.read_extensions(root) is None


def test_unreadable_settings_file_voids_the_record(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('root reads mode-000 files')
    root = _project(tmp_path / 'proj')
    (root / '.claude' / 'settings.json').chmod(0)
    try:
        assert hi.read_settings(root) is None
    finally:
        (root / '.claude' / 'settings.json').chmod(0o644)


def test_unreadable_skill_file_voids_the_extensions_record(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('root reads mode-000 files')
    root = _project(tmp_path / 'proj')
    skill = _symlink_skill_dir(root) / 'SKILL.md'
    skill.chmod(0)
    try:
        assert hi.read_extensions(root) is None
    finally:
        skill.chmod(0o644)
