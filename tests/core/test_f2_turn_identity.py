"""F-2 t2: identity capture for marked turns and effort delivery (plan 5.4, 5.5, 6 t2).

All fixtures are synthetic and hermetic: the launcher is a test-owned script in a temporary
`versions/9.9.9` folder, the pairing and versions folder are monkeypatched to it, and no real
home, probe tree or agent CLI is read or launched.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, events, harness_commands, harness_identity, jobs, sandbox, service
from scieflow.core.run import charter, conversation
from scieflow.core.turn_input import accept_human_submission

PROTOCOL = Path(__file__).resolve().parents[1] / 'fixtures' / 'k_conversation_protocol.py'
CLAUDE_AGENT = 'k-claude-fixture'
KNOWN_HOOK = harness_identity.KNOWN_HOOK_COMMANDS[0]


def _credentials(root: Path, agents) -> None:
    token = sandbox.credentials_dir() / 'fixture.token'
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text('fake-token-fixture\n')
    token.chmod(0o600)
    entry = {'mode': 'env', 'var': 'SCIEFLOW_FIXTURE_TOKEN', 'file': 'fixture.token',
             'reason': 'fake fixture token'}
    (root / 'config' / 'sandbox.yml').write_text(
        yaml.safe_dump({'credentials': {a: dict(entry) for a in agents}}))


def _add_claude_agent(project, tmp_path, monkeypatch, *, reasoning='extended-thinking',
                      prefix=False, family='claude', name=CLAUDE_AGENT):
    """A claude-family turn agent; its launcher is the versioned file versions/9.9.9 (F2-D4)."""
    versions = (tmp_path / 'claude-home' / '.local' / 'share' / 'claude' / 'versions').resolve()
    launcher = versions / '9.9.9'
    versions.mkdir(parents=True, exist_ok=True)
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} -B {PROTOCOL} "$@"\n')
    launcher.chmod(0o755)
    digest = hashlib.sha256(launcher.read_bytes()).hexdigest()
    monkeypatch.setattr(harness_identity, 'claude_versions_dir', lambda: versions)
    monkeypatch.setattr(harness_identity, 'VERSION_PAIRINGS', {digest: '9.9.9'})
    cfg = {'cmd': f'{launcher} first {{prompt}}',
           'session_cmd': f'{launcher} first {{prompt}}',
           'resume_cmd': f'{launcher} --resume {{session}} resume {{prompt}}',
           'family': family, 'enabled': True, 'timeout_min': 1,
           'model': 'fixture'}
    if prefix:   # the legacy shape: extended thinking as an env prefix on cmd only, no reasoning key
        cfg['cmd'] = f'env MAX_THINKING_TOKENS=32000 {launcher} first {{prompt}}'
    else:
        cfg['reasoning'] = reasoning
    block = ''.join('    ' + line + '\n' for line in yaml.safe_dump(cfg).splitlines())
    path = project.root / 'config' / 'agents.yml'
    path.write_text(path.read_text() + f'  {name}:\n{block}')
    _credentials(project.root, ['k-protocol-fixture', name])
    return launcher


def _complete_claude_tree(root: Path) -> None:
    """The files the settings, extensions and discovery records read (synthetic, bounded)."""
    (root / 'AGENTS.md').write_text('agents\n')
    (root / '.claude' / 'skills').mkdir(parents=True)
    (root / '.claude' / 'settings.json').write_text(json.dumps(
        {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': KNOWN_HOOK}]}]}}))
    (root / 'scripts' / 'hooks').mkdir(parents=True)
    (root / 'scripts' / 'hooks' / 'dvc-sync-reminder.py').write_text('print("synthetic")\n')
    # One in-project skill link (plan 5.2): a symlink under .claude/skills to a folder of regular files.
    skill = root / 'skills' / 'fixture-skill'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('synthetic skill\n')
    os.symlink(os.path.relpath(skill, root / '.claude' / 'skills'),
               root / '.claude' / 'skills' / 'fixture-skill')


def _tree_digest(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            out[str(path.relative_to(root)) + '/'] = None
    return out


def test_default_resolution_keeps_the_uncaptured_identity(k_conversational_project):
    project, ws, _ = k_conversational_project
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    identity = resolved.harness_identity
    assert identity.discovery_sha256 is None and identity.extensions_sha256 is None
    assert identity.settings_sha256 is None and identity.executable_sha256 is None
    assert identity.version is None and identity.effects_pinned is False
    assert identity.reasoning == 'high'


def test_marked_claude_turn_captures_the_full_identity(k_conversational_project, tmp_path,
                                                       monkeypatch):
    project, ws, _ = k_conversational_project
    launcher = _add_claude_agent(project, tmp_path, monkeypatch)
    _complete_claude_tree(project.root)
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                                      capture_identity=True)
    identity = resolved.harness_identity
    assert identity.executable == str(launcher.resolve())
    assert identity.executable_sha256 == hashlib.sha256(launcher.read_bytes()).hexdigest()
    assert identity.version == '9.9.9'
    assert identity.reasoning == 'env:MAX_THINKING_TOKENS=32000'
    assert identity.cwd == str(project.root.resolve())
    assert None not in (identity.discovery_sha256, identity.extensions_sha256,
                        identity.settings_sha256)
    assert identity.effects_pinned is True
    # The production tuple does not match this synthetic identity: support stays disabled.
    support = harness_commands.support_for(identity)
    assert support.enabled is False
    assert support.reason == 'No exact certified /scieflow-menu policy for this harness identity'
    marked = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    with pytest.raises(agent_run.DispatchError):
        agent_run.prepare_turn(resolved, marked)


def test_unsandboxed_marked_turn_is_incomplete(k_conversational_project, tmp_path, monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch)
    _complete_claude_tree(project.root)
    (project.root / 'config' / 'sandbox.yml').write_text(
        'unsandboxed_runs:\n  - slug: r1\n    reason: human\n')
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                                      capture_identity=True)
    identity = resolved.harness_identity
    assert identity.discovery_sha256 is None
    assert identity.effects_pinned is False
    assert harness_commands.support_for(identity).enabled is False


def test_effort_is_delivered_only_in_the_turn_environment(k_conversational_project, tmp_path,
                                                          monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch)
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None)
    assert dict(resolved.env_items)['MAX_THINKING_TOKENS'] == '32000'
    chat = accept_human_submission('hello', 'chat', '0')
    prepared = agent_run.prepare_turn(resolved, chat)
    assert dict(prepared.env_items)['MAX_THINKING_TOKENS'] == '32000'
    assert not any('MAX_THINKING_TOKENS' in str(token) for token in prepared.argv)
    # The uncaptured production identity keeps today's label; only a captured identity names it.
    assert resolved.harness_identity.reasoning == 'extended-thinking'
    captured = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                                      capture_identity=True)
    assert captured.harness_identity.reasoning == 'env:MAX_THINKING_TOKENS=32000'


def test_legacy_cmd_prefix_still_delivers_the_effort_by_environment(k_conversational_project,
                                                                    tmp_path, monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch, prefix=True)
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                                      capture_identity=True)
    assert dict(resolved.env_items)['MAX_THINKING_TOKENS'] == '32000'
    assert resolved.harness_identity.reasoning == 'env:MAX_THINKING_TOKENS=32000'


def test_default_reasoning_adds_nothing_even_with_a_host_value(k_conversational_project,
                                                               tmp_path, monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch, reasoning='default')
    monkeypatch.setenv('MAX_THINKING_TOKENS', '999')
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None)
    assert 'MAX_THINKING_TOKENS' not in dict(resolved.env_items)
    assert resolved.harness_identity.reasoning == 'default'


def test_capture_adds_no_resolution_work_and_writes_nothing(k_conversational_project, tmp_path,
                                                            monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch)
    _complete_claude_tree(project.root)
    counts = {'execution': 0, 'protection': 0, 'charter': 0}

    def counting(name, real):
        def wrapper(*args, **kwargs):
            counts[name] += 1
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(agent_run, '_capture_execution',
                        counting('execution', agent_run._capture_execution))
    monkeypatch.setattr(sandbox, 'resolve_turn_protection',
                        counting('protection', sandbox.resolve_turn_protection))
    monkeypatch.setattr(charter, 'snapshot', counting('charter', charter.snapshot))
    before = _tree_digest(ws)
    agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None)
    plain = dict(counts)
    for key in counts:
        counts[key] = 0
    agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                           capture_identity=True)
    assert counts == plain
    assert _tree_digest(ws) == before


def test_service_say_passes_the_capture_flag_only_for_marked_submissions(
        k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    token = conversation.selection_revision(conversation.read(ws))
    seen = []
    real = agent_run.resolve_turn

    def spy(*args, **kwargs):
        seen.append(kwargs.get('capture_identity'))
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_run, 'resolve_turn', spy)
    marked = accept_human_submission('/scieflow-menu', 'harness-command', token)
    errors = []
    for submission in (marked, accept_human_submission('hello', 'chat', token)):
        try:
            service.say(project, 'r1', submission.message, human_submission=submission)
        except Exception as exc:  # the turn may refuse; the flag is what this test records
            errors.append(repr(exc)[:300])
    assert seen[:1] == [True], errors
    assert seen[1:] == [False], errors


def test_marked_turn_prepares_once_the_captured_identity_is_bound(k_conversational_project,
                                                                  offline_k_support):
    project, ws, _ = k_conversational_project
    _complete_claude_tree(project.root)
    resolved = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None,
                                      capture_identity=True)
    identity = resolved.harness_identity
    assert identity.agent == 'k-protocol-fixture' and identity.extensions_sha256 is not None
    support = harness_commands.support_for(identity)
    assert support.enabled is False and support.reason
    marked = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    with pytest.raises(agent_run.DispatchError, match=re.escape(support.reason)):
        agent_run.prepare_turn(resolved, marked)
    offline_k_support(identity)
    prepared = agent_run.prepare_turn(resolved, marked)
    assert prepared.policy_revision == 'fixture-only'
    assert prepared.composition_order == 'command-first'
    assert not any('MAX_THINKING_TOKENS' in str(token) for token in prepared.argv)


def test_sandboxed_turn_launch_carries_effort_in_env_only(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    path = project.root / 'config' / 'agents.yml'
    assert path.read_text().count('reasoning: high') == 1
    path.write_text(path.read_text().replace('reasoning: high', 'reasoning: extended-thinking'))
    monkeypatch.setenv('MAX_THINKING_TOKENS', '999')
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    launches = []
    real_start = jobs.start

    def start(*args, **kwargs):
        launches.append((list(args[1]), dict(kwargs)))
        return real_start(*args, **kwargs)

    monkeypatch.setattr(jobs, 'start', start)
    chat = accept_human_submission('hello', 'chat',
                                   conversation.selection_revision(conversation.read(ws)))
    service.say(project, 'r1', 'hello', human_submission=chat)
    assert len(launches) == 1
    argv, kwargs = launches[0]
    assert kwargs['env']['MAX_THINKING_TOKENS'] == '32000'
    assert not any('MAX_THINKING_TOKENS' in str(token) for token in argv)


def test_unsandboxed_turn_gets_effort_only_from_the_setting(k_conversational_project, tmp_path,
                                                            monkeypatch):
    project, ws, _ = k_conversational_project
    _add_claude_agent(project, tmp_path, monkeypatch)
    (project.root / 'config' / 'sandbox.yml').write_text(
        'unsandboxed_runs:\n  - slug: r1\n    reason: human\n')
    monkeypatch.setenv('MAX_THINKING_TOKENS', '999')
    resolved = agent_run.resolve_turn(project, CLAUDE_AGENT, run_dir=ws, session=None,
                                      capture_identity=True)
    assert resolved.protection.sandbox_enabled is False
    assert dict(resolved.env_items)['MAX_THINKING_TOKENS'] == '32000'
    assert resolved.harness_identity.reasoning == 'env:MAX_THINKING_TOKENS=32000'


@pytest.mark.parametrize('family', ['codex', 'agy'])
def test_non_claude_family_adds_no_effort_even_with_a_host_value(k_conversational_project,
                                                                 tmp_path, monkeypatch, family):
    project, ws, _ = k_conversational_project
    name = 'k-other-fixture'
    _add_claude_agent(project, tmp_path, monkeypatch, family=family, name=name)
    monkeypatch.setenv('MAX_THINKING_TOKENS', '999')
    resolved = agent_run.resolve_turn(project, name, run_dir=ws, session=None)
    assert 'MAX_THINKING_TOKENS' not in dict(resolved.env_items)
    assert resolved.harness_identity.reasoning == 'extended-thinking'


def test_marked_and_ordinary_turns_record_the_same_audit_and_job_shape(
        k_conversational_project, offline_k_support, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    real_resolve = agent_run.resolve_turn

    def resolve(*args, **kwargs):
        value = real_resolve(*args, **kwargs)
        if kwargs.get('capture_identity'):
            offline_k_support(value.harness_identity)
        return value

    def finish(prepared, job, *, artifacts):
        records = [json.loads(line) for line in Path(job.log).read_text().splitlines()]
        assert job.exit_code == 0
        assert records[-1]['input_sha256'] == hashlib.sha256(prepared.composed_bytes).hexdigest()
        return service._TurnCompletion(records[0]['session_id'], records[-1]['result'], True, '')

    monkeypatch.setattr(agent_run, 'resolve_turn', resolve)
    monkeypatch.setattr(service, '_complete_marked_turn', finish)
    spans = []
    real_start = jobs.start

    def start(*args, **kwargs):
        spans.append(sorted(kwargs))
        return real_start(*args, **kwargs)

    monkeypatch.setattr(jobs, 'start', start)

    def revision():
        return conversation.selection_revision(conversation.read(ws))

    before = len(events.read(ws))
    service.say(project, 'r1', 'hello',
                human_submission=accept_human_submission('hello', 'chat', revision()))
    middle = len(events.read(ws))
    service.say(project, 'r1', '/scieflow-menu',
                human_submission=accept_human_submission('/scieflow-menu', 'harness-command',
                                                         revision()))
    everything = events.read(ws)
    chat_types = [event['type'] for event in everything[before:middle]]
    marked_types = [event['type'] for event in everything[middle:]]
    human = [turn for turn in conversation.read(ws)['turns'] if turn['role'] == 'human']
    assert [turn['audit']['input_kind'] for turn in human] == ['chat', 'harness-command']
    assert set(human[0]['audit']) == set(human[1]['audit'])
    assert len(spans) == 2 and spans[0] == spans[1]
    assert marked_types == chat_types
    identity_keys = {'harness_identity', 'reasoning', 'discovery_sha256', 'executable_sha256'}
    assert not any(identity_keys & set(turn['audit']) for turn in human)

