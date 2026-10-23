"""Offline tests for the K Task8 D7 probe binding (plan r2, T1-T7).

Written test-first: the binding module k_task8_binding.py is authored after the
RED run was recorded. Nothing here calls the real claude CLI; the subprocess guard
below refuses any claude launch, and the agent is the synthetic stream fixture.
"""
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import types
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
W = HERE.parents[1]
FIXTURE = W / 'tests' / 'fixtures' / 'k_task8_stream_fixture.py'
sys.path.insert(0, str(HERE))

import k_task8_binding as kb  # noqa: E402  (RED: module absent until implemented)

from scieflow.core import agent_run, harness_commands, jobs, sandbox, service  # noqa: E402
from scieflow.core.project import Project  # noqa: E402
from scieflow.core.turn_input import HumanSubmission  # noqa: E402

PY = sys.executable


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch, tmp_path):
    """R10-5: every test reads a FAKE claude token from a tmp credentials dir (test seam CREDENTIALS_OVERRIDE).
    The real ~/.config/scieflow/credentials is never resolved under test."""
    cred = tmp_path / 'credentials'
    cred.mkdir(mode=0o700)
    token = cred / 'claude.token'
    token.write_text(f'k8-fake-token-{os.urandom(8).hex()}', encoding='utf-8')
    token.chmod(0o600)
    monkeypatch.setattr(sandbox, 'CREDENTIALS_OVERRIDE', cred)
    yield


def _h_k() -> Path:
    """R11-3: the probe tree is the harness constant H.K, the single source for the session guard and the pin."""
    return Path(_gate().K)


def _real_k_state():
    k = _h_k()  # R11-3: the tree the live probe uses (K11 after R11-2; K10 on the r10b bytes)
    workspace = k / 'workspace'
    return (kb.tree_digest(k), sorted(p.name for p in workspace.iterdir()) if workspace.is_dir() else None)


@pytest.fixture(scope='session', autouse=True)
def real_k_unchanged():
    """C3/R4-2: the real K source trees and K/workspace listing are byte-identical before and after the session."""
    before = _real_k_state()
    yield
    assert _real_k_state() == before, 'the real K trees or K/workspace changed during the binding suite'


# R10 D-guard: the only argv items that may name a `claude` path are the test-owned fake launchers registered by
# _fixture_templates (exact string equality); every other `claude` token is still refused.
FAKE_LAUNCHERS: set = set()


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch, tmp_path):
    """Refuse any claude launch; only interpreter, bwrap and /usr/bin/env may start."""
    FAKE_LAUNCHERS.clear()
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', kb.path_without_claude(os.environ.get('PATH', '')))  # C7/R5-6: no claude directory
    # C2/R5-3: the offline suite runs under the same allowlist as a live launch (monkeypatch restores it).
    monkeypatch.setattr(kb, 'ENV_TEST_RUNNER', frozenset({'PYTEST_CURRENT_TEST'}))
    for key in list(os.environ):
        if key not in kb.ENV_ALLOW and key not in kb.ENV_TEST_RUNNER and not key.startswith('LC_'):
            monkeypatch.delenv(key, raising=False)
    real_popen = subprocess.Popen
    allowed_names = {'bwrap', 'env', 'sh', 'python3.12', 'python3', 'python'}

    class Guard(real_popen):
        def __init__(self, args, *more, **kwargs):
            argv = [str(a) for a in args] if isinstance(args, (list, tuple)) else [str(args)]
            checked = [a for a in argv if a not in FAKE_LAUNCHERS]  # R10 D-guard: exact fake launcher path only
            if os.path.basename(argv[0]) == 'bwrap' and '--' in checked:
                # R10 D-guard2: bwrap's own bind arguments (e.g. the agent home .../scratch/agent-home/claude) are not
                # launches; the command after the first `--` is what would start claude and is still checked.
                checked = checked[checked.index('--') + 1:]
            if kb.claude_argv_denied(checked, str(kb.CLAUDE_BINARY)):  # C7/R5-6: basename, pin, shell-string tokens
                raise AssertionError(f'offline guard: claude launch refused: {argv}')
            if os.path.basename(argv[0]) not in allowed_names and argv[0] != PY:
                raise AssertionError(f'offline guard: non-allowlisted launch refused: {argv[0]}')
            super().__init__(args, *more, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', Guard)
    yield


@pytest.fixture
def k_project(tmp_path):
    root = tmp_path / 'proj'
    shutil.copytree(W / 'config', root / 'config')
    shutil.copytree(W / 'schemas', root / 'schemas')
    (root / 'workspace').mkdir()
    return Project(root=root)


def _mode_file(tmp_path: Path) -> Path:
    """R10: the mode file lives in the fake install root. The sandbox binds the launcher's install root (read-only)
    only when it has the `versions/<v>/` shape (sandbox._install_root); a plain file is bound alone, so the mode
    file would be invisible. The layout is tmp_path/fake-claude/versions/9.9.9/{claude,mode.txt}."""
    path = tmp_path / 'fake-claude' / 'versions' / '9.9.9'
    path.mkdir(parents=True, exist_ok=True)
    return path / 'mode.txt'


FAKE_TARGET: dict = {}  # R12-4: the realpath of the fake launcher, recorded by _fixture_templates (test-known)


def _fixture_templates(mode_file: Path):
    """R10-2 / R12-4: production's claude templates. `claude` resolves on PATH to a SYMLINK (the host's shape) that
    points at a test-owned fake launcher (an sh wrapper that execs the stream fixture). The wrapper is a regular file
    beside the mode file, in the versions/<v>/ install root the sandbox binds (sandbox._install_root), so the mode file
    stays visible; the symlink lives in fake-install/ and that directory is prepended to PATH for this test only
    (offline_guard's monkeypatch restores PATH at teardown). Both the lexical symlink and the realpath are registered
    for the D-guard exception."""
    root = Path(mode_file).parent
    target = root / 'claude'
    target.write_text(f'#!/bin/sh\nexec {PY} -B {FIXTURE} --mode-file {mode_file} "$@"\n', encoding='utf-8')
    target.chmod(0o755)
    install = root / 'fake-install'
    install.mkdir(parents=True, exist_ok=True)
    launcher = install / 'claude'
    if launcher.is_symlink() or launcher.exists():
        launcher.unlink()
    launcher.symlink_to(target.resolve())
    FAKE_TARGET['realpath'] = str(target.resolve())
    FAKE_LAUNCHERS.update({str(launcher), str(target.resolve())})
    os.environ['PATH'] = os.pathsep.join([str(install), os.environ.get('PATH', '')])
    return kb.SESSION_TEMPLATE, kb.RESUME_TEMPLATE


# ---- T1 constants --------------------------------------------------------

def test_t1_pinned_model_is_hyphenated_and_alternates_rejected():
    assert kb.PINNED_MODEL == 'claude-opus-5-5'
    assert kb.PINNED_MODEL != 'claude-opus-5.5'  # negative-vector
    assert kb.PINNED_MODEL != 'claude-opus-5-5[1m]'


def test_t1_effort_env_and_allowed_tools_pinned():
    assert kb.PINNED_EFFORT_ENV == ('MAX_THINKING_TOKENS', '32000')
    assert kb.ALLOWED_TOOLS == frozenset({'Read', 'Glob', 'Grep', 'Skill'})


def test_t1_no_dotted_model_outside_marked_negative_vectors():
    dotted = 'claude-opus-5' + '.5'
    for name in ('k_task8_binding.py', 'test_k_task8_binding.py'):
        for number, line in enumerate((HERE / name).read_text(encoding='utf-8').splitlines(), 1):
            if dotted in line:
                assert 'negative-vector' in line, f'{name}:{number} uses the dotted id'


# ---- T2 identity and immutability -----------------------------------------

def test_t2_mutated_control_file_is_unexpected_permission(tmp_path):
    k = tmp_path / 'K'
    for rel in ('skills/scieflow-menu/SKILL.md', 'skills/workspace-sync/SKILL.md',
                'scripts/hooks/dvc-sync-reminder.py', 'config/agents.yml', 'config/sandbox.yml',
                '.claude/settings.json'):
        path = k / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'original {rel}\n', encoding='utf-8')
    before = kb.control_snapshot(k, tmp_path / 'run', binary=None)
    (k / 'skills/workspace-sync/SKILL.md').write_text('mutated\n', encoding='utf-8')
    after = kb.control_snapshot(k, tmp_path / 'run', binary=None)
    assert kb.compare_snapshots(before, after) == ['unexpected-permission:skills/workspace-sync/SKILL.md']


def test_t2_tree_mutation_is_identity_drift_and_pycache_is_excluded(tmp_path):
    tree = tmp_path / 'src'
    (tree / 'pkg').mkdir(parents=True)
    (tree / 'pkg' / 'a.py').write_text('x = 1\n', encoding='utf-8')
    digest = kb.tree_digest(tmp_path, ['src'])
    (tree / 'pkg' / '__pycache__').mkdir()
    (tree / 'pkg' / '__pycache__' / 'a.cpython-312.pyc').write_bytes(b'bytecode')
    assert kb.tree_digest(tmp_path, ['src']) == digest
    (tree / 'pkg' / 'a.py').write_text('x = 2\n', encoding='utf-8')
    assert kb.tree_digest(tmp_path, ['src']) != digest


# ---- T3 setup and argv ------------------------------------------------------

def test_t3_setup_never_launches_and_writes_override(k_project, tmp_path, monkeypatch):
    launches = []
    monkeypatch.setattr(jobs, 'start', lambda *a, **kw: launches.append(a) or (_ for _ in ()).throw(
        AssertionError('launch during setup')))
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    assert launches == []
    config = (k_project.run_dir(kb.RUN_SLUG) / 'config.yml').read_text(encoding='utf-8')
    assert 'claude' in config and 'claude-opus-5-5' in config
    assert kb.run_override_ok(k_project)


def test_t3_expected_argv_shapes_per_probe():
    # R10-2: no env prefix anywhere on the turn path (production refuses one, A7)
    argv = kb.expected_argv('P1', session=None, model='claude-opus-5-5')
    assert argv[:1] == ['claude']
    assert argv[1:] == ['-p', '--output-format=stream-json', '--verbose',
                        '--dangerously-skip-permissions', '--tools=Read,Glob,Grep,Skill',  # R13-1
                        '--model', 'claude-opus-5-5', '{prompt}']
    resumed = kb.expected_argv('P3', session='S1', model='claude-opus-5-5')
    assert resumed[-5:] == ['--model', 'claude-opus-5-5', '--resume', 'S1', '{prompt}']
    assert '--disable-slash-commands' in kb.expected_argv('P5', session=None,
                                                          model='claude-opus-5-5')
    assert '--disable-slash-commands' not in argv


# ---- T4 binding drives real service.say -------------------------------------

def test_t4_marked_turn_refused_without_binding_with_production_message(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume), bind_support=False)
    # R14-2: the production refusal before launch is a named refusal; its own message stays on the cause chain.
    with pytest.raises(kb.ProbeRefused, match='^service-refused-before-launch$') as info:
        binding.run_probe('P1')
    assert isinstance(info.value.__cause__, service.ServiceError)
    assert 'Incomplete, disabled, unsupported or unpinned' in str(info.value.__cause__)


def test_t4_p6_constructor_refusal_has_zero_launches(k_project):
    with pytest.raises(ValueError, match='marked|command'):
        HumanSubmission('/scieflow-not-a-command', 'harness-command', '0')


def test_t4_marked_turn_reaches_job_record_with_binding(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    report = binding.run_probe('P1')
    assert report['job_state'] == 'done'
    assert report['interception']['evidence'], 'skill interception must be recorded'


# ---- T5 confinement and policy binding ------------------------------------

def test_t5_certified_policies_empty_before_during_and_after_exception(k_project):
    assert harness_commands.CERTIFIED_POLICIES == ()
    with pytest.raises(RuntimeError):
        with kb.support_bound(kb.expected_identity_for_fixture(k_project, session_template='x',
                                                               resume_template='y')):
            assert harness_commands.CERTIFIED_POLICIES == ()
            raise RuntimeError('boom')
    assert harness_commands.CERTIFIED_POLICIES == ()


def test_t5_other_identity_refused_and_original_restored(k_project, tmp_path):
    _fixture_templates(_mode_file(tmp_path))  # R10: the fake claude launcher must be on PATH for capture
    expected = kb.expected_identity_for_fixture(k_project, session_template='a', resume_template='b')
    other = dataclasses.replace(expected, model='claude-sonnet-5-5')
    original = harness_commands.support_for
    with kb.support_bound(expected):
        assert harness_commands.support_for(expected).enabled is True
        # R12-2: a mismatch is a named refusal, never a SupportStatus(False) and never a fall-through
        with pytest.raises(kb.ProbeRefused, match=r'^binding-identity-mismatch:model$'):
            harness_commands.support_for(other)
    assert harness_commands.support_for is original


def test_t5_unsandboxed_run_refused_before_launch(k_project, tmp_path, monkeypatch):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    _unsandbox(k_project, kb.RUN_SLUG)
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    with pytest.raises(kb.ProbeRefused, match='unsandboxed'):
        binding.run_probe('P1')


# ---- T6 live invoker controls ---------------------------------------------

def test_t6_p2_stdin_forced_only_for_p2_and_p5_flag_only_for_p5(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    p2 = binding.run_probe('P2')
    assert p2['launch']['stdin_used'] is True
    assert p2['launch']['stdin_sha256'] == p2['composed_sha256']
    assert all(not token.startswith('/scieflow') for token in p2['launch']['argv'])
    p1 = binding.run_probe('P1')
    assert p1['launch']['stdin_used'] is False
    assert '--disable-slash-commands' not in p1['launch']['argv']
    p5 = binding.run_probe('P5')
    assert '--disable-slash-commands' in p5['launch']['argv']


def test_t6_wrappers_restored_after_exception(k_project, tmp_path):
    originals = (agent_run.resolve_turn, agent_run.prepare_turn, jobs.start)
    with pytest.raises(RuntimeError):
        with kb.probe_controls('P2', {}, project=k_project, truncate_after_s=2.0, cancel_after_s=2.0,
                               cancel_enabled=False, truncate_enabled=False):
            raise RuntimeError('boom')
    assert (agent_run.resolve_turn, agent_run.prepare_turn, jobs.start) == originals


def test_t6_prepare_wrapper_keeps_identity_and_revision(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    report = binding.run_probe('P5')
    assert report['harness_identity'] == json.loads(json.dumps(report['harness_identity']))
    assert report['policy_revision'] == 'k-task8-g4-probe'


def test_t6_p7_truncation_and_p8_cancel_states(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('hang\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    p7 = binding.run_probe('P7', truncate_after_s=2.0)
    assert p7['job_state'] == 'timeout' and p7['recovery_required'] is True and p7['auto_resends'] == 0
    p8 = binding.run_probe('P8', cancel_after_s=2.0)
    assert p8['job_state'] == 'cancelled' and p8['recovery_required'] is True


# ---- T7 raw capture and filesystem effects ---------------------------------

def test_t7_derived_effects_accept_real_turn_and_reject_extra_files(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    report = binding.run_probe('P1')
    assert kb.derived_effect_refusals(report) == []
    extra = dict(report, fs_after=dict(report['fs_after'], **{'logs/extra.txt': 'x' * 64}))
    assert kb.derived_effect_refusals(extra) == ['unexpected-file:logs/extra.txt']
    changed = dict(report, fs_after=dict(report['fs_after'], **{'charter.yml': 'y' * 64}),
                   fs_before=dict(report['fs_before'], **{'charter.yml': 'z' * 64}))
    assert kb.derived_effect_refusals(changed) == ['unexpected-file:charter.yml']


def test_t7_raw_bytes_hashes_usage_and_unexpected_file(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(k_project,
                              session_template=session, resume_template=resume))
    report = binding.run_probe('P1')
    raw = Path(report['raw_stream']['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == report['raw_stream']['sha256']
    assert report['usage'] == {'input_tokens': 11, 'output_tokens': 7, 'cost_usd': 0.0}
    assert report['composed_charter_occurrences'] == 1
    (k_project.run_dir(kb.RUN_SLUG) / 'stray.txt').write_text('x', encoding='utf-8')
    assert kb.filesystem_refusals(report) == ['unexpected-file:stray.txt']


# ---- integration (impl checkpoint steps 1-5; operator ruling D2 on budget.yml) ------------

HARNESS_PATH = W.parents[1] / 'tools' / 'k_harness_capability_probe.py'
FIXTURE_SHA = kb.sha256_file(FIXTURE)
AGENTS_SHA = kb.sha256_file(W / 'config' / 'agents.yml')
PIN = (str(FIXTURE), FIXTURE_SHA)  # the pinned claude for offline runs is the fixture binary (S5c)
GEN2_SHA = 'a' * 64  # placeholder bound into checkpoints in tests only


def _gate():
    spec = importlib.util.spec_from_file_location('k_harness_capability_probe', HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def _probe_setup(project, tmp_path, mode='normal'):
    mode_file = _mode_file(tmp_path)
    mode_file.write_text(mode + '\n', encoding='utf-8')
    session, resume = _fixture_templates(mode_file)
    kb.setup_probe_run(project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    expected = kb.expected_identity_for_fixture(project, session_template=session, resume_template=resume)
    return kb.ProbeBinding(project, expected=expected, claude_binary=FIXTURE), mode_file


def _modes(mode_file, modes):
    def mode_for(probe):
        mode_file.write_text(modes.get(probe, 'normal') + '\n', encoding='utf-8')
    return mode_for


def _pause(binding, gate, mode_file, tmp_path):
    ckpt = tmp_path / 'checkpoint.json'
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}),
                          claude_pin=PIN, agents_sha256=AGENTS_SHA,
                          truncate_after_s=2.0, cancel_after_s=2.0, checkpoint_path=ckpt)
    return out, ckpt


def _spy_starts(monkeypatch):
    calls = []
    original = jobs.start

    def spy(*args, **kwargs):
        calls.append(args[1][:1] if len(args) > 1 else None)
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, 'start', spy)
    return calls


BUDGET_BEFORE = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 0\n  wall_minutes: 0.5\nstarted: x\n'


def test_t7b_budget_rule_allows_only_spent_growth_and_records_values():
    after = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 1\n  wall_minutes: 0.9\nstarted: x\n'
    effect = kb.budget_effect(BUDGET_BEFORE, after)
    assert effect['reasons'] == []
    assert effect['before'] == {'iterations': 0, 'wall_minutes': 0.5}
    assert effect['after'] == {'iterations': 1, 'wall_minutes': 0.9}


@pytest.mark.parametrize('after, reason', [
    ('budgets:\n  max_iterations: 5\nspent:\n  iterations: 1\n  wall_minutes: 0.9\nstarted: x\n',
     'unexpected-file:budget.yml:budgets'),
    ('budgets:\n  max_iterations: 0\nspent:\n  iterations: -1\n  wall_minutes: 0.9\nstarted: x\n',
     'unexpected-file:budget.yml:spent.iterations'),
    ('budgets:\n  max_iterations: 0\nspent:\n  iterations: 1\n  wall_minutes: -1\nstarted: x\n',
     'unexpected-file:budget.yml:spent.wall_minutes'),
    ('budgets:\n  max_iterations: 0\nspent:\n  iterations: 1\n  wall_minutes: 0.9\nstarted: x\nextra: 1\n',
     'unexpected-file:budget.yml:extra'),
    ('budgets:\n  max_iterations: 0\nspent:\n  iterations: 1\n  wall_minutes: 0.9\n',
     'unexpected-file:budget.yml:started'),
])
def test_t7b_budget_rule_refuses_every_other_change(after, reason):
    assert reason in kb.budget_effect(BUDGET_BEFORE, after)['reasons']


def test_t7b_budget_counter_may_not_decrease():
    before = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 2\n  wall_minutes: 0.5\nstarted: x\n'
    after = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 1\n  wall_minutes: 0.5\nstarted: x\n'
    assert kb.budget_effect(before, after)['reasons'] == ['unexpected-file:budget.yml:spent.iterations']


def test_t7b_budget_is_outside_the_digest_and_charter_stays_pinned():
    assert 'budget.yml' not in kb.RUN_FILES
    assert 'charter.yml' in kb.RUN_FILES


def test_t7d_budget_change_needs_a_passing_structural_record():
    report = {'fs_before': {'budget.yml': 'a' * 64}, 'fs_after': {'budget.yml': 'b' * 64}, 'budget': {'reasons': []}}
    assert kb.derived_effect_refusals(report) == []
    refused = dict(report, budget={'reasons': ['unexpected-file:budget.yml:budgets']})
    assert kb.derived_effect_refusals(refused) == ['unexpected-file:budget.yml:budgets']
    assert kb.derived_effect_refusals(dict(report, budget=None)) == [
        'unexpected-file:budget.yml:no-structural-check']


def test_t7c_k_side_refusals_allow_only_admission_locks_and_clean_home():
    before = {'scieflow': [], 'home_probes': [], 'journals': 'h1', 'pycache': []}
    lock = 'turn-admission/v1-' + 'a' * 64 + '.lock'
    ok = dict(before, scieflow=[lock])
    assert kb.k_side_refusals(before, ok) == []
    assert kb.k_side_refusals(before, dict(before, scieflow=['other.txt'])) == ['unexpected-file:.scieflow/other.txt']
    assert kb.k_side_refusals(before, dict(ok, home_probes=['.scieflow-sandbox-probe-x'])) == [
        'unexpected-file:$HOME/.scieflow-sandbox-probe-x']
    assert kb.k_side_refusals(before, dict(ok, journals='h2')) == ['unexpected-file:config/journals']
    assert kb.k_side_refusals(before, dict(ok, pycache=['src/scieflow/x/__pycache__'])) == [
        'unexpected-file:src/scieflow/x/__pycache__']


def test_t2b_probe_report_carries_immutability_and_no_permission_change(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    assert report['immutability']['before'] == report['immutability']['after']
    assert 'run:charter.yml' in report['immutability']['before']
    assert 'run:budget.yml' not in report['immutability']['before']
    assert report['permission_changes'] == []
    assert report['effect_reasons'] == []
    assert report['budget']['reasons'] == []


def test_t4b_p6_refused_by_production_constructor_with_zero_launches(k_project, tmp_path, monkeypatch):
    def no_launch(*args, **kwargs):
        raise AssertionError('launch during P6')

    monkeypatch.setattr(jobs, 'start', no_launch)
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity(
        executable='/usr/bin/env', session_template='x', resume_template='y',
        model=kb.PINNED_MODEL, cwd=k_project.root))
    report = binding.run_probe('P6')
    assert report['job_started'] is False and report['live_calls'] == 0
    assert report['refusal']['stage'] == 'pre-attempt'
    assert report['refusal']['type'] == 'ValueError' and report['refusal']['message']


def test_t6c_adapter_maps_shape_and_identity_and_flags_mismatch(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    raw = binding.run_probe('P1')
    gate = _gate()
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    report = kb.to_harness_report(raw, agents_sha256=AGENTS_SHA)
    assert gate.validate_report(report, expected, 'P1') == [], [
        k for k, v in report['identity'].items() if v in (None, '')]
    assert report['tool_inputs'] and report['marked_input'] == kb.CANDIDATE
    bad = dict(report, identity=dict(report['identity'], cwd='/elsewhere'))
    assert gate.validate_report(bad, expected, 'P1') == ['mismatched-identity']


def test_t6b_p8_state_read_accepts_one_cancelled_bump_equal_to_turn_final(k_project, tmp_path):
    """DD7 P8 state read, per operator ruling 2026-10-14 (approvals/k-task8-binding-dd7-operator-ruling-20261014.md,
    option a; card t_77d80646): the cancelled turn's finalize bumps the selection revision exactly once
    (conversation.py finalize), so revision_after == revision_before + 1 and equals the turn's final_revision.
    Production is not changed; the rule is recorded under DD7 in the binding report."""
    binding, _ = _probe_setup(k_project, tmp_path, 'hang')
    gate = _gate()
    raw = binding.run_probe('P8', cancel_after_s=2.0)
    state = raw['state_read']
    assert state['in_flight'] is False and state['queued'] is False and state['replayed'] is False
    assert state['agent'] == 'claude' and state['revision_before'] is not None
    assert int(state['revision_after']) == int(state['revision_before']) + 1
    assert state['final_revision'] == state['revision_after']
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    assert gate.validate_report(kb.to_harness_report(raw, agents_sha256=AGENTS_SHA),
                                expected, 'P8') == []


def test_t7e_file_written_during_a_turn_becomes_an_effect_refusal(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    real_say = kb.service.say

    def say_with_stray(project, slug, message, **kwargs):
        (project.run_dir(kb.RUN_SLUG) / 'stray-during-turn.txt').write_text('x', encoding='utf-8')
        return real_say(project, slug, message, **kwargs)

    monkeypatch.setattr(kb.service, 'say', say_with_stray)
    report = binding.run_probe('P1')
    assert 'unexpected-file:stray-during-turn.txt' in report['effect_reasons']


def test_t8_usage_limit_is_seen_by_the_harness_gate(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'usage-limit')
    gate = _gate()
    raw = binding.run_probe('P1')
    limit = gate.classify_usage_limit(raw['stream_text'], raw['stderr_text'])
    assert limit['limited'] is True and limit['reset_epoch'] == 1760000000


def test_t8_pause_writes_checkpoint_and_stops_without_retry(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out, ckpt = _pause(binding, gate, mode_file, tmp_path)
    assert out['status'] == 'paused-usage-limit'
    data = json.loads(ckpt.read_text(encoding='utf-8'))
    assert data['schema'] == 'k-task8-binding-checkpoint-v1' and data['status'] == 'paused-usage-limit'
    assert data['limited_probe'] == 'P1' and [r['probe'] for r in data['reports']] == ['P1']
    assert data['reports'][0]['status'] == 'not-run-usage-limited'
    assert data['dd1_sha256'] == gate.dd1_receipt_digests()
    assert len(jobs.list_jobs(k_project, binding.run_dir)) == 1


def test_t8_resume_refused_without_bound_receipt_and_for_mutated_state(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    _out, ckpt = _pause(binding, gate, mode_file, tmp_path)
    launches = _spy_starts(monkeypatch)
    for receipt in (None, 'f' * 64):
        out = kb.resume_sequence(binding, gate, checkpoint_path=ckpt, receipt_checkpoint_sha256=receipt,
                                 mode_for=_modes(mode_file, {}), claude_pin=PIN,
                                 agents_sha256=AGENTS_SHA, truncate_after_s=2.0, cancel_after_s=2.0)
        assert out['status'] == 'refused:resume-receipt-not-bound-to-checkpoint'
    assert launches == []
    digest = gate.sha256_file(ckpt)
    session = json.loads(ckpt.read_text(encoding='utf-8'))['session']
    config_sha = gate.sha256_file(binding.run_dir / 'config.yml')
    assert 'checkpoint-revision-changed' in gate.resume_refusals(
        ckpt, config_sha256=config_sha, revision='99', session=session, job_running=False,
        receipt_checkpoint_sha256=digest)
    assert 'checkpoint-config-changed' in gate.resume_refusals(
        ckpt, config_sha256='0' * 64, revision=binding.revision(), session=session, job_running=False,
        receipt_checkpoint_sha256=digest)
    assert 'checkpoint-job-running' in gate.resume_refusals(
        ckpt, config_sha256=config_sha, revision=binding.revision(), session=session, job_running=True,
        receipt_checkpoint_sha256=digest)


def test_t8_resume_continues_without_reinvoking_limited_probe(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    _out, ckpt = _pause(binding, gate, mode_file, tmp_path)
    launches = _spy_starts(monkeypatch)
    out = kb.resume_sequence(binding, gate, checkpoint_path=ckpt,
                             receipt_checkpoint_sha256=gate.sha256_file(ckpt),
                             mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                             claude_pin=PIN, agents_sha256=AGENTS_SHA,
                             truncate_after_s=2.0, cancel_after_s=2.0)
    assert out['status'] == 'attempted-all'  # every remaining probe attempted; certify (not status) decides the matrix
    statuses = {r['probe']: r['status'] for r in out['reports']}
    assert statuses['P1'] == 'not-run-usage-limited'
    assert statuses['P3'] == 'not-run-dependency' and statuses['P4'] == 'not-run-dependency'
    assert [statuses[p] for p in ('P2', 'P5', 'P6', 'P7', 'P8')] == ['usable'] * 5  # P8: DD7 ruling 2026-10-14
    assert len(launches) == 4  # P2, P5, P7, P8: P1 is never re-invoked; P6 never launches
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    certified = gate.certify(out['reports'], expected, True)
    assert 'not-run-dependency:P3' in certified['reasons'] and certified['certified'] is False
    assert 'state-read-failed:P8' not in certified['reasons']


def test_t9_run_sequence_consults_the_ceiling_before_any_launch(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    launches = _spy_starts(monkeypatch)
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), claude_pin=PIN,
                          agents_sha256=AGENTS_SHA, truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'c.json',
                          ceiling_state={'invocations': 8, 'completing': 0, 'active_s': 0.0})
    assert out['status'] == 'refused:ceiling-would-be-exceeded:invocations'
    assert launches == []


def test_t10_offline_matrix_passes_the_release_gate_and_only_origin_refusals_remain(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    # C11: reports are pinned under the reviews root, named for their role, and carry every DD1 digest.
    reviews_root = tmp_path / 'reviews'
    reviews_root.mkdir()
    monkeypatch.setattr(gate, 'REVIEWS_ROOT', reviews_root)
    dd1_digests = {rel: gate.sha256_file(gate.dd1_path(rel)) for rel in gate.DD1_FILES}
    body = 'VERDICT: PASS\n\nsynthetic test-only review\n' + ''.join(
        f'{rel} {dd1_digests[rel]}\n' for rel in gate.DD1_FILES)
    reviews = {}
    for role, name in (('implementation_spec_review', 'k-task8-spec-review.md'),
                       ('code_security_review', 'k-task8-code-security-review.md')):
        path = reviews_root / name
        path.write_text(body, encoding='utf-8')
        reviews[role] = {'path': str(path), 'sha256': gate.sha256_file(path)}
    receipt = {'schema': gate.RECEIPT_SCHEMA, 'decisions_sha256': gate.DECISIONS_SHA256,
               'plan_sha256': gate.PROBE_PLAN_SHA256, 'scope_review_sha256': gate.SCOPE_REVIEW_SHA256,
               'amendment_sha256': gate.AMENDMENT_SHA256, 'binding_plan_sha256': gate.BINDING_PLAN_SHA256,
               'probe_order': list(gate.PROBE_ORDER), 'human_confirmations': dict(gate.HUMAN_CONFIRMATIONS),
               'approver': 'human', 'released_by': 'operator',
               'dd1_sha256': gate.dd1_receipt_digests(),
               'review_reports': reviews, 'resume_checkpoint_sha256': None}
    receipt_path = tmp_path / 'receipt.json'
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    assert gate.receipt_refusals(receipt_path) == []
    assert kb.dd1_digests() == {rel: gate.sha256_file(W / rel) for rel in kb.DD1_FILES}
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          claude_pin=PIN, agents_sha256=AGENTS_SHA,
                          truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'c.json')
    assert out['status'] == 'attempted-all'
    assert [r['probe'] for r in out['reports']] == list(kb.PROBE_ORDER)
    assert all(r['status'] == 'usable' for r in out['reports'])  # DD7 P8 ruling 2026-10-14: P8 now usable
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    certified = gate.certify(out['reports'], expected, True)
    assert set(certified['reasons']) == {f'synthetic-origin-refused:{p}' for p in kb.PROBE_ORDER}
    assert certified['policy_written'] is False


# ==== r4 (impl spec review r3, findings S1-S12): RED tests written before the r4 production edits ====
# Each test is named by its finding (sN_...). They exercise real config/protection values where the
# review requires it; no test stubs the function it is checking.

import types  # noqa: E402
import yaml  # noqa: E402
from scieflow.core import sandbox  # noqa: E402
from scieflow.core.run import conversation  # noqa: E402



class FakeClock:
    def __init__(self, step):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def _unsandbox(project, slug):
    path = project.root / 'config' / 'sandbox.yml'
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    data['unsandboxed_runs'] = [{'slug': slug, 'reason': 'r4 test: real config entry'}]
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding='utf-8')


# S3: deletions are effects; budget.yml gets no structural exemption for a missing file.
def test_s3_deleted_budget_yml_is_unexpected_file():
    report = {'fs_before': {'budget.yml': 'a' * 64, 'conversation.yml': 'c' * 64},
              'fs_after': {'conversation.yml': 'c' * 64}, 'budget': {'reasons': []}}
    assert kb.derived_effect_refusals(report) == ['unexpected-file:budget.yml:deleted']


def test_s3_deleted_arbitrary_run_file_is_unexpected_file():
    assert kb.derived_effect_refusals({'fs_before': {'notes.txt': 'n' * 64}, 'fs_after': {}}) == [
        'unexpected-file:notes.txt:deleted']


def test_s3_deleted_charter_refused_on_derived_and_permission_paths():
    assert 'unexpected-file:charter.yml:deleted' in kb.derived_effect_refusals(
        {'fs_before': {'charter.yml': 'x' * 64}, 'fs_after': {}})
    assert kb.compare_snapshots({'run:charter.yml': 'x' * 64}, {'run:charter.yml': None}) == [
        'unexpected-permission:run:charter.yml']


def test_s3_budget_spent_key_added_or_removed_is_refused():
    added = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 0\n  wall_minutes: 0.5\n  extra: 1\nstarted: x\n'
    removed = 'budgets:\n  max_iterations: 0\nspent:\n  iterations: 0\nstarted: x\n'
    assert kb.budget_effect(BUDGET_BEFORE, added)['reasons'] == ['unexpected-file:budget.yml:spent-keys']
    assert kb.budget_effect(BUDGET_BEFORE, removed)['reasons'] == ['unexpected-file:budget.yml:spent-keys']


# S4: confinement is asserted before and after each launch, using real protection values.
def test_s4_real_unsandboxed_entry_refuses_before_any_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    _unsandbox(k_project, kb.RUN_SLUG)
    launches = _spy_starts(monkeypatch)
    with pytest.raises(kb.ProbeRefused, match='unsandboxed'):
        binding.run_probe('P1')
    assert launches == []


def test_s4_protection_is_captured_before_the_launch_call(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    order = []
    real_protection = sandbox.resolve_turn_protection
    real_start = jobs.start

    def protection_spy(*args, **kwargs):
        order.append('protection')
        return real_protection(*args, **kwargs)

    def start_spy(*args, **kwargs):
        order.append('start')
        return real_start(*args, **kwargs)

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', protection_spy)
    monkeypatch.setattr(jobs, 'start', start_spy)
    report = binding.run_probe('P1')
    assert order.index('protection') < order.index('start')
    assert report['effect_reasons'] == []
    run = k_project.run_dir(kb.RUN_SLUG).resolve()
    assert report['launch']['sandboxed'] is True
    assert report['launch']['writable'] == [str(run), str(k_project.root / 'config' / 'journals')]


def test_s4_confinement_refusals_use_real_protection_values(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    prot = sandbox.resolve_turn_protection(k_project, run_dir=run)
    assert kb.confinement_refusals(prot, run) == []
    wrong = dataclasses.replace(prot, writable=(run.resolve(), k_project.root / 'workspace'))
    assert kb.confinement_refusals(wrong, run) == ['writable-mismatch']
    _unsandbox(k_project, kb.RUN_SLUG)
    off = sandbox.resolve_turn_protection(k_project, run_dir=run)
    assert off.sandbox_enabled is False
    assert kb.confinement_refusals(off, run) == ['sandbox-disabled']


def test_s4_launch_refusals_check_sandboxed_flag_and_writable_set():
    assert kb.launch_refusals({'sandboxed': False, 'writable': ['a']}, ['a']) == ['launch-unsandboxed']
    assert kb.launch_refusals({'sandboxed': True, 'writable': ['b']}, ['a']) == ['launch-writable-mismatch']
    assert kb.launch_refusals({'sandboxed': True, 'writable': ['a']}, ['a']) == []


# S5: real templates through production resolve/prepare, binary in the snapshot, pinned claude identity.
def _argv_for(project, probe, session):
    spec = kb.PROBE_SPEC[probe]
    run = project.run_dir(kb.RUN_SLUG)
    seen = {}
    message = kb.CANDIDATE if spec['marked'] else 'ordinary chat control'
    with kb.probe_controls(probe, seen, project=project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=run):
        resolved = agent_run.resolve_turn(project, 'claude', run_dir=run, session=session)
        if spec['stdin']:
            resolved = dataclasses.replace(resolved, prompt_argv_limit=1)
        expected = kb.expected_identity_for_fixture(project, session_template=kb.SESSION_TEMPLATE,
                                                    resume_template=kb.RESUME_TEMPLATE, model=kb.PINNED_MODEL)
        assert expected.executable == FAKE_TARGET['realpath']  # R12-4: test-known target, not production's value
        with kb.support_bound(expected):
            rev = conversation.selection_revision(conversation.read(run))
            submission = HumanSubmission(message, 'harness-command' if spec['marked'] else 'chat',
                                         rev if spec['marked'] else None)
            prepared = agent_run.prepare_turn(resolved, submission)
    return list(prepared.argv), prepared.composed_bytes.decode('utf-8'), resolved.harness_identity.executable


def test_s5a_real_templates_produce_expected_argv_token_for_token(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    _fixture_templates(mode)  # R10: the fake claude launcher must be on PATH for capture
    kb.setup_probe_run(k_project, session_template=kb.SESSION_TEMPLATE, resume_template=kb.RESUME_TEMPLATE,
                       timeout_min=1, model=kb.PINNED_MODEL)
    for probe, session in (('P1', None), ('P3', 'S1'), ('P4', 'S1'), ('P2', None), ('P5', None),
                           ('P7', None), ('P8', None)):
        argv, composed, _executable = _argv_for(k_project, probe, session)
        expected = kb.expected_argv(probe, session=session, model=kb.PINNED_MODEL)
        # R12-4: production's argv[0] is the LEXICAL launcher (prepare_turn execution_path), not the realpath
        # identity executable; the lexical path is test-known (the symlink the test created)
        expected[0] = str(mode.parent / 'fake-install' / 'claude')
        if expected[-1] == '{prompt}':
            assert argv == expected[:-1] + [composed], probe
        else:
            assert argv == expected, probe


def test_s5b_snapshot_carries_the_claude_binary_and_a_changed_binary_is_refused(tmp_path):
    k = tmp_path / 'K'
    run = tmp_path / 'run'
    run.mkdir()
    binary = tmp_path / 'claude'
    binary.write_bytes(b'version-one')
    before = kb.control_snapshot(k, run, binary=binary)
    assert before['binary'] == hashlib.sha256(b'version-one').hexdigest()
    binary.write_bytes(b'version-two')
    after = kb.control_snapshot(k, run, binary=binary)
    assert kb.compare_snapshots(before, after) == ['unexpected-permission:binary']


def test_s5b_probe_report_snapshot_binary_is_the_bound_binary(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    assert report['immutability']['before']['binary'] == FIXTURE_SHA


def test_s5c_claude_pin_mismatch_is_refused_before_any_launch(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    launches = _spy_starts(monkeypatch)
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=('/elsewhere/claude', 'f' * 64))
    assert out['status'] == 'refused:identity-drift:claude'
    assert launches == []


# S6: one active-time clock around each whole probe call (verify, evidence and resets included).
def test_s6_whole_probe_call_is_counted_on_the_clock(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          clock=FakeClock(12.0))
    live = [r for r in out['reports'] if r['probe'] != 'P6']
    assert out['ceiling']['active_s'] == 12.0 * len(live)


def test_s6_checkpoint_records_pause_timestamp(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'c.json', now=lambda: 1234.5)
    assert out['status'] == 'paused-usage-limit'
    assert json.loads((tmp_path / 'c.json').read_text(encoding='utf-8'))['paused_at'] == 1234.5


# S7: P6 counts what the production constructor actually launches.
def test_s7_p6_measures_zero_launches_for_the_real_constructor(k_project):
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity(
        executable='/usr/bin/env', session_template='x', resume_template='y',
        model=kb.PINNED_MODEL, cwd=k_project.root))
    report = binding._p6_refusal()
    assert report['live_calls'] == 0 and report['job_started'] is False


def test_s7_p6_constructor_that_launches_is_counted_and_refused(k_project):
    def launching(message, mode, revision):
        subprocess.Popen([PY, '-c', 'pass']).wait()
        raise ValueError('refused after launching')

    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity(
        executable='/usr/bin/env', session_template='x', resume_template='y',
        model=kb.PINNED_MODEL, cwd=k_project.root))
    report = binding._p6_refusal(constructor=launching)
    assert report['live_calls'] >= 1
    gate = _gate()
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    assert 'unknown-command-not-refused-before-attempt' in gate.validate_report(
        dict(report, origin='live-g4', identity=None), expected, 'P6')


# S2: persisted evidence per probe, fresh directory, bytes equal to the source.
def test_s2_evidence_copies_match_source_bytes_and_shas(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    evidence = tmp_path / 'ev' / 'P1'
    desc = kb.write_probe_evidence(evidence, report)
    raw = Path(report['raw_stream']['path']).read_bytes()
    assert (evidence / 'stream.jsonl').read_bytes() == raw
    assert desc['files']['stream.jsonl'] == hashlib.sha256(raw).hexdigest()
    composed = Path(report['composed_path'])
    assert (evidence / 'composed.md').read_bytes() == composed.read_bytes()
    assert desc['files']['composed.md'] == report['composed_sha256']
    assert report['composed_charter_occurrences'] == 1


def test_s2_usage_is_null_without_result_event_and_budget_sits_beside_usage(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path, 'hang')
    report = binding.run_probe('P7', truncate_after_s=2.0)
    desc = kb.write_probe_evidence(tmp_path / 'ev7', report)
    usage = json.loads((tmp_path / 'ev7' / 'usage.json').read_text(encoding='utf-8'))
    assert usage['usage'] is None and desc['usage'] is None
    mode_file.write_text('normal\n', encoding='utf-8')
    ok = binding.run_probe('P1')
    kb.write_probe_evidence(tmp_path / 'ev1', ok)
    usage1 = json.loads((tmp_path / 'ev1' / 'usage.json').read_text(encoding='utf-8'))
    assert usage1['usage'] == {'input_tokens': 11, 'output_tokens': 7, 'cost_usd': 0.0}
    assert set(usage1['budget']) == {'before', 'after'}


def test_s2_existing_evidence_directory_is_refused(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    kb.write_probe_evidence(tmp_path / 'once', report)
    with pytest.raises(kb.ProbeRefused, match='evidence-dir-exists'):
        kb.write_probe_evidence(tmp_path / 'once', report)


def test_s2_evidence_records_launch_protection_and_discovery(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    kb.write_probe_evidence(tmp_path / 'ev', report)
    launch = json.loads((tmp_path / 'ev' / 'launch.json').read_text(encoding='utf-8'))
    assert launch['protection']['enabled'] is True and launch['launch']['sandboxed'] is True
    assert launch['launch']['env_has_effort'] is True and launch['launch']['effort_in_argv'] is False  # R10-3
    discovery = json.loads((tmp_path / 'ev' / 'discovery.json').read_text(encoding='utf-8'))
    assert set(discovery) >= {'CLAUDE.md', 'AGENTS.md', '.mcp.json', 'home-claude-settings'}


# S8: discovery inputs are evidence, recorded with presence and digest.
def test_s8_discovery_inputs_record_presence_and_digest(tmp_path):
    root = tmp_path / 'K'
    root.mkdir()
    (root / 'CLAUDE.md').write_text('claude\n', encoding='utf-8')
    home = tmp_path / 'home'
    (home / '.claude').mkdir(parents=True)
    (home / '.claude' / 'settings.json').write_text('{}', encoding='utf-8')
    found = kb.discovery_inputs(root, home)
    assert found['CLAUDE.md'] == hashlib.sha256(b'claude\n').hexdigest()
    assert found['AGENTS.md'] is None and found['.mcp.json'] is None
    assert list(found['home-claude-settings']) == ['settings.json']


# S9: the receipt binds the gen2 harness files too; pause computes the digests from the files.
def test_s9_pause_checkpoint_binds_gen2_files_computed_from_disk(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'c.json')
    data = json.loads((tmp_path / 'c.json').read_text(encoding='utf-8'))
    assert data['gen2_sha256'] == gate.gen2_digest()
    assert data['dd1_sha256'] == gate.dd1_receipt_digests()
    assert sorted(data['dd1_sha256']) == sorted(gate.DD1_FILES)


# S10: dotted model spelling is rejected in both places; wrapped prepare equals unwrapped prepare.
def test_s10_wrapped_prepare_turn_returns_the_same_identity_and_policy_as_unwrapped(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    original = agent_run.prepare_turn
    resolved = agent_run.resolve_turn(k_project, 'claude', run_dir=run, session=None)
    expected = kb.expected_identity_for_fixture(k_project, session_template=kb.SESSION_TEMPLATE,
                                                resume_template=kb.RESUME_TEMPLATE, model=kb.PINNED_MODEL)
    assert expected.executable == FAKE_TARGET['realpath']  # R12-4: test-known target, not production's value
    with kb.support_bound(expected):
        submission = HumanSubmission(kb.CANDIDATE, 'harness-command', conversation.selection_revision(
            conversation.read(run)))
        plain = original(resolved, submission)
        seen = {}
        with kb.probe_controls('P1', seen, project=k_project, truncate_after_s=2.0, cancel_after_s=2.0,
                               cancel_enabled=False, truncate_enabled=False, run_dir=run):
            wrapped = agent_run.prepare_turn(resolved, submission)
    assert wrapped.harness_identity == plain.harness_identity
    assert wrapped.policy_revision == plain.policy_revision == 'k-task8-g4-probe'


def test_s10_fixture_fast_mode_on_p7_is_stop_not_exercised(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'fast')
    gate = _gate()
    raw = binding.run_probe('P7', truncate_after_s=2.0)
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    assert 'stop-not-exercised' in gate.validate_report(kb.to_harness_report(raw, agents_sha256=AGENTS_SHA),
                                                        expected, 'P7')


def test_s10_fixture_other_skill_is_unexpected_skill_through_the_binding(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'other-skill')
    gate = _gate()
    raw = binding.run_probe('P1')
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    assert 'unexpected-skill' in gate.validate_report(kb.to_harness_report(raw, agents_sha256=AGENTS_SHA),
                                                     expected, 'P1')


def test_s10_fixture_fast_mode_on_p8_is_not_an_accepted_cancel(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'fast')
    gate = _gate()
    raw = binding.run_probe('P8', cancel_after_s=2.0)
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    assert 'stop-not-exercised' in gate.validate_report(kb.to_harness_report(raw, agents_sha256=AGENTS_SHA),
                                                        expected, 'P8')


# S11: the sequence reports attempted-all plus a boolean matrix_complete.
def test_s11_full_offline_matrix_reports_attempted_all_and_matrix_complete(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0)
    assert out['status'] == 'attempted-all' and out['matrix_complete'] is True


def test_s11_usage_limit_leaves_matrix_incomplete(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'c.json')
    assert out['status'] == 'paused-usage-limit' and out['matrix_complete'] is False


# S12 (binding side): a usage-limit pattern in assistant text does not pause; a structured error does.
def test_s12_assistant_text_about_rate_limits_does_not_pause_the_binding(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path, 'rate-talk')
    gate = _gate()
    raw = binding.run_probe('P1')
    assert gate.classify_usage_limit(raw['stream_text'], raw['stderr_text'])['limited'] is False


# r4 additional RED→GREEN tests (review mutants: evidence persistence in the sequence, S4 gates on both sides)
def test_s2_run_sequence_persists_evidence_per_probe_and_records_its_shas(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    evidence_root = tmp_path / 'evidence'
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          evidence_root=evidence_root)
    for probe in ('P1', 'P3', 'P4', 'P2', 'P5', 'P7', 'P8'):
        assert (evidence_root / probe / 'stream.jsonl').is_file(), probe
    p1 = next(r for r in out['reports'] if r['probe'] == 'P1')
    stream = (evidence_root / 'P1' / 'stream.jsonl').read_bytes()
    assert p1['evidence']['files']['stream.jsonl'] == hashlib.sha256(stream).hexdigest()


def test_s4_pre_launch_confinement_refusal_stops_the_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    binding.run_probe('P1')  # R14-2: a resume probe needs a session, so one usable first turn gives it one
    launches = _spy_starts(monkeypatch)
    monkeypatch.setattr(kb, 'confinement_refusals', lambda protection, run_dir: ['forced-by-test'])
    with pytest.raises(Exception, match='confinement-before-launch'):
        binding.run_probe('P4')  # the unmarked chat turn: prepare_turn does not check confinement itself
    assert launches == []


def test_s4_a_job_that_reports_unsandboxed_is_an_effect_refusal(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    real_start = jobs.start

    def unsandboxed_start(*args, **kwargs):
        job, proc = real_start(*args, **kwargs)
        job.sandboxed = False
        return job, proc

    monkeypatch.setattr(jobs, 'start', unsandboxed_start)
    report = binding.run_probe('P1')
    assert report['launch']['sandboxed'] is False
    assert 'unexpected-permission:sandbox:launch-unsandboxed' in report['effect_reasons']


# ----- r5 fix round (code/security review C2; brief R5-3) --------------------

def test_c2_a_poisoned_environment_key_is_refused_before_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    # R10-4: production's allow-list drops unknown keys before launch, so the poison is put onto the PRODUCTION
    # allow-list here; the binding's pinned allow-list (kb.ENV_ALLOW, fixed at import) must still refuse it.
    monkeypatch.setattr(sandbox, 'C12_KEPT', sandbox.C12_KEPT | {'ANTHROPIC_BASE_URL'})
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://attacker.example')
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    chain = _refusal_chain(info.value)  # bounded walker: fails, never loops, on a cyclic chain
    assert any('unexpected-permission:env:ANTHROPIC_BASE_URL' in text for text in chain), chain
    assert launches == []


def test_c2_launch_records_env_key_names_sorted_and_never_values(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    report = binding.run_probe('P1')
    keys = report['launch']['env_keys']
    assert keys == sorted(keys) and 'UV_CACHE_DIR' in keys
    assert all(isinstance(key, str) for key in keys)
    # R10-3: the effort key is the binding's own addition, recorded by name; it is the only refused-looking key.
    assert kb.env_refusal_keys(keys) == ['MAX_THINKING_TOKENS']


# ----- r5 fix round (code/security review C5, C14; brief R5-8) -----------------

def _chain_text(exc) -> list[str]:
    texts, seen = [], set()
    while exc is not None:  # production wraps a probe refusal in ServiceError; walk the cause chain
        assert id(exc) not in seen and len(texts) < 20, 'exception chain is cyclic or unbounded'
        seen.add(id(exc))
        texts.append(str(exc))
        exc = exc.__cause__ or exc.__context__
    return texts


def test_c5_launch_kwargs_that_differ_from_the_fresh_protection_are_refused(k_project, tmp_path, monkeypatch):
    # The protection admitted at prepare time is genuine; by the launch the fresh capture has drifted
    # (a writable root appeared). The kwargs production built from the admitted protection must be refused.
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    real_resolve = sandbox.resolve_turn_protection
    calls = {'n': 0}

    def drifting_resolve(project, **kwargs):
        calls['n'] += 1
        protection = real_resolve(project, **kwargs)
        if calls['n'] == 1:
            return protection
        writable = tuple(protection.writable) + (tmp_path,)
        masks = tuple(sandbox.masks_for(list(writable), run_dir=kwargs['run_dir']))
        return dataclasses.replace(protection, writable=writable, masks=masks)

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', drifting_resolve)
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    chain = _chain_text(info.value)
    assert any('unexpected-permission:sandbox:kwargs' in text for text in chain), chain
    assert launches == []


def test_c14_claude_identity_changed_after_a_launch_is_an_effect_refusal(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    launches = _spy_starts(monkeypatch)
    # Pinned until the first launch, then the binary is swapped: the post-launch recheck must see it.
    binding.observe_claude = lambda: PIN if not launches else ('/elsewhere/claude', 'f' * 64)
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0)
    first = [r for r in out['reports'] if r['probe'] == 'P1'][0]
    assert 'unexpected-permission:claude-identity' in first['effect_reasons']
    assert out['status'] != 'done'


# ----- r5 fix round SR2 / R5-14 (binding side): one report.json per probe, P6 included -----

def test_r5_14_each_probe_leaves_a_report_json_without_bytes_fields(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    evidence = tmp_path / 'evidence'
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          evidence_root=evidence)
    assert out['status'] == 'attempted-all'
    for probe in kb.PROBE_ORDER:
        record = json.loads((evidence / probe / 'report.json').read_text(encoding='utf-8'))
        assert not any(key.startswith('_bytes') for key in record)
        if probe == 'P6':
            assert 'refusal' in record
        else:
            assert {'state_read', 'immutability', 'effect_reasons', 'k_side_before', 'k_side_after'} <= set(record)


# ----- r5 fix round C9 / C12 (code review C9, C12; brief R5-9, R5-13) -----

@pytest.mark.parametrize('after, reason', [
    ('spent: [\n', 'unexpected-file:budget.yml:after-parse-error'),
    ('- a\n- b\n', 'unexpected-file:budget.yml:after-not-a-mapping'),
    (BUDGET_BEFORE.replace('wall_minutes: 0.5', 'wall_minutes: half'),
     'unexpected-file:budget.yml:spent.wall_minutes'),
    (BUDGET_BEFORE.replace('wall_minutes: 0.5', 'wall_minutes: .nan'),
     'unexpected-file:budget.yml:spent.wall_minutes:non-finite'),
    (BUDGET_BEFORE.replace('wall_minutes: 0.5', 'wall_minutes: .inf'),
     'unexpected-file:budget.yml:spent.wall_minutes:non-finite'),
])
def test_c9_budget_effect_is_total_and_names_the_reason(after, reason):
    assert reason in kb.budget_effect(BUDGET_BEFORE, after)['reasons']


def test_c9_a_malformed_budget_before_is_a_reason_not_an_exception():
    assert 'unexpected-file:budget.yml:before-parse-error' in kb.budget_effect('spent: [\n', BUDGET_BEFORE)['reasons']


def test_c9_an_exception_after_the_launch_keeps_evidence_counts_the_invocation_and_stops(k_project, tmp_path,
                                                                                         monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)

    def boom(self, probe, seen, **kwargs):
        raise RuntimeError('synthetic post-launch failure')

    monkeypatch.setattr(kb.ProbeBinding, '_report', boom)
    evidence = tmp_path / 'evidence'
    out = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0, evidence_root=evidence)
    assert out['status'] == 'stopped-after-launch' and out['ceiling']['invocations'] == 1
    assert any('synthetic post-launch failure' in reason for reason in out['reasons'])
    assert (evidence / 'P1' / 'stream.jsonl').is_file() and (evidence / 'P1' / 'stderr.txt').is_file()
    stopped = json.loads((evidence / 'stopped.json').read_text(encoding='utf-8'))
    assert stopped['probe'] == 'P1' and stopped['ceiling']['invocations'] == 1 and stopped['elapsed_s'] >= 0


def test_c12_a_second_launch_in_one_probe_is_refused_before_the_second_start(k_project, tmp_path, monkeypatch):
    # Production admission refuses a second say after a first launch ("turn attempt uncertain"), so the guard in the
    # start wrapper is exercised directly in the state a first launch leaves behind (seen['launches'] == 1).
    binding, _ = _probe_setup(k_project, tmp_path)
    reached = []

    def spy_start(*args, **kwargs):
        reached.append(args)
        raise AssertionError('the original jobs.start must not be reached')

    monkeypatch.setattr(kb.jobs, 'start', spy_start)
    seen = {'launches': 1}
    with kb.probe_controls('P1', seen, project=binding.project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=binding.run_dir):
        with pytest.raises(kb.ProbeRefused, match='second-launch'):
            kb.jobs.start(binding.project, ['true'], sandbox_writable=[], sandbox_mask=[], env={})
    assert reached == [] and seen['launches'] == 1


# ----- r5 fix round C15 / C16 (binding side; brief R5-15) -----

def test_c15_the_binding_docstring_no_longer_claims_it_has_no_live_invoker():
    assert 'has no live invoker' not in (kb.__doc__ or '')


def test_c16_listings_record_a_symlink_as_a_link_and_do_not_follow_it(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'target.txt').write_text('payload', encoding='utf-8')
    (run / 'ln.txt').symlink_to('target.txt')
    listing = kb.run_listing(run)
    assert listing['ln.txt'] == 'link:target.txt'
    digest = kb._files_digest(run)
    (run / 'target.txt').write_text('changed', encoding='utf-8')  # the link itself is not followed
    assert kb._files_digest(run) != digest  # the regular file changed, so the digest moves
    assert kb.run_listing(run)['ln.txt'] == 'link:target.txt'


def test_c15_every_cancel_thread_is_joined_before_the_probe_returns(k_project, tmp_path, monkeypatch):
    # The P8 stop point is 60 s away and its turn finishes normally, so the cancel thread is still waiting on its
    # stop event when the probe exits. Only the stop-and-join at probe exit can end it. A recording Thread records
    # every join of the cancel thread, so the check does not depend on a race with the thread's shutdown.
    joined = []

    class RecordingThread(threading.Thread):
        def join(self, timeout=None):
            if self.name == 'k8-cancel':
                joined.append(self.name)
            return super().join(timeout)

    monkeypatch.setattr(kb.threading, 'Thread', RecordingThread)
    binding, mode_file = _probe_setup(k_project, tmp_path)
    kb.CANCEL_THREADS.clear()
    kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {'P7': 'hang'}), agents_sha256=AGENTS_SHA,
                    claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=60.0)
    assert kb.CANCEL_THREADS and all(not thread.is_alive() for thread in kb.CANCEL_THREADS)
    assert len(joined) == len(kb.CANCEL_THREADS) >= 1


def test_c7_b_deny_list_refuses_basename_pin_and_shell_string_tokens():
    assert kb.claude_argv_denied(['claude', '-p']) is True
    assert kb.claude_argv_denied(['/x/claude']) is True
    assert kb.claude_argv_denied([str(kb.CLAUDE_BINARY)], str(kb.CLAUDE_BINARY)) is True
    assert kb.claude_argv_denied(['sh', '-c', 'claude -p hi']) is True
    assert kb.claude_argv_denied(['sh', '-c', 'echo claude-ish']) is False
    assert kb.claude_argv_denied(['env', 'X=1', '/w/ScieFlow-claude-f2976f52/fixture.py', '--model',
                                  'claude-opus-5-5']) is False


def test_c7_b_offline_suite_path_has_no_claude_directory():
    assert all(not (Path(d) / 'claude').exists() for d in os.environ.get('PATH', '').split(os.pathsep) if d)


def test_c7_b_offline_guard_refuses_a_shell_string_that_names_claude():
    # the autouse guard refuses before any process starts: a shell string naming claude never reaches the OS
    with pytest.raises(AssertionError, match='claude launch refused'):
        subprocess.Popen(['sh', '-c', 'claude -p hi'])


# ----- r6 fix round (spec SS3, SS5; code CS2, CS5, CS8, N1; brief R6-2, R6-4, R6-5, R6-8, R6-9) -----

def _fail_for_probe(monkeypatch, name, index, point, probe):
    """Raise an OSError from the named binding step only for one probe (the arguments are inspected, not the order)."""
    original = getattr(kb, name)

    def selective(*args, **kwargs):
        if args[index].get('probe') == probe:
            raise OSError(f'synthetic {point} failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(kb, name, selective)


def _shared_progress(gate):
    progress = {'preflight': None, 'ceiling': gate.Ceiling()}
    progress['ceiling'].record_preflight(0.0)  # the preflight is the first counted invocation (H1)
    return progress


@pytest.mark.parametrize('name,index,point', [
    ('write_probe_evidence', 1, 'evidence'),
    ('to_harness_report', 0, 'to-harness-report'),
    ('write_probe_report', 1, 'write-probe-report'),
])
def test_r6_2_an_exception_after_p3_returns_counts_every_launch_and_stops(k_project, tmp_path, monkeypatch,
                                                                          name, index, point):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    _fail_for_probe(monkeypatch, name, index, point, 'P3')
    evidence = tmp_path / 'evidence'
    progress = _shared_progress(gate)
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA, claude_pin=PIN,
                          truncate_after_s=2.0, cancel_after_s=2.0, evidence_root=evidence, progress=progress)
    assert out['status'] == 'stopped-after-launch'
    assert out['ceiling']['invocations'] == 3 and progress['ceiling'].invocations == 3  # preflight, P1, P3
    assert [r['probe'] for r in out['reports']] == ['P1']
    stopped = json.loads((evidence / 'stopped.json').read_text(encoding='utf-8'))
    assert stopped['probe'] == 'P3' and stopped['ceiling']['invocations'] == 3
    assert out['reasons'] == [f'exception:OSError: synthetic {point} failure']


def test_r6_2_a_checkpoint_that_cannot_be_written_stops_and_never_pauses(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()

    def cannot_write(*args, **kwargs):
        raise OSError('synthetic checkpoint failure')

    monkeypatch.setattr(kb, '_pause', cannot_write)
    evidence = tmp_path / 'evidence'
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          checkpoint_path=tmp_path / 'checkpoint.json', evidence_root=evidence,
                          progress=_shared_progress(gate))
    assert out['status'] == 'stopped' and out['reasons'] == ['checkpoint-write-failed:OSError']
    assert out['ceiling']['invocations'] == 2 and (evidence / 'stopped.json').is_file()
    assert not (tmp_path / 'checkpoint.json').exists()


def test_r6_4_a_launch_without_an_explicit_environment_is_refused_before_the_start(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    reached = []
    monkeypatch.setattr(kb.jobs, 'start', lambda *args, **kwargs: reached.append(args))
    seen = {'launches': 0}
    with kb.probe_controls('P1', seen, project=binding.project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=binding.run_dir):
        with pytest.raises(kb.ProbeRefused, match='unexpected-permission:env:inherited'):
            kb.jobs.start(binding.project, ['true'], sandbox_writable=[], sandbox_mask=[], env=None)
    assert reached == [] and seen['launches'] == 0


def test_r6_9_the_binding_environment_allowlists_are_pinned_exactly():
    # R10-4 (deviation D-env): the launch allow-list is production's C12_KEPT, plus LC_*, plus the credential var.
    assert kb.ENV_ALLOW == sandbox.C12_KEPT
    assert kb.ENV_CREDENTIAL == frozenset({'CLAUDE_CODE_OAUTH_TOKEN'})
    assert kb.ENV_PRODUCTION == frozenset()
    assert kb.ENV_HARNESS == frozenset()
    gate = _gate()
    assert kb.ENV_ALLOW == gate.LIVE_ENV_ALLOW and kb.ENV_PRODUCTION == gate.PRODUCTION_ENV_KEYS
    # D-harness: the harness process may carry its bytecode switch (re-exec); the LAUNCH env never does.
    assert gate.HARNESS_ENV_KEYS == frozenset({'PYTHONDONTWRITEBYTECODE'})


class _AlarmExpired(Exception):
    """SU4/SU9: the guard's failure. It is deliberately not an OSError, so no production `except OSError` swallows it."""


def _alarm(seconds):
    """Fails a test instead of hanging it when a listing or read blocks on a special file (CS5, R7-2).
    The timer repeats every `seconds`, so an expiry that a caller swallowed is raised again at the next tick, and
    each guarded call arms its own timer."""
    import contextlib
    import signal

    @contextlib.contextmanager
    def guard():
        fired = []

        def expired(signum, frame):
            fired.append(signum)
            raise _AlarmExpired('blocked on a special file')

        previous = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        if fired:  # an expiry the production code swallowed (e.g. in an `except OSError`) still fails the test
            raise _AlarmExpired('a blocking call expired inside the guarded block')

    return guard()


def test_r6_5_a_fifo_in_the_run_dir_or_the_journals_dir_never_blocks(tmp_path):
    run = tmp_path / 'run'
    journals = tmp_path / 'journals'
    run.mkdir()
    journals.mkdir()
    (run / 'a.txt').write_text('payload', encoding='utf-8')
    os.mkfifo(run / 'pipe')
    os.mkfifo(journals / 'pipe')
    with _alarm(10):
        listing = kb.run_listing(run)
        digest = kb._files_digest(journals)
        raw = kb._read_regular(run / 'pipe')
    assert listing['pipe'].startswith('special:') and listing['a.txt'] == kb.sha256_file(run / 'a.txt')
    assert digest is not None and raw == b''
    assert 'unexpected-file:pipe' in kb.derived_effect_refusals({'fs_before': {}, 'fs_after': listing})


def test_r6_8_the_binding_exclusive_writer_refuses_an_existing_file_and_a_symlink(tmp_path):
    existing = tmp_path / 'stopped.json'
    existing.write_text('old', encoding='utf-8')
    with pytest.raises(FileExistsError):
        kb._write_new(existing, b'new')
    assert existing.read_text(encoding='utf-8') == 'old'
    victim = tmp_path / 'victim'
    link = tmp_path / 'link.json'
    link.symlink_to(victim)
    with pytest.raises(FileExistsError):
        kb._write_new(link, b'new')
    assert not victim.exists()



# ----- r7 fix round (review r6 CR2 survivor table, SU4 real survivors R6-5b, R6-5c; brief R7-2) -----

def _swap_on_open(monkeypatch, target, replace):
    """Race helper (R7-2): the entry is swapped between the lstat and the open, through the os.open the module uses."""
    import os as _os
    real_open = _os.open

    def swap_then_open(path, flags, *args, **kwargs):
        if Path(path) == Path(target):
            _os.unlink(target)
            replace(target)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(kb.os, 'open', swap_then_open)


def test_r7_2_entry_digest_rechecks_the_file_it_opened(tmp_path, monkeypatch):
    """CR-B01 (no fstat recheck) and R6-5b (open without O_NONBLOCK): a regular file replaced by a writer-less FIFO
    between lstat and open must read as special, and must not block."""
    target = tmp_path / 'entry'
    target.write_text('data', encoding='utf-8')
    _swap_on_open(monkeypatch, target, lambda p: os.mkfifo(p))
    with _alarm(5):
        digest = kb._entry_digest(target)
    assert digest.startswith('special:')


def test_r7_2_entry_digest_never_follows_a_symlink_swapped_in_after_lstat(tmp_path, monkeypatch):
    """CR-B06: the open must refuse a symlink (O_NOFOLLOW); the secret behind it must not be digested."""
    import hashlib
    target = tmp_path / 'entry'
    target.write_text('regular', encoding='utf-8')
    secret = tmp_path / 'secret'
    secret.write_text('TOP-SECRET', encoding='utf-8')
    _swap_on_open(monkeypatch, target, lambda p: os.symlink(secret, p))
    digest = kb._entry_digest(target)
    assert digest.startswith('special:')
    assert digest != hashlib.sha256(b'TOP-SECRET').hexdigest()


def test_r7_2_entry_digest_does_not_open_a_non_regular_entry(tmp_path, monkeypatch):
    """CR-B02: an entry whose lstat is not regular is reported special and never opened."""
    fifo = tmp_path / 'pipe'
    os.mkfifo(fifo)
    opened = []
    real_open = os.open

    def spy(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(kb.os, 'open', spy)
    with _alarm(5):
        digest = kb._entry_digest(fifo)
    assert digest.startswith('special:')
    assert fifo not in opened


def test_r7_2_read_regular_never_blocks_on_a_writerless_fifo(tmp_path):
    """CR-B03: _read_regular on a FIFO with no writer returns b'' at once; a blocking open would expire the guard."""
    fifo = tmp_path / 'pipe'
    os.mkfifo(fifo)
    with _alarm(5):
        assert kb._read_regular(fifo) == b''


def test_r7_2_read_regular_returns_nothing_from_a_fifo_that_holds_data(tmp_path):
    """CR-B04: a FIFO with buffered data is not a regular file; the fstat recheck must return b''."""
    fifo = tmp_path / 'pipe'
    os.mkfifo(fifo)
    keep = os.open(fifo, os.O_RDWR)  # a writer stays open, so no open waits, and the data stays buffered
    try:
        os.write(keep, b'payload')
        with _alarm(5):
            assert kb._read_regular(fifo) == b''
    finally:
        os.close(keep)


def test_r7_2_a_special_entry_at_a_derived_name_is_refused_as_special():
    """CR-B16 (special written as specia1 in derived_effect_refusals) and R6-5c (the special line removed): a special
    entry at a name the turn may legitimately create is still refused as special."""
    name = 'jobs/' + '0' * 26 + '.log'
    reasons = kb.derived_effect_refusals({'fs_before': {}, 'fs_after': {name: 'special:10600'}})
    assert f'unexpected-file:{name}:special' in reasons
    assert f'unexpected-file:{name}' not in reasons  # the name itself is derived, so only the special reason applies


def test_r7_2_a_symlinked_directory_is_listed_as_a_link_and_never_descended(tmp_path):
    """XB09: os.walk must not descend a symlinked directory; the link is listed as link:<target>."""
    run = tmp_path / 'run'
    outside = tmp_path / 'outside'
    run.mkdir()
    outside.mkdir()
    (outside / 'secret.txt').write_text('x', encoding='utf-8')
    (run / 'linkdir').symlink_to(outside)
    listing = kb.run_listing(run)
    assert listing['linkdir'].startswith('link:')
    assert not any(path.startswith('linkdir/') for path in listing)


def test_r7_2_the_production_env_test_runner_allowlist_is_empty():
    """CR-B14: the production source defines an empty test-runner allowlist. (The suite's own fixture patches the
    attribute at run time, so the literal in the source is what is checked.)"""
    import re
    source = Path(kb.__file__).read_text(encoding='utf-8')
    assert re.search(r'^ENV_TEST_RUNNER = frozenset\(\)$', source, re.M)
    assert not re.search(r'^ENV_TEST_RUNNER = frozenset\(\{', source, re.M)


def test_r7_2_resume_sequence_refuses_a_checkpoint_that_understates_the_turn_jobs(k_project, tmp_path, monkeypatch):
    """XB13: resume_sequence passes the real turn-job count to the content check; an understated checkpoint is refused
    and nothing launches."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    _out, ckpt = _pause(binding, gate, mode_file, tmp_path)
    launches = _spy_starts(monkeypatch)
    out = kb.resume_sequence(binding, gate, checkpoint_path=ckpt,
                             receipt_checkpoint_sha256=gate.sha256_file(ckpt),
                             mode_for=_modes(mode_file, {}), claude_pin=PIN, agents_sha256=AGENTS_SHA,
                             truncate_after_s=2.0, cancel_after_s=2.0, turn_jobs=50)
    assert 'checkpoint-invocations-understated' in out['reasons']
    assert out['status'].startswith('refused:')
    assert len(launches) == 0


def test_r7_2_launch_kwargs_that_drift_only_in_the_masks_are_refused(k_project, tmp_path, monkeypatch):
    """XB03b: the sandbox mask list is compared with the admitted protection, not only the writable roots."""
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    real_resolve = sandbox.resolve_turn_protection
    calls = {'n': 0}

    def drifting_masks(project, **kwargs):
        calls['n'] += 1
        protection = real_resolve(project, **kwargs)
        if calls['n'] == 1:
            return protection
        assert protection.masks, 'the fixture must carry masks to drift'
        return dataclasses.replace(protection, masks=tuple(protection.masks)[:-1])  # same roots, one mask fewer

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', drifting_masks)
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    chain = _chain_text(info.value)
    assert any('unexpected-permission:sandbox:kwargs' in text for text in chain), chain
    assert launches == []


def test_r7_2_a_probe_refused_before_its_launch_is_not_counted_as_an_invocation(k_project, tmp_path, monkeypatch):
    """CR-B09: a refusal before the launch stops the matrix at zero invocations."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    monkeypatch.setattr(kb, 'run_is_unsandboxed', lambda project, slug: True)
    out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0)
    assert out['ceiling']['invocations'] == 0
    assert out['status'].startswith('refused:unsandboxed-run'), out['status']


def test_r7_2_a_refusal_before_launch_does_not_inherit_the_previous_launch_state(k_project, tmp_path, monkeypatch):
    """CR-B11: run_probe resets the launch record before it can refuse."""
    binding, _ = _probe_setup(k_project, tmp_path)
    binding.last_seen = {'launches': 1, 'job_id': 'PREVIOUS'}
    monkeypatch.setattr(kb, 'run_is_unsandboxed', lambda project, slug: True)
    with pytest.raises(kb.ProbeRefused):
        binding.run_probe('P1')
    assert binding.launched() is False


def test_r7_2_a_stopped_probe_keeps_the_stream_bytes_it_already_produced(k_project, tmp_path, monkeypatch):
    """XB18: after a failure that follows the launch, the stream bytes are persisted, not written empty."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()

    def fail_after_the_launch(*args, **kwargs):
        raise RuntimeError('evidence write failed after the launch')

    monkeypatch.setattr(kb, 'write_probe_evidence', fail_after_the_launch)
    evidence_root = tmp_path / 'evidence'
    with _alarm(60):
        kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                        claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0, evidence_root=evidence_root)
    assert (evidence_root / 'P1' / 'stream.jsonl').read_bytes() != b''


def test_r7_2_a_stopped_probe_whose_stream_is_a_writerless_fifo_does_not_block(k_project, tmp_path, monkeypatch):
    """CR-B18: the partial-bytes read goes through the non-blocking reader; a FIFO in the job log's place cannot hang."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    real_partial = kb.ProbeBinding.partial_bytes

    def fail_after_the_launch(*args, **kwargs):
        raise RuntimeError('evidence write failed after the launch')

    def fifo_in_the_log_place(self, seen):
        job_log = Path(kb.jobs.find(self.project, seen['job_id']).log)
        job_log.unlink()
        os.mkfifo(job_log)
        return real_partial(self, seen)

    monkeypatch.setattr(kb, 'write_probe_evidence', fail_after_the_launch)
    monkeypatch.setattr(kb.ProbeBinding, 'partial_bytes', fifo_in_the_log_place)
    with _alarm(60):
        out = kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                              claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                              evidence_root=tmp_path / 'evidence')
    assert out['reasons'] and out['ceiling']['invocations'] >= 1


def test_r7_2_the_environment_values_of_a_launch_are_never_recorded(k_project, tmp_path, monkeypatch):
    """XB16: a sentinel value in an allowlisted variable reaches the launch but no file records the value."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    key = next(k for k in ('LANG', 'PATH', 'HOME', 'TERM', 'TZ') if k in kb.ENV_ALLOW)
    sentinel = 'SENTINEL-VALUE-7f3a9c'
    monkeypatch.setenv(key, sentinel)
    kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                    claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                    evidence_root=tmp_path / 'evidence')
    recorded = [p for p in tmp_path.rglob('*') if p.is_file() and sentinel.encode() in p.read_bytes()]
    assert recorded == [], recorded


def test_r7_2_resume_sequence_pauses_into_the_next_checkpoint_and_keeps_the_resumed_one(k_project, tmp_path):
    """CR-B13: a pause inside a resume writes next_checkpoint_path; the checkpoint that was resumed is never replaced."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    _out, ckpt = _pause(binding, gate, mode_file, tmp_path)
    before = gate.sha256_file(ckpt)
    nxt = tmp_path / 'next-checkpoint.json'
    out = kb.resume_sequence(binding, gate, checkpoint_path=ckpt, receipt_checkpoint_sha256=before,
                             mode_for=_modes(mode_file, {'P2': 'usage-limit'}), claude_pin=PIN, agents_sha256=AGENTS_SHA,
                             truncate_after_s=2.0, cancel_after_s=2.0, next_checkpoint_path=nxt)
    assert out['status'] == 'paused-usage-limit', out['status']
    assert gate.sha256_file(ckpt) == before
    assert nxt.is_file()


def test_r7_2_the_guard_expiry_is_not_an_oserror_so_a_handler_cannot_swallow_it():
    """SU9: a handler that catches OSError must not be able to swallow the guard's expiry."""
    import time
    swallowed = []
    with pytest.raises(_AlarmExpired):
        with _alarm(1):
            try:
                time.sleep(3)
            except OSError:
                swallowed.append(True)
    assert swallowed == []


def test_r7_2_read_regular_does_not_follow_a_symlink(tmp_path):
    """CR-B05: the non-blocking reader refuses a symlink (O_NOFOLLOW); the target's bytes are never returned."""
    target = tmp_path / 'target.txt'
    target.write_text('SECRET', encoding='utf-8')
    link = tmp_path / 'link'
    link.symlink_to(target)
    assert kb._read_regular(link) == b''


def test_r7_2_a_failure_before_the_launch_propagates_and_writes_no_stop_record(k_project, tmp_path, monkeypatch):
    """CR-B08: a failure before any launch is re-raised to the caller; it is not recorded as a post-launch stop."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()

    def fail_before_the_launch(*args, **kwargs):
        raise RuntimeError('failure before the launch')

    monkeypatch.setattr(kb.sandbox, 'resolve_turn_protection', fail_before_the_launch)
    evidence = tmp_path / 'evidence'
    with pytest.raises(RuntimeError, match='failure before the launch'):
        kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                        claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0, evidence_root=evidence)
    assert not (evidence / 'stopped.json').exists()


# ----- R8 (fix round r8): discriminating tests for the r7 survivors CR-B15..CR-B25 (tests only) ----------------

def test_r8_cr_b15_a_hermes_prefixed_variable_is_an_env_refusal():
    """CR-B15: HERMES_* is not allowed by prefix; the binding environment check names the variable."""
    assert kb.env_refusal_keys(['HERMES_SESSION_ID']) == ['HERMES_SESSION_ID']


def test_r8_cr_b17_special_journal_entries_change_the_digest(tmp_path):
    """CR-B17: two journal trees that differ only in a special entry's permission bits digest differently."""
    left, right = tmp_path / 'left', tmp_path / 'right'
    left.mkdir()
    right.mkdir()
    os.mkfifo(left / 'pipe')
    os.mkfifo(right / 'pipe')
    os.chmod(left / 'pipe', 0o600)
    os.chmod(right / 'pipe', 0o644)
    with _alarm(10):
        assert kb._files_digest(left) != kb._files_digest(right)


def test_r8_cr_b19_the_report_returns_when_the_job_log_is_a_fifo(k_project, tmp_path, monkeypatch):
    """CR-B19: the report reads job.log through the non-blocking regular-file reader, so a FIFO cannot block it."""
    from types import SimpleNamespace
    binding, _mode_file = _probe_setup(k_project, tmp_path)
    fifo = tmp_path / 'job.log'
    os.mkfifo(fifo)
    job = SimpleNamespace(log=str(fifo), err=None, turn_audit={}, id='r8-job', kind='probe', sandboxed=True,
                          state='running', exit_code=None, duration_s=None, json=None)
    monkeypatch.setattr(kb.jobs, 'find', lambda project, job_id: job)
    monkeypatch.setattr(kb, 'control_snapshot', lambda *args, **kwargs: {})
    empty_listing = {'scieflow': [], 'home_probes': [], 'journals': None, 'pycache': []}
    monkeypatch.setattr(kb, 'k_side_listing', lambda project: dict(empty_listing))
    with _alarm(20):
        report = kb.ProbeBinding._report(binding, 'P1', {'job_id': 'r8-job'}, fs_before={}, snap_before={},
                                         k_before=dict(empty_listing), budget_before='', state_read={}, error=None,
                                         result=None)
    assert isinstance(report, dict) and report['probe'] == 'P1'


def test_r8_cr_b22_a_refused_resume_reports_the_live_ceiling_not_the_saved_one(tmp_path, monkeypatch):
    """CR-B22: a refusal with a live shared ceiling reports the live counts; the saved checkpoint's counts are not used."""
    from types import SimpleNamespace
    checkpoint = tmp_path / 'checkpoint.json'
    checkpoint.write_text(json.dumps({'reports': [], 'ceiling': {'invocations': 2, 'completing': 0, 'active_s': 1.0}}),
                          encoding='utf-8')

    class Gate:
        def resume_refusals(self, *args, **kwargs):
            return ['checkpoint-job-running']

    monkeypatch.setattr(kb.conversation, 'read', lambda run_dir: {'session': 's'})
    monkeypatch.setattr(kb, '_config_digest', lambda run_dir: 'config')
    monkeypatch.setattr(kb, '_running_jobs', lambda project, run_dir: False)
    binding = SimpleNamespace(run_dir=tmp_path, project=None, revision=lambda: 'rev')
    live = SimpleNamespace(invocations=9, completing=1, active_s=4.0)
    out = kb.resume_sequence(binding, Gate(), checkpoint_path=checkpoint, receipt_checkpoint_sha256='x',
                             mode_for=None, agents_sha256='a', claude_pin=None, progress={'ceiling': live})
    assert out['status'] == 'refused:checkpoint-job-running'
    assert out['ceiling'] == {'invocations': 9, 'completing': 1, 'active_s': 4.0}


def test_r8_cr_b25_a_pause_carries_the_preflight_recorded_in_progress(k_project, tmp_path):
    """CR-B25: a pause after a preflight writes that preflight record (from progress) into the checkpoint."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    carried = {'ok': True, 'r8_marker': 'carried-from-progress'}
    progress = {'preflight': carried, 'ceiling': gate.Ceiling()}
    progress['ceiling'].record_preflight(0.0)
    ckpt = tmp_path / 'checkpoint.json'
    kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}), claude_pin=PIN,
                    agents_sha256=AGENTS_SHA, truncate_after_s=2.0, cancel_after_s=2.0, checkpoint_path=ckpt,
                    progress=progress)
    data = json.loads(ckpt.read_text(encoding='utf-8'))
    assert data['preflight'] == carried


def test_r8_r6_2h_run_sequence_publishes_the_ceiling_it_creates_through_progress(k_project, tmp_path):
    """R6-2h (SU5, R8-4): a run_sequence called without a caller ceiling publishes the ceiling it creates in `progress`,
    so an outer handler reads the same accounting. Without this line the mutant leaves progress without a ceiling."""
    binding, mode_file = _probe_setup(k_project, tmp_path)
    gate = _gate()
    progress = {}
    kb.run_sequence(binding, gate, mode_for=_modes(mode_file, {'P1': 'usage-limit'}), claude_pin=PIN,
                    agents_sha256=AGENTS_SHA, truncate_after_s=2.0, cancel_after_s=2.0,
                    checkpoint_path=tmp_path / 'checkpoint.json', progress=progress)
    assert 'ceiling' in progress and progress['ceiling'].invocations == 1


# ---- R10-7 .. R10-9 (addendum r10 section 1) ----------------------------------------------------------------

def test_r10_7_agent_home_writes_are_evidence_not_effect_refusals():
    prefix = kb.AGENT_HOME_PREFIX
    report = {'fs_before': {}, 'fs_after': {prefix + 'projects/s.jsonl': 'd1', 'scratch/other.txt': 'd2'}}
    assert kb.derived_effect_refusals(report) == ['unexpected-file:scratch/other.txt']


def test_r10_7_leftover_verify_marker_in_host_home_is_a_k_side_refusal():
    before = {'scieflow': [], 'home_probes': [], 'journals': 'h1', 'pycache': []}
    assert kb.k_side_refusals(before, dict(before, home_probes=['.scieflow-verify-marker-x'])) == [
        'unexpected-file:$HOME/.scieflow-verify-marker-x']


def test_r10_7_discovery_reads_the_agent_home_settings_only(tmp_path):
    agent = tmp_path / 'agent' / '.claude'
    agent.mkdir(parents=True)
    (agent / 'settings.json').write_text('{}', encoding='utf-8')
    host = tmp_path / 'host' / '.claude'
    host.mkdir(parents=True)
    (host / 'settings.local.json').write_text('{"host": 1}', encoding='utf-8')
    found = kb.discovery_inputs(tmp_path / 'k', tmp_path / 'agent')
    assert list(found['home-claude-settings']) == ['settings.json']


def _token_scan(root: Path, token: str, skip=('credentials', 'agent-home')):
    """R10-8: every byte under root (except the credentials dir and the agent home) is searched for the fake token."""
    files = occurrences = 0
    for path in Path(root).rglob('*'):
        if not path.is_file() or path.is_symlink() or any(part in skip for part in path.parts):
            continue
        files += 1
        occurrences += path.read_bytes().count(token.encode('utf-8'))
    return files, occurrences


def _fake_token(tmp_path) -> str:
    return (tmp_path / 'credentials' / 'claude.token').read_text(encoding='utf-8').strip()


def test_r10_8_success_path_writes_no_token_byte(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                    agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0)
    files, occurrences = _token_scan(tmp_path, _fake_token(tmp_path))
    assert files > 0 and occurrences == 0


def test_r10_8_refusal_path_writes_no_token_byte(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    binding.run_probe('P1')  # R14-2: the resume probe needs a session; one usable first turn gives it one
    monkeypatch.setattr(kb, 'confinement_refusals', lambda protection, run_dir: ['forced-by-test'])
    with pytest.raises(Exception, match='confinement-before-launch'):
        binding.run_probe('P4')
    files, occurrences = _token_scan(tmp_path, _fake_token(tmp_path))
    assert files > 0 and occurrences == 0


def test_r10_9_real_bwrap_production_form_sees_only_the_allowed_environment(k_project, tmp_path):
    """R10-9: real bwrap with the production-form command; must run (no skip)."""
    import pwd
    assert shutil.which('bwrap') is not None, 'bwrap is required for R10-9 and must not be skipped'
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    run_dir = tmp_path / 'run'
    (run_dir / 'scratch' / 'agent-home' / 'claude').mkdir(parents=True)
    install = tmp_path / 'fake-claude' / 'versions' / '9.9.9'
    install.mkdir(parents=True)
    launcher = install / 'claude'
    checks = ' '.join(str(p) for p in [real_home / '.ssh', real_home / '.hermes', real_home / '.config' / 'scieflow'
                                       / 'credentials', Path(f'/run/user/{os.getuid()}/bus'),
                                       Path('/run/dbus/system_bus_socket')])
    launcher.write_text('#!/bin/sh\nfor p in ' + checks + '; do if [ -e "$p" ]; then echo PRESENT "$p"; '
                        'else echo ABSENT "$p"; fi; done\necho HOME="$HOME"\n'
                        'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then echo TOKEN_VAR_PRESENT; fi\n'
                        'if [ -n "$MAX_THINKING_TOKENS" ]; then echo EFFORT_PRESENT; fi\n'
                        'env | cut -d= -f1 | sort\n', encoding='utf-8')
    launcher.chmod(0o755)
    FAKE_LAUNCHERS.add(str(launcher))  # D-guard: this exact test-owned launcher only
    layout = sandbox.dispatch_layout(k_project, run_dir=run_dir, agent='claude', launcher=launcher)
    agent_home = sandbox.agent_home_path(run_dir, 'claude')
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(agent_home), 'LANG': 'C.UTF-8', 'TZ': 'UTC',
           'UV_CACHE_DIR': str(run_dir / 'scratch' / 'uv-cache'), 'MAX_THINKING_TOKENS': '32000',
           'CLAUDE_CODE_OAUTH_TOKEN': 'k8-fake-token-r10-9'}
    argv = sandbox.wrap([str(launcher)], writable=[run_dir], cwd=Path(k_project.root), layout=layout)
    proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert [line for line in lines if line.startswith('PRESENT')] == [], lines
    assert sum(line.startswith('ABSENT') for line in lines) == 5
    assert f'HOME={agent_home}' in lines and 'TOKEN_VAR_PRESENT' in lines and 'EFFORT_PRESENT' in lines
    keys = {line for line in lines if '=' not in line and line not in ('TOKEN_VAR_PRESENT', 'EFFORT_PRESENT')
            and not line.startswith(('ABSENT', 'PRESENT'))}
    allowed = set(h_allowed()) | {'MAX_THINKING_TOKENS', 'CLAUDE_CODE_OAUTH_TOKEN', 'PWD'}  # PWD: the shell's own
    assert keys <= allowed, sorted(keys - allowed)


def h_allowed() -> frozenset:
    """R10-4 allowed set = production's C12_KEPT (LC_* are not used by this env)."""
    return frozenset(sandbox.C12_KEPT)


# ---- R10-4 / R10-6 direct refusals (R10-11: each gate has a named test) ------------------------------------

def _refusal_chain(exc) -> list[str]:
    chain, seen = [], set()
    while exc is not None:  # production wraps the refusal in ServiceError; walk the cause chain
        assert id(exc) not in seen and len(chain) < 20, 'exception chain is cyclic or unbounded'
        seen.add(id(exc))
        chain.append(str(exc))
        exc = exc.__cause__ or exc.__context__
    return chain


def _edit_dispatch_env(monkeypatch, edit):
    original = sandbox.dispatch_environment

    def patched(*args, **kwargs):
        env = original(*args, **kwargs)
        edit(env)
        return env

    monkeypatch.setattr(sandbox, 'dispatch_environment', patched)


def test_r10_4_missing_credential_var_is_refused_before_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    _edit_dispatch_env(monkeypatch, lambda env: env.pop(kb.CREDENTIAL_VAR, None))
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    assert any('unexpected-permission:env:credential-missing' in text for text in _refusal_chain(info.value)), _refusal_chain(info.value)
    assert launches == []


def test_r10_4_home_other_than_the_agent_home_is_refused_before_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    _edit_dispatch_env(monkeypatch, lambda env: env.__setitem__('HOME', str(tmp_path)))
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    assert any('unexpected-permission:env:home' in text for text in _refusal_chain(info.value))
    assert launches == []


def test_r10_4_a_preset_effort_key_is_refused_before_launch(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    launches = _spy_starts(monkeypatch)
    _edit_dispatch_env(monkeypatch, lambda env: env.__setitem__('MAX_THINKING_TOKENS', '1'))
    with pytest.raises(Exception) as info:
        binding.run_probe('P1')
    assert any('unexpected-permission:env:effort-preset' in text for text in _refusal_chain(info.value))
    assert launches == []


def test_r10_6_capture_refuses_when_claude_is_not_on_path(k_project):
    with pytest.raises(kb.ProbeRefused, match='not capturable'):
        kb.expected_identity_for_fixture(k_project, session_template=kb.SESSION_TEMPLATE,
                                         resume_template=kb.RESUME_TEMPLATE)


def test_r10_6_observed_claude_is_none_when_claude_is_absent(k_project):
    assert kb.observe_claude_live(k_project) == (None, None)


def test_r10_6_layout_refusals_name_a_missing_or_unverified_layout(k_project, tmp_path):
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    protection = types.SimpleNamespace(readable=(), writable=(run_dir,))
    assert kb.layout_refusals(k_project, None, protection, run_dir, []) == ['layout-missing']
    layout = sandbox.dispatch_layout(k_project, run_dir=run_dir, agent='claude')
    assert kb.layout_refusals(k_project, layout, protection, run_dir, []) == ['verify-not-run']


def test_r10_6_layout_refusals_name_home_and_launcher_install_violations(k_project, tmp_path, monkeypatch):
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    install = tmp_path / 'fake-claude' / 'versions' / '9.9.9'
    install.mkdir(parents=True)
    launcher = install / 'claude'
    launcher.write_text('#!/bin/sh\n', encoding='utf-8')
    launcher.chmod(0o755)
    monkeypatch.setenv('PATH', f'{install}{os.pathsep}' + os.environ.get('PATH', ''))
    protection = types.SimpleNamespace(readable=(), writable=(run_dir,))
    layout = sandbox.dispatch_layout(k_project, run_dir=run_dir, agent='claude', launcher=launcher)
    assert kb.layout_refusals(k_project, layout, protection, run_dir, [layout]) == []
    away = dataclasses.replace(layout, home=tmp_path / 'elsewhere')
    assert 'layout-home' in kb.layout_refusals(k_project, away, protection, run_dir, [away])
    no_install = dataclasses.replace(layout, installs=())
    assert 'launcher-not-in-installs' in kb.layout_refusals(k_project, no_install, protection, run_dir, [no_install])


def test_r10_7_k_side_listing_sees_a_leftover_verify_marker_in_the_host_home(k_project, tmp_path, monkeypatch):
    home = tmp_path / 'host-home'
    home.mkdir()
    (home / '.scieflow-verify-marker-x').write_text('', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    assert '.scieflow-verify-marker-x' in kb.k_side_listing(k_project)['home_probes']


def test_r10_7_discovery_for_a_run_is_the_agent_home_not_the_host_home(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    host_claude = Path(os.environ['HOME']) / '.claude'  # the offline HOME; a settings file the probed claude must not see
    host_claude.mkdir(parents=True, exist_ok=True)
    (host_claude / 'settings.json').write_text('{}', encoding='utf-8')
    report = binding.run_probe('P1')
    assert report['discovery']['home-claude-settings'] == {}


# ----- r10b fix round (addendum r10b F1, F2, F3, F5, F6, F7; brief r10b section 3) ---------
# F1, F2, F3 and F5 pin behaviour that r10 already had (the reviewer's mutants and the r10b mutation run are the
# check that they bite). F6 and F7 are guard changes and are RED on the starting bytes.


def _r10b_cred_dir(tmp_path: Path) -> Path:
    cred = tmp_path / 'r10b-cred'
    cred.mkdir(mode=0o700)
    return cred


def _r10b_token(cred: Path, data: bytes = b'k8-fake-token-r10b', mode: int = 0o600) -> Path:
    path = cred / 'claude.token'
    path.write_bytes(data)
    path.chmod(mode)
    return path


def test_r10b_f1_valid_fake_token_file_has_no_refusals(tmp_path):
    cred = _r10b_cred_dir(tmp_path)
    _r10b_token(cred)
    assert kb.token_file_refusals(cred) == []


def test_r10b_f1_missing_token_file_is_refused(tmp_path):
    assert kb.token_file_refusals(_r10b_cred_dir(tmp_path)) == ['token-file-missing']


def test_r10b_f1_symlink_to_a_valid_file_is_not_regular(tmp_path):
    cred = _r10b_cred_dir(tmp_path)
    target = _r10b_token(tmp_path)  # a valid fake token outside the credentials dir
    (cred / 'claude.token').symlink_to(target)
    assert 'token-file-not-regular' in kb.token_file_refusals(cred)


def test_r10b_f1_wrong_mode_is_refused(tmp_path):
    cred = _r10b_cred_dir(tmp_path)
    _r10b_token(cred, mode=0o644)
    assert kb.token_file_refusals(cred) == ['token-file-mode']


def test_r10b_f1_foreign_owner_is_refused(tmp_path, monkeypatch):
    cred = _r10b_cred_dir(tmp_path)
    _r10b_token(cred)
    real_uid = os.getuid()
    monkeypatch.setattr(kb.os, 'getuid', lambda: real_uid + 1)  # a foreign owner is faked; nothing is chowned
    assert kb.token_file_refusals(cred) == ['token-file-owner']


@pytest.mark.parametrize('data', [b'', b'x' * 4097])
def test_r10b_f1_empty_or_oversized_token_file_is_refused(tmp_path, data):
    cred = _r10b_cred_dir(tmp_path)
    _r10b_token(cred, data=data)
    assert kb.token_file_refusals(cred) == ['token-file-size']


def test_r10b_f1_a_token_of_the_maximum_size_is_accepted(tmp_path):
    cred = _r10b_cred_dir(tmp_path)
    _r10b_token(cred, data=b'x' * kb.TOKEN_MAX_BYTES)
    assert kb.token_file_refusals(cred) == []


def _r10b_prefix_case(tmp_path):
    run = (tmp_path / 'run').resolve()
    home = run / 'scratch' / 'agent-home' / 'claude'
    home.mkdir(parents=True)
    layout = sandbox.Layout(home=home)
    protection = types.SimpleNamespace(writable=(run,))  # the fake protection grants exactly the run directory
    prefix = ['bwrap', '--ro-bind', '/usr', '/usr', '--ro-bind', '/etc', '/etc', '--tmpfs', '/run',
              '--bind', str(run), str(run), '--bind', str(home), str(home)]
    return prefix, layout, protection, run


def test_r10b_f1_production_form_prefix_of_a_valid_layout_has_no_refusals(tmp_path):
    prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    assert kb.prefix_refusals(prefix, layout, protection, run) == []


def test_r10b_f1_root_bind_is_refused(tmp_path):
    _prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    assert 'ro-bind-root' in kb.prefix_refusals(['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/run'],
                                               layout, protection, run)


def test_r10b_f1_absent_tmpfs_run_is_refused(tmp_path):
    prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    no_run = prefix[:7] + prefix[9:]  # drops the '--tmpfs', '/run' pair only
    assert kb.prefix_refusals(no_run, layout, protection, run) == ['tmpfs-run-absent']


def test_r10b_f1_hard_deny_sources_are_refused(tmp_path):
    prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    home = os.path.expanduser('~')
    assert 'hard-deny-source' in kb.prefix_refusals(prefix + ['--bind', home, home], layout, protection, run)
    ssh = str(Path(home) / '.ssh')
    assert 'hard-deny-source' in kb.prefix_refusals(prefix + ['--ro-bind', ssh, ssh], layout, protection, run)


def test_r10b_f1_a_bind_source_outside_the_allow_list_is_refused(tmp_path):
    prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    extra = prefix + ['--bind', '/opt/not-allowed', '/opt/not-allowed']
    assert kb.prefix_refusals(extra, layout, protection, run) == ['source-not-allowed']


def test_r10b_f1_missing_prefix_or_layout_is_refused(tmp_path):
    prefix, layout, protection, run = _r10b_prefix_case(tmp_path)
    assert kb.prefix_refusals([], layout, protection, run) == ['prefix-missing']
    assert kb.prefix_refusals(prefix, None, protection, run) == ['prefix-missing']


def test_r10b_f1_a_prefix_refusal_reaches_the_probe_report_as_a_permission_change(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    monkeypatch.setattr(kb, 'prefix_refusals', lambda prefix, layout, protection, run_dir: ['source-not-allowed'])
    report = binding.run_probe('P1')
    assert 'unexpected-permission:sandbox:source-not-allowed' in report['effect_reasons']


def test_r10b_f2_production_resolve_refuses_an_env_prefixed_effort_template(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template='env MAX_THINKING_TOKENS=32000 ' + kb.SESSION_TEMPLATE,
                       resume_template=kb.RESUME_TEMPLATE, timeout_min=1, model=kb.PINNED_MODEL)
    with pytest.raises(Exception, match='MAX_THINKING_TOKENS|env'):
        agent_run.resolve_turn(k_project, 'claude', run_dir=k_project.run_dir(kb.RUN_SLUG), session=None)


def test_r10b_f2_production_resolve_accepts_the_r10_template(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=kb.SESSION_TEMPLATE, resume_template=kb.RESUME_TEMPLATE,
                       timeout_min=1, model=kb.PINNED_MODEL)
    resolved = agent_run.resolve_turn(k_project, 'claude', run_dir=k_project.run_dir(kb.RUN_SLUG), session=None)
    assert resolved.harness_identity.executable


def test_r10b_f2_the_resolve_wrapper_forwards_create_home_unchanged(k_project, tmp_path, monkeypatch):
    run = k_project.run_dir(kb.RUN_SLUG)
    calls = []

    def spy(project, agent, **kwargs):
        calls.append((project, agent, dict(kwargs)))
        raise RuntimeError('spy-stop')

    monkeypatch.setattr(agent_run, 'resolve_turn', spy)  # installed before the wrapper, so it is resolve_orig
    with kb.probe_controls('P1', {}, project=k_project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=run):
        with pytest.raises(RuntimeError, match='spy-stop'):
            agent_run.resolve_turn(k_project, 'claude', run_dir=run, session=None, create_home=True)
    assert calls == [(k_project, 'claude', {'run_dir': run, 'session': None, 'create_home': True})]


def _r10b_scan_text(blob: str, token: str) -> int:
    return blob.count(token)


def test_r10b_f3_success_with_evidence_and_checkpoint_has_no_token(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    out = tmp_path / 'outdir'
    out.mkdir()
    res = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {'P7': 'hang', 'P8': 'hang'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          evidence_root=out / 'ev', checkpoint_path=out / 'checkpoint.json')
    token = _fake_token(tmp_path)
    files, occ = _token_scan(tmp_path, token)
    evfiles = sum(1 for p in (out / 'ev').rglob('*') if p.is_file())
    assert evfiles > 0 and files > 0 and occ == 0
    assert _r10b_scan_text(json.dumps(res, default=str), token) == 0


def test_r10b_f3_usage_limit_pause_with_checkpoint_has_no_token(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    out = tmp_path / 'outdir'
    out.mkdir()
    res = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {'P1': 'usage-limit'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, truncate_after_s=2.0, cancel_after_s=2.0,
                          evidence_root=out / 'ev', checkpoint_path=out / 'checkpoint.json')
    token = _fake_token(tmp_path)
    files, occ = _token_scan(tmp_path, token)
    assert (out / 'checkpoint.json').is_file() and occ == 0
    assert _r10b_scan_text(json.dumps(res, default=str), token) == 0


def test_r10b_f5_a_failed_start_restores_the_original_wrap_and_wraps_the_recorded_argv(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    real_wrap = sandbox.wrap
    recorded = {}

    def failing_start(project, argv, **kwargs):
        recorded['argv'] = list(argv)
        recorded['wrap_installed'] = sandbox.wrap is not real_wrap
        wrapped = sandbox.wrap(list(argv), writable=list(kwargs['sandbox_writable']), cwd=kwargs['cwd'],
                               mask=list(kwargs.get('sandbox_mask') or []) or None,
                               layout=kwargs.get('sandbox_layout'))
        recorded['wrapped_tail_is_argv'] = list(wrapped[-len(argv):]) == list(argv)
        raise RuntimeError('start-failed-by-test')

    monkeypatch.setattr(jobs, 'start', failing_start)  # becomes start_orig when the probe controls enter
    try:
        binding.run_probe('P1')
    except Exception:  # noqa: BLE001 - the probe may report or re-raise the test's failure
        pass
    assert recorded.get('wrap_installed') is True
    assert recorded.get('wrapped_tail_is_argv') is True
    assert sandbox.wrap is real_wrap


def test_r10b_f5_the_env_handed_to_the_launch_is_a_copy_without_the_effort_key(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    produced, seen = [], {}
    real_env = sandbox.dispatch_environment

    def recording_env(*args, **kwargs):
        env = real_env(*args, **kwargs)
        produced.append(env)
        return env

    def failing_start(project, argv, **kwargs):
        seen['copy_has_effort'] = kb.PINNED_EFFORT_ENV[0] in kwargs['env']
        raise RuntimeError('start-failed-by-test')

    monkeypatch.setattr(sandbox, 'dispatch_environment', recording_env)
    monkeypatch.setattr(jobs, 'start', failing_start)
    try:
        binding.run_probe('P1')
    except Exception:  # noqa: BLE001
        pass
    assert produced, 'production built no dispatch environment'
    assert seen.get('copy_has_effort') is True  # the handed-over copy carries the pinned effort
    assert kb.PINNED_EFFORT_ENV[0] not in produced[-1]  # production's own dict is never mutated


# ----- r11 (operator ruling r11; addendum r11 R11-1 to R11-4) -----------------------------------------
# The probe tree moves out of the workspace ancestry. With K under ROOT/workspace, the probe run has two
# structural owners and production agent_run.resolve_turn refuses it (434572c, agent_run.py:537-539).

K11 = Path('/home/ubuntu/ScieFlow-probe/k-task8-k-r11')  # R11-1: detached worktree of 21292ce (R14-3; was 434572c)


def test_r11_f6_the_session_guard_watches_k11_the_probe_tree():
    # R11-3 (replaces test_r10b_f6, renamed): the session guard digests the probe tree of r11, taken from H.K.
    assert _real_k_state()[0] == kb.tree_digest(K11)


def test_r11_k11_literal_is_the_harness_probe_tree():
    # R11-3 pin: this file's literal K11 equals the harness constant that every live path uses.
    assert _h_k() == K11


@contextlib.contextmanager
def _geometry_project(parent: Path):
    """R11-4: a disposable sibling of the probe tree. It has the same ancestry except its own last name, plus
    config/ and schemas/ copied from W10 and an empty workspace/. It is removed on exit and never written in K."""
    assert parent.is_dir(), f'geometry parent is missing: {parent}'
    sibling = parent / f'.k8r11-geom-{os.getpid()}-{secrets.token_hex(4)}'
    sibling.mkdir(mode=0o700)
    try:
        assert sibling.parent == parent
        shutil.copytree(W / 'config', sibling / 'config')
        shutil.copytree(W / 'schemas', sibling / 'schemas')
        (sibling / 'workspace').mkdir()
        yield Project(root=sibling)
    finally:
        shutil.rmtree(sibling)
        assert not sibling.exists(), 'the geometry sibling survived its test'


def _geometry_run(project: Project, tmp_path: Path) -> Path:
    """The production probe run (k-probe-a1) with production templates; no launch. The fake claude is on PATH."""
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(project, session_template=session, resume_template=resume, timeout_min=0.1,
                       model=kb.PINNED_MODEL)
    return project.run_dir(kb.RUN_SLUG)


def test_r11_1_the_live_k_ancestry_passes_production_structural_ownership(tmp_path):
    # R11-4: RED on the r10b bytes. H.K = K10 under RUN/scratch, so the sibling's ancestry holds RUN as a 2nd owner.
    with _geometry_project(_h_k().parent) as project:
        run = _geometry_run(project, tmp_path)
        resolved = agent_run.resolve_turn(project, 'claude', run_dir=run, session=None)
        assert isinstance(resolved, agent_run.ResolvedTurn)


def test_r11_2_the_r10b_ancestry_is_refused_by_production(tmp_path):
    # R11-4 positive control: the same construction under RUN/scratch (the r10b layout) is refused by production.
    with _geometry_project(W.parents[1] / 'scratch') as project:
        run = _geometry_run(project, tmp_path)
        with pytest.raises(agent_run.DispatchError, match='unique structural owner'):  # production wraps the refusal
            agent_run.resolve_turn(project, 'claude', run_dir=run, session=None)


def test_r11_3_the_live_k_ancestry_passes_turn_protection(tmp_path):
    # R11-4 (should): the same sibling passes production turn protection with no refusal and no launch.
    with _geometry_project(_h_k().parent) as project:
        run = _geometry_run(project, tmp_path)
        prot = sandbox.resolve_turn_protection(project, run_dir=run)
        assert kb.confinement_refusals(prot, run) == []


def test_r10b_f7_discovery_never_hashes_a_symlink(tmp_path):
    root = tmp_path / 'K'
    root.mkdir()
    home = tmp_path / 'agent'
    (home / '.claude').mkdir(parents=True)
    target = tmp_path / 'elsewhere.json'
    target.write_text('{"x": 1}', encoding='utf-8')
    (home / '.claude' / 'settings.json').symlink_to(target)
    (root / 'CLAUDE.md').symlink_to(target)
    found = kb.discovery_inputs(root, home)
    assert found['home-claude-settings'] == {}
    assert found['CLAUDE.md'] is None


# F3 (adopted from the reviewer's test_rv10_extra.py, sha256 430ff1dd...): covered above as test_r10b_f3_*.


# r10b F9 gap closure (the r10 mutants rv10m2 and rv10m10 survived the r10 suite; see reviews/k-task8-binding-impl.md):
# the launch wrapper is called directly with a test-owned env dict, so the identity of the handed-over object is observed.

def test_r10b_f1_the_launch_gate_refuses_a_bad_token_file_before_any_start(k_project, tmp_path, monkeypatch):
    _probe_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    protection = sandbox.resolve_turn_protection(k_project, run_dir=run)
    (sandbox.credentials_dir() / 'claude.token').chmod(0o644)  # a bad mode, read only through lstat
    started = []
    monkeypatch.setattr(jobs, 'start', lambda project, argv, **kwargs: started.append(argv))  # start_orig: must not run
    caller_env = {kb.CREDENTIAL_VAR: 'k8-fake-token-env', 'HOME': str(sandbox.agent_home_path(run, 'claude'))}
    with kb.probe_controls('P1', {}, project=k_project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=run):
        with pytest.raises(kb.ProbeRefused, match='token-file-mode'):
            jobs.start(k_project, ['claude'], kind='probe', cwd=run, run_dir=run, env=caller_env,
                       sandbox_writable=list(protection.writable), sandbox_mask=list(protection.masks),
                       sandbox_layout=None)
    assert started == []


def test_r10b_f5_the_launch_is_handed_a_copy_and_the_callers_env_keeps_no_effort_key(k_project, tmp_path, monkeypatch):
    _probe_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    protection = sandbox.resolve_turn_protection(k_project, run_dir=run)
    monkeypatch.setattr(kb, 'layout_refusals', lambda *args, **kwargs: [])  # isolates the env contract from the layout gate
    handed = {}

    def recording_start(project, argv, **kwargs):
        handed['env'] = kwargs['env']
        raise RuntimeError('start-stopped-by-test')

    monkeypatch.setattr(jobs, 'start', recording_start)  # becomes start_orig when the probe controls enter
    caller_env = {kb.CREDENTIAL_VAR: 'k8-fake-token-env', 'HOME': str(sandbox.agent_home_path(run, 'claude'))}
    with kb.probe_controls('P1', {}, project=k_project, truncate_after_s=2.0, cancel_after_s=2.0,
                           cancel_enabled=False, truncate_enabled=False, run_dir=run):
        with pytest.raises(RuntimeError, match='start-stopped-by-test'):
            jobs.start(k_project, ['claude'], kind='probe', cwd=run, run_dir=run, env=caller_env,
                       sandbox_writable=list(protection.writable), sandbox_mask=list(protection.masks),
                       sandbox_layout=None)
    assert handed['env'] is not caller_env  # the launch never receives the caller's own object
    assert kb.PINNED_EFFORT_ENV[0] not in caller_env  # the caller's dict is untouched after the launch
    assert handed['env'].get(kb.PINNED_EFFORT_ENV[0]) == kb.PINNED_EFFORT_ENV[1]  # the copy carries the effort


# ---- R12 (addendum r12): realpath projection, named mismatch, zero-launch refusal ----

def _r12_setup(k_project, tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    return mode, session, resume


def test_r12_1_projection_is_the_realpath_of_a_symlinked_launcher(k_project, tmp_path):
    mode, session, resume = _r12_setup(k_project, tmp_path)
    target = (mode.parent / 'claude').resolve()  # test-known: the path the test itself created
    link = mode.parent / 'fake-install' / 'claude'
    assert link.is_symlink() and str(link) != str(target)
    expected = kb.expected_identity_for_fixture(k_project, session_template=session, resume_template=resume)
    assert expected.executable == str(target)
    assert expected.executable != str(link)  # the lexical launcher is NOT the projection (R12-1)
    resolved = agent_run.resolve_turn(k_project, 'claude', run_dir=k_project.run_dir(kb.RUN_SLUG), session=None)
    assert resolved.harness_identity.executable == expected.executable


def test_r12_2_marked_p1_prepare_passes_with_a_symlinked_launcher(k_project, tmp_path):
    _mode, session, resume = _r12_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    resolved = agent_run.resolve_turn(k_project, 'claude', run_dir=run, session=None)
    expected = kb.expected_identity_for_fixture(k_project, session_template=session, resume_template=resume)
    with kb.support_bound(expected):
        submission = HumanSubmission(kb.CANDIDATE, 'harness-command',
                                     conversation.selection_revision(conversation.read(run)))
        prepared = agent_run.prepare_turn(resolved, submission)
    assert prepared.policy_revision == kb.POLICY_REVISION


def test_r12_3_mismatch_names_the_field_and_never_falls_through(k_project, tmp_path):
    mode, session, resume = _r12_setup(k_project, tmp_path)
    run = k_project.run_dir(kb.RUN_SLUG)
    resolved = agent_run.resolve_turn(k_project, 'claude', run_dir=run, session=None)
    original = harness_commands.support_for
    lexical = kb.expected_identity(executable=str(mode.parent / 'fake-install' / 'claude'),
                                   session_template=session, resume_template=resume,
                                   model=kb.PINNED_MODEL, cwd=resolved.cwd)
    with kb.support_bound(lexical):
        with pytest.raises(kb.ProbeRefused) as err:
            harness_commands.support_for(resolved.harness_identity)
        assert str(err.value) == 'binding-identity-mismatch:executable'
    good = kb.expected_identity_for_fixture(k_project, session_template=session, resume_template=resume)
    with kb.support_bound(good):
        wrong = dataclasses.replace(resolved.harness_identity, model='claude-opus-5-5-x')
        with pytest.raises(kb.ProbeRefused) as err2:
            harness_commands.support_for(wrong)
        assert str(err2.value) == 'binding-identity-mismatch:model'  # names the field, never values
    assert harness_commands.support_for is original
    assert harness_commands.CERTIFIED_POLICIES == ()


def test_r12_3b_run_probe_with_lexical_identity_names_the_field_and_launches_nothing(k_project, tmp_path,
                                                                                     monkeypatch):
    mode, session, resume = _r12_setup(k_project, tmp_path)
    launches = []
    real_start = jobs.start

    def spy(*args, **kwargs):
        launches.append(args)
        return real_start(*args, **kwargs)

    monkeypatch.setattr(jobs, 'start', spy)
    lexical = kb.expected_identity(executable=str(mode.parent / 'fake-install' / 'claude'),
                                   session_template=session, resume_template=resume, model=kb.PINNED_MODEL,
                                   cwd=Path(k_project.root).resolve())
    binding = kb.ProbeBinding(k_project, expected=lexical, claude_binary=FIXTURE)
    with pytest.raises(Exception) as err:
        binding.run_probe('P1')
    chain, current = [], err.value
    while current is not None and len(chain) < 8:
        chain.append(str(current))
        current = current.__cause__ or current.__context__
    assert launches == []
    assert 'binding-identity-mismatch:executable' in chain, chain


# ---- R13 (addendum r13): launch tool restriction, init tools, transcript interception, stop timings ----

R13_SID = '0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b'
R13_STARTED = '2026-10-21T17:30:00.000+00:00'
R13_LATER = '2026-10-21T17:31:00.000Z'
R13_PROJECTS = Path('scratch') / 'agent-home' / 'claude' / '.claude' / 'projects'


def _r13_skill_dir(project_root, name='scieflow-menu'):
    return str(Path(project_root) / '.claude' / 'skills' / name)


def _r13_cmd(name, ts=R13_LATER, prompt='p-1', sid=R13_SID):
    return {'type': 'user', 'sessionId': sid, 'timestamp': ts, 'promptId': prompt, 'isMeta': False,
            'message': {'role': 'user',
                        'content': f'<command-name>/{name}</command-name>\n<command-message>{name}</command-message>'}}


def _r13_meta(skill_dir, ts=R13_LATER, prompt='p-1', sid=R13_SID):
    return {'type': 'user', 'sessionId': sid, 'timestamp': ts, 'promptId': prompt, 'isMeta': True,
            'message': {'role': 'user',
                        'content': [{'type': 'text', 'text': f'Base directory for this skill: {skill_dir}\n\nbody'}]}}


def _r13_pair(project_root, ts=R13_LATER, prompt='p-1', sid=R13_SID):
    return [_r13_cmd('scieflow-menu', ts, prompt, sid), _r13_meta(_r13_skill_dir(project_root), ts, prompt, sid)]


def _r13_write(run_dir, records, name=R13_SID + '.jsonl', *, subdir=R13_PROJECTS / '-synthetic-proj'):
    path = Path(run_dir) / subdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    return path


def _r13_append(path, records):
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(''.join(json.dumps(r) + '\n' for r in records))


def _r13_call(run_dir, project_root, sizes_before=None, sid=R13_SID, started=R13_STARTED):
    return kb.transcript_interception(run_dir, sid, started, sizes_before or {}, project_root=project_root)


def _r13_find_claude_cmds(node):
    if isinstance(node, dict):
        if isinstance(node.get('session_cmd'), str) and node['session_cmd'].startswith('claude -p'):
            yield node['session_cmd'], node['resume_cmd']
        for value in node.values():
            yield from _r13_find_claude_cmds(value)
    elif isinstance(node, list):
        for value in node:
            yield from _r13_find_claude_cmds(value)


def test_r13_1_tools_arg_is_exact_and_set_equal_to_allowed():
    assert kb.TOOLS_ARG == '--tools=Read,Glob,Grep,Skill'
    assert ' ' not in kb.TOOLS_ARG
    assert set(kb.TOOLS_ARG.split('=', 1)[1].split(',')) == kb.ALLOWED_TOOLS


def test_r13_1_templates_are_production_plus_tools_arg_computed_from_yaml():
    import yaml
    cfg = yaml.safe_load((W / 'config' / 'agents.yml').read_text(encoding='utf-8'))
    prod = list(_r13_find_claude_cmds(cfg))
    assert prod, 'production claude session_cmd/resume_cmd not found'
    session, resume = prod[0]

    def with_tools(template):
        return template.replace('--dangerously-skip-permissions ',
                                f'--dangerously-skip-permissions {kb.TOOLS_ARG} ', 1)
    assert kb.SESSION_TEMPLATE == with_tools(session)
    assert kb.RESUME_TEMPLATE == with_tools(resume)


def test_r13_1_expected_argv_puts_tools_arg_after_skip_and_disable_before_model():
    sid = '11111111-2222-3333-4444-555555555555'
    for probe in ('P1', 'P2', 'P3', 'P4', 'P5'):
        spec = kb.PROBE_SPEC[probe]
        argv = kb.expected_argv(probe, session=sid if spec['resume'] else None, model='claude-opus-5-5')
        slot = argv.index('--dangerously-skip-permissions') + (2 if spec['disable'] else 1)
        assert argv[slot] == kb.TOOLS_ARG, (probe, argv)
        assert argv[slot + 1] == '--model', (probe, argv)


def test_r13_1_fixture_init_reports_exactly_the_tools_arg_set(tmp_path):
    mode = _mode_file(tmp_path)
    mode.write_text('normal\n', encoding='utf-8')
    argv = kb.expected_argv('P1', session=None, model='claude-opus-5-5')
    argv = [PY, '-B', str(FIXTURE), '--mode-file', str(mode)] + argv[1:-1] + ['hello']
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    init = json.loads(proc.stdout.splitlines()[0])
    assert init['type'] == 'system' and init['subtype'] == 'init'
    assert sorted(init['tools']) == sorted(kb.ALLOWED_TOOLS)


def test_r13_2_init_tools_come_from_the_first_init_event():
    events = [{'type': 'system', 'subtype': 'init', 'tools': ['Read', 'Glob', 'Grep', 'Skill']},
              {'type': 'system', 'subtype': 'init', 'tools': ['Bash']}]
    assert kb.init_tools_of(events) == ['Read', 'Glob', 'Grep', 'Skill']
    assert kb.init_tools_of([{'type': 'assistant'}]) is None


def test_r13_3_expansion_pair_is_intercepted_with_no_stream_tool_use(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, _r13_pair(K))
    result = _r13_call(run, K)
    assert result['evidence'] == [{'source': 'session-transcript', 'kind': 'slash_command_expansion',
                                   'name': 'scieflow-menu', 'skill_dir': _r13_skill_dir(K)}]
    assert result['other_commands'] == []
    assert set(result['transcript']) == {'found', 'relpath', 'size_before', 'size_after',
                                         'records_considered', 'unexpanded', 'error'}
    assert result['transcript']['found'] is True and result['transcript']['error'] is None
    assert result['transcript']['records_considered'] == 2 and result['transcript']['unexpanded'] == 0


def test_r13_3_command_without_meta_record_is_counted_not_intercepted(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, [_r13_cmd('scieflow-menu')])
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['unexpanded'] == 1


def test_r13_3_records_older_than_started_are_ignored(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, _r13_pair(K, ts='2026-10-21T17:00:00.000Z'))
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['records_considered'] == 0


def test_r13_3_copied_history_before_the_size_offset_is_ignored(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    path = _r13_write(run, _r13_pair(K, prompt='p-old'))
    relpath = str(path.relative_to(run))
    before = {relpath: path.stat().st_size}
    _r13_append(path, _r13_pair(K, prompt='p-new'))
    result = _r13_call(run, K, sizes_before=before)
    assert len(result['evidence']) == 1
    assert result['transcript']['size_before'] == before[relpath]


def test_r13_3_records_of_another_session_are_ignored(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, _r13_pair(K, sid='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'))
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['records_considered'] == 0


def test_r13_3_other_command_is_named_in_other_commands(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, [_r13_cmd('workspace-sync'), _r13_meta(_r13_skill_dir(K, 'workspace-sync'))])
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['other_commands'] == ['workspace-sync']


def test_r13_3_assistant_prose_naming_the_expansion_is_not_evidence(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    prose = {'type': 'assistant', 'sessionId': R13_SID, 'timestamp': R13_LATER, 'promptId': 'p-1',
             'message': {'role': 'assistant', 'content': [{'type': 'text', 'text':
                         '<command-name>/scieflow-menu</command-name> Base directory for this skill: x'}]}}
    _r13_write(run, [prose])
    result = _r13_call(run, K)
    assert result['evidence'] == []


@pytest.mark.parametrize('kind', ['file', 'projects'])
def test_r13_3_symlinked_transcript_or_projects_gives_no_evidence_and_an_error(tmp_path, kind):
    K, run = tmp_path / 'K', tmp_path / 'run'
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    real = elsewhere / f'{R13_SID}.jsonl'
    real.write_text(''.join(json.dumps(r) + '\n' for r in _r13_pair(K)), encoding='utf-8')
    if kind == 'file':
        proj = run / R13_PROJECTS / '-synthetic-proj'
        proj.mkdir(parents=True)
        (proj / f'{R13_SID}.jsonl').symlink_to(real)
    else:
        (run / R13_PROJECTS).mkdir(parents=True)
        (run / R13_PROJECTS / '-synthetic-proj').symlink_to(elsewhere, target_is_directory=True)
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['error']


def test_r13_3_oversized_transcript_gives_no_evidence_and_an_error(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    path = _r13_write(run, [])
    with open(path, 'ab') as handle:
        handle.write(b' ' * (8 * 1024 * 1024 + 1))
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['error']


def test_r13_3_two_candidates_give_no_evidence_and_an_error(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, _r13_pair(K), subdir=R13_PROJECTS / '-one')
    _r13_write(run, _r13_pair(K), subdir=R13_PROJECTS / '-two')
    result = _r13_call(run, K)
    assert result['evidence'] == []
    assert result['transcript']['error']


def test_r13_3_malformed_session_id_finds_no_transcript(tmp_path):
    K, run = tmp_path / 'K', tmp_path / 'run'
    _r13_write(run, _r13_pair(K), name='not-a-uuid.jsonl')
    result = _r13_call(run, K, sid='not-a-uuid')
    assert result['evidence'] == []
    assert result['transcript']['found'] is False


def test_r13_3_p1_expansion_through_the_fixture_is_intercepted(k_project, tmp_path):
    K = Path(k_project.root)
    mode = _mode_file(tmp_path)
    mode.write_text(f'expand\n{_r13_skill_dir(K)}\n', encoding='utf-8')
    session, resume = _fixture_templates(mode)
    kb.setup_probe_run(k_project, session_template=session, resume_template=resume,
                       timeout_min=0.1, model='claude-opus-5-5')
    binding = kb.ProbeBinding(k_project, expected=kb.expected_identity_for_fixture(
        k_project, session_template=session, resume_template=resume))
    report = binding.run_probe('P1')
    assert report['job_state'] == 'done'
    assert report['tools_used'] == [], 'the expansion fixture writes no stream-json Skill tool_use'
    assert sorted(report['init_tools']) == sorted(kb.ALLOWED_TOOLS)  # the CLI lists tools in launch order
    assert report['interception']['evidence'] == [{'source': 'session-transcript',
                                                   'kind': 'slash_command_expansion',
                                                   'name': 'scieflow-menu', 'skill_dir': _r13_skill_dir(K)}]


def test_r13_4_stop_timings_are_six_seconds_and_below_the_p8_timeout():
    assert kb.DEFAULT_TRUNCATE_S == 6.0
    assert kb.DEFAULT_CANCEL_S == 6.0


# ---- R14 (addendum r14): production-unusable turns, named pre-launch refusals, K re-pin, record shape ----

R14_USABLE = {'parse_complete': True, 'reason': ''}


def _r14_refuse(*args, **kwargs):
    raise service.ServiceError('synthetic pre-launch refusal')


def test_r14_1_binding_report_carries_the_production_turn_audit(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path)
    raw = binding.run_probe('P1')
    assert raw['turn_audit'] == R14_USABLE
    assert raw['recovery_required'] is False
    assert kb.to_harness_report(raw, agents_sha256=AGENTS_SHA)['turn_audit'] == R14_USABLE


def test_r14_1_binding_records_record_limit_for_an_oversize_assistant_record(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'big-assistant')
    raw = binding.run_probe('P1')
    assert raw['turn_audit'] == {'parse_complete': False, 'reason': 'record-limit'}
    assert raw['recovery_required'] is True


def test_r14_2_resume_refused_before_launch_when_recovery_is_required(k_project, tmp_path, monkeypatch):
    from scieflow.core.run import conversation
    binding, _ = _probe_setup(k_project, tmp_path, 'big-assistant')
    binding.run_probe('P1')  # production refuses this marked turn and records recovery_required
    assert conversation.read(binding.run_dir).get('recovery_required') is True
    starts = _spy_starts(monkeypatch)
    with pytest.raises(kb.ProbeRefused, match='^conversation-recovery-required$'):
        binding.run_probe('P3')
    assert starts == []


def test_r14_2_resume_refused_before_launch_without_a_session(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    starts = _spy_starts(monkeypatch)
    with pytest.raises(kb.ProbeRefused, match='^conversation-no-session$'):
        binding.run_probe('P3')
    assert starts == []


def test_r14_2_service_refusal_before_launch_is_a_named_refusal(k_project, tmp_path, monkeypatch):
    binding, _ = _probe_setup(k_project, tmp_path)
    monkeypatch.setattr(service, 'say', _r14_refuse)
    with pytest.raises(kb.ProbeRefused, match='^service-refused-before-launch$') as info:
        binding.run_probe('P1')
    assert str(info.value) == 'service-refused-before-launch'
    assert isinstance(info.value.__cause__, service.ServiceError)


def test_r14_2_matrix_status_for_a_resume_refusal_is_named(k_project, tmp_path):
    binding, mode_file = _probe_setup(k_project, tmp_path, 'big-assistant')
    binding.run_probe('P1')  # leaves recovery_required true; P1 is taken as already done
    out = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN, reports=[{'probe': 'P1', 'usable': True, 'status': 'usable'}])
    assert out['status'] == 'refused:conversation-recovery-required'


def test_r14_2_matrix_status_for_a_pre_launch_service_refusal_is_named(k_project, tmp_path, monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    monkeypatch.setattr(service, 'say', _r14_refuse)
    out = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {}), agents_sha256=AGENTS_SHA,
                          claude_pin=PIN)
    assert out['status'] == 'refused:service-refused-before-launch'
    assert not out['status'].startswith('exception:')


def test_r14_4_big_tool_result_record_is_a_usable_turn_end_to_end(k_project, tmp_path):
    binding, _ = _probe_setup(k_project, tmp_path, 'big-tool-result')
    raw = binding.run_probe('P1')
    longest = max(len(line.encode('utf-8')) for line in raw['stream_text'].splitlines())
    assert longest >= 28336
    assert raw['recovery_required'] is False
    assert raw['turn_audit'] == R14_USABLE
    expected = kb.expected_harness_identity(binding.expected, binary_sha256=FIXTURE_SHA, agents_sha256=AGENTS_SHA)
    reasons = _gate().validate_report(kb.to_harness_report(raw, agents_sha256=AGENTS_SHA), expected, 'P1')
    assert reasons == []


def test_r14_4_big_assistant_is_refused_by_production_and_the_matrix_stops_at_p1(k_project, tmp_path,
                                                                                 monkeypatch):
    binding, mode_file = _probe_setup(k_project, tmp_path)
    starts = _spy_starts(monkeypatch)
    out = kb.run_sequence(binding, _gate(), mode_for=_modes(mode_file, {'P1': 'big-assistant'}),
                          agents_sha256=AGENTS_SHA, claude_pin=PIN, checkpoint_path=tmp_path / 'c.json')
    assert out['status'].startswith('refused:production-turn-unusable:')
    assert [e['probe'] for e in out['reports']] == ['P1']
    reasons = out['reports'][0]['reasons']
    assert 'production-turn-unusable:record-limit' in reasons
    assert 'production-turn-unusable:recovery-required' in reasons
    assert len(starts) == 1
