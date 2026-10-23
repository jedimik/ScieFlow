"""Task4 behavioral state/snapshot/recovery controls; inert, not certification."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scieflow.core import agent_run, events, jobs, service, store
from scieflow.core.run import conversation


def audit(turn_id, *, kind='harness-command', revision='0'):
    return dict(schema_version=1, turn_id=turn_id, input_kind=kind,
                composition_order='command-first' if kind == 'harness-command' else 'charter-first',
                selected_manager='fixture', family='claude', policy_revision='fixture-only',
                message_sha256='a' * 64, charter_version=0, charter_sha256='b' * 64,
                composed_artifact=f'logs/turn-{turn_id}.composed.md', job_id='',
                parse_complete=None, reason='', accepted_revision=revision, final_revision=None)


def test_t4_legacy_read_default_is_nonmutating(project):
    ws = project.run_dir('r1')
    path = ws / 'conversation.yml'
    path.write_text('agent: fixture\nsession: old\nturns: []\n')
    before = path.read_bytes()
    doc = conversation.read(ws)
    assert doc.get('selection_generation') == 0
    assert doc['recovery_required'] is False and doc['pending_turn_id'] is None
    assert conversation.selection_revision(doc) == '0'
    assert path.read_bytes() == before


def test_t4_every_central_mutation_increments(project):
    ws = project.run_dir('r1')
    for name, expected in [('A', 1), ('B', 2), ('A', 3), ('A', 4)]:
        doc = conversation.set_agent(ws, name)
        assert doc.get('selection_generation') == expected
    conversation.record_session(ws, 'keep')
    assert conversation.read(ws)['selection_generation'] == 5
    before = (ws / 'conversation.yml').read_bytes()
    conversation.record_session(ws, None)
    assert (ws / 'conversation.yml').read_bytes() == before
    conversation.add_turn(ws, role='human', text='hi')
    conversation.add_turn(ws, role='agent', text='reply')
    assert conversation.read(ws)['selection_generation'] == 7
    conversation.set_agent(ws, 'A', expected_revision='7', reset_session=True)
    doc = conversation.read(ws)
    assert doc['session'] is None and doc['selection_generation'] == 8
    conversation.set_agent(ws, 'A', expected_revision='8', reset_session=True)
    assert conversation.read(ws)['selection_generation'] == 9


@pytest.mark.parametrize('value', [True, -1, '0', 1.5, None, [], {}])
def test_t4_malformed_generation_never_resets(project, value):
    ws = project.run_dir('r1')
    path = ws / 'conversation.yml'
    path.write_text(yaml.safe_dump({'selection_generation': value}))
    before = path.read_bytes()
    for operation in [lambda: conversation.read(ws), lambda: conversation.set_agent(ws, 'A'),
                      lambda: conversation.record_session(ws, 'session'),
                      lambda: conversation.add_turn(ws, role='human', text='hi')]:
        with pytest.raises(conversation.ConversationError):
            operation()
        assert path.read_bytes() == before


@pytest.mark.parametrize('token', ['', '01', '-1', '١', True, 1, '9' * 10000, '0'])
def test_t4_invalid_or_stale_selection_token_is_no_write(project, token):
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'A')
    path = ws / 'conversation.yml'
    before = path.read_bytes()
    with pytest.raises(conversation.ConversationError):
        conversation.set_agent(ws, 'B', expected_revision=token)
    assert path.read_bytes() == before


def test_t4_finalize_atomic_once_and_pending_recovery(project, monkeypatch):
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'fixture')
    conversation.record_session(ws, 'old')
    tid, jid = store.new_id(), store.new_id()
    accepted = audit(tid, revision='2')
    human = conversation.add_turn(ws, role='human', text='/scieflow-menu', turn_id=tid, audit=accepted)
    assert human['finalized'] is False
    assert conversation.read(ws)['pending_turn_id'] == tid
    before = (ws / 'conversation.yml').read_bytes()
    accepted['selected_manager'] = 'mutated'
    assert conversation.read(ws)['turns'][0]['audit']['selected_manager'] == 'fixture'
    done_audit = audit(tid, revision='2')
    done_audit.update(job_id=jid, parse_complete=False, reason='incomplete')
    conversation.annotate_turn(ws, tid, job_id=jid, audit=done_audit)
    assert conversation.read(ws)['selection_generation'] == 3
    real_update = store.update_yaml
    writes = []
    def observe(path, fn):
        writes.append(path)
        return real_update(path, fn)
    monkeypatch.setattr(store, 'update_yaml', observe)
    turn = conversation.finalize_turn(ws, tid, job_id=jid, session_id=None, reply='partial',
                                     state='failed', audit=done_audit, recovery_required=True)
    assert len(writes) == 1
    doc = conversation.read(ws)
    assert doc['selection_generation'] == 4 and doc['pending_turn_id'] is None
    assert doc['session'] is None and doc['recovery_required'] is True
    assert doc['turns'][0]['finalized'] is True
    assert turn['state'] == 'failed' and turn['audit']['final_revision'] == '4'
    assert len(doc['turns']) == 2
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(conversation.ConversationError):
        conversation.finalize_turn(ws, tid, job_id=jid, session_id='fake', reply='duplicate',
                                   state='done', audit=done_audit, recovery_required=False)
    assert (ws / 'conversation.yml').read_bytes() == before
    with pytest.raises(conversation.ConversationError):
        conversation.set_agent(ws, 'other')
    conversation.set_agent(ws, 'fixture', expected_revision='4')
    assert conversation.read(ws)['recovery_required'] is True
    conversation.set_agent(ws, 'other', expected_revision='5')
    assert conversation.read(ws)['recovery_required'] is False
    assert len(conversation.read(ws)['turns']) == 2


@pytest.mark.parametrize('fault', ['unknown', 'ambiguous', 'mismatched-pending', 'bad-audit'])
def test_t4_annotation_finalization_fail_closed(project, monkeypatch, fault):
    ws = project.run_dir('r1')
    tid, jid = store.new_id(), store.new_id()
    a = audit(tid)
    conversation.add_turn(ws, role='human', text='raw', turn_id=tid, audit=a)
    path = ws / 'conversation.yml'
    doc = yaml.safe_load(path.read_text())
    if fault == 'unknown':
        tid = store.new_id()
    elif fault == 'ambiguous':
        doc['turns'].append(dict(doc['turns'][0]))
    elif fault == 'mismatched-pending':
        doc['pending_turn_id'] = store.new_id()
    a = audit(tid)
    a.update(job_id=jid, parse_complete=True)
    if fault == 'bad-audit':
        a['env_items'] = [['TOKEN', 'secret-not-to-store']]
    else:
        assert conversation.validate_audit(a) == a
    path.write_text(yaml.safe_dump(doc))
    before = path.read_bytes()
    real_correlated = conversation._correlated
    reached = []
    def correlated(doc, turn_id):
        reached.append(turn_id)
        return real_correlated(doc, turn_id)
    monkeypatch.setattr(conversation, '_correlated', correlated)
    expected = ('invalid turn audit fields' if fault == 'bad-audit' else
                'unfinalized pending attempt' if fault == 'mismatched-pending' else
                'unknown or ambiguous human turn id')
    with pytest.raises(conversation.ConversationError, match=expected):
        conversation.finalize_turn(ws, tid, job_id=jid, session_id='session', reply='reply',
                                   state='done', audit=a, recovery_required=False)
    assert reached == ([] if fault == 'bad-audit' else [tid])
    assert path.read_bytes() == before


def test_t4_old_job_json_defaults_and_audit_copy(project):
    ws = project.run_dir('r1')
    p = ws / 'old.json'
    p.write_text(json.dumps(dict(id='old', kind='turn', argv=[], cwd=str(ws))))
    old = jobs.load(p)
    assert getattr(old, 'turn_audit', 'missing') is None
    a = audit(store.new_id())
    j = jobs.Job(id='new', kind='turn', argv=[], cwd=str(ws), turn_audit=a)
    a['selected_manager'] = 'changed'
    assert j.turn_audit['selected_manager'] == 'fixture'


def test_t4_supported_ordinary_never_reprepares(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    def forbidden(*a, **kw):
        raise AssertionError('legacy file-based preparation reached')
    monkeypatch.setattr(agent_run, 'prepare', forbidden)
    monkeypatch.setattr(service, 'dispatch_agent', forbidden)
    result = service.say(project, 'r1', '/scieflow-menu')
    assert result['job']['state'] == 'done' and result['job']['exit_code'] == 0
    assert result['turn']['text'] == 'K protocol reply'


@pytest.mark.parametrize('marked', [False, True])
@pytest.mark.parametrize('force_stdin', [False, True])
def test_t4_real_first_resume_snapshot(k_conversational_project, offline_k_support, monkeypatch,
                                      marked, force_stdin):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.run import charter
    from scieflow.core.turn_input import accept_human_submission
    project, ws, _ = k_conversational_project
    charter.set_text(ws, 'T4 charter sentinel')
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    resolved, prepared, validations, launches, spends = [], [], [], [], []
    original_resolve, original_prepare = agent_run.resolve_turn, agent_run.prepare_turn
    original_validate = turn_admission.Reservation.validate_protection
    original_start, original_spend = jobs.start, service.actions.record_spend
    original_environment = sandbox.environment
    semantic_calls = {'registry': 0, 'policy': 0, 'charter': 0}
    for obj, name, key in [(agent_run.config, 'load_agents', 'registry'),
                            (sandbox, '_capture_turn_policy', 'policy'), (charter, 'snapshot', 'charter')]:
        original = getattr(obj, name)
        def count(*a, _original=original, _key=key, **kw):
            semantic_calls[_key] += 1
            return _original(*a, **kw)
        monkeypatch.setattr(obj, name, count)
    def environment(path):
        return {**original_environment(path), 'K_TASK4_SENTINEL': 'captured'}
    monkeypatch.setattr(sandbox, 'environment', environment)
    def resolve(*a, **kw):
        value = original_resolve(*a, **kw)
        resolved.append(value)
        if marked:
            offline_k_support(value.harness_identity)
        return value
    def prepare(*a, **kw):
        value = original_prepare(*a, **kw)
        prepared.append(value)
        return value
    def validate(self, value):
        validations.append(value)
        return original_validate(self, value)
    def start(*a, **kw):
        value = prepared[-1]
        assert a[1] == list(value.argv)
        assert kw['stdin_text'] == value.stdin_text
        assert kw['env']['K_TASK4_SENTINEL'] == 'captured'
        assert kw['sandbox_writable'] == list(value.protection.writable)
        assert kw['sandbox_mask'] == list(value.protection.masks)
        assert kw['cwd'] == value.cwd and kw['run_dir'] == ws
        launches.append(kw)
        return original_start(*a, **kw)
    def spend(*a, **kw):
        spends.append(kw)
        return original_spend(*a, **kw)
    def completion(value, job, *, artifacts):
        assert value is prepared[-1] and job.exit_code == 0 and job.state == 'done'
        records = [json.loads(line) for line in Path(job.log).read_text().splitlines()]
        assert records[0]['session_id'] == 'k-session-1'
        assert records[-1]['result'] == 'K protocol reply'
        assert records[-1]['input_sha256'] == hashlib.sha256(value.composed_bytes).hexdigest()
        return service._TurnCompletion('k-session-1', records[-1]['result'], True, '')
    monkeypatch.setattr(agent_run, 'resolve_turn', resolve)
    monkeypatch.setattr(agent_run, 'prepare_turn', prepare)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', validate)
    monkeypatch.setattr(jobs, 'start', start)
    monkeypatch.setattr(service.actions, 'record_spend', spend)
    if marked:
        monkeypatch.setattr(service, '_complete_marked_turn', completion)
    for i in range(2):
        token = conversation.selection_revision(conversation.read(ws))
        submission = accept_human_submission('/scieflow-menu', 'harness-command' if marked else 'chat', token)
        result = service.say(project, 'r1', submission.message, human_submission=submission)
        assert result['job']['state'] == 'done' and result['job']['exit_code'] == 0
        assert result['job']['sandboxed'] is True
        assert result['turn']['text'] == 'K protocol reply'
        value = prepared[-1]
        assert value.protection is resolved[-1].protection
        assert len(validations) == 4 * (i + 1)
        assert all(p is value.protection for p in validations[-4:])
        expected = '/scieflow-menu\n\n---\n\n' + agent_run.CHARTER_HEADER + '\n\nT4 charter sentinel' if marked else agent_run.CHARTER_HEADER + '\n\nT4 charter sentinel\n\n---\n\n/scieflow-menu'
        assert value.composed_bytes == expected.encode()
        assert (value.stdin_text is not None) == force_stdin
        a = result['job']['turn_audit']
        assert a['accepted_revision'] == token and a['message_sha256'] == hashlib.sha256(submission.message.encode()).hexdigest()
        tid = a['turn_id']
        assert (ws / 'logs' / f'turn-{tid}.md').read_bytes() == submission.message.encode()
        assert (ws / a['composed_artifact']).read_bytes() == expected.encode()
        records = [json.loads(line) for line in Path(result['job']['log']).read_text().splitlines()]
        assert records[-1]['input_sha256'] == hashlib.sha256(expected.encode()).hexdigest()
        assert conversation.read(ws)['session'] == 'k-session-1'
        if i:
            assert value.session == 'k-session-1' and '--resume' in value.argv
            assert value.argv[value.argv.index('--resume') + 1] == 'k-session-1'
    assert len(resolved) == len(prepared) == len(launches) == len(spends) == 2
    assert semantic_calls == {'registry': 2, 'policy': 2, 'charter': 2}


def test_t4_prompt_artifact_swap_has_no_launch_authority(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    original = service._launch_prepared_turn
    observed = []
    def swapped(project, prepared, *, artifacts, turn_audit):
        artifacts.raw.write_text('/hostile-replacement')
        artifacts.composed.write_text('hostile-composed')
        observed.append(prepared)
        return original(project, prepared, artifacts=artifacts, turn_audit=turn_audit)
    monkeypatch.setattr(service, '_launch_prepared_turn', swapped)
    result = service.say(project, 'r1', 'exact accepted input')
    record = json.loads(Path(result['job']['log']).read_text().splitlines()[-1])
    assert record['input_sha256'] == hashlib.sha256(observed[0].composed_bytes).hexdigest()
    assert b'hostile' not in observed[0].composed_bytes


def _marked_fixture(project, ws, monkeypatch, offline_k_support, *, completion=True):
    from scieflow.core.turn_input import accept_human_submission
    real_resolve = agent_run.resolve_turn
    def resolve(*a, **kw):
        value = real_resolve(*a, **kw)
        offline_k_support(value.harness_identity)
        return value
    monkeypatch.setattr(agent_run, 'resolve_turn', resolve)
    if completion:
        def finish(prepared, job, *, artifacts):
            records = [json.loads(line) for line in Path(job.log).read_text().splitlines()]
            assert job.exit_code == 0 and records[-1]['input_sha256'] == hashlib.sha256(prepared.composed_bytes).hexdigest()
            return service._TurnCompletion(records[0]['session_id'], records[-1]['result'], True, '')
        monkeypatch.setattr(service, '_complete_marked_turn', finish)
    return accept_human_submission('/scieflow-menu', 'harness-command',
                                   conversation.selection_revision(conversation.read(ws)))


@pytest.mark.parametrize('boundary', ['artifact', 'start-unknown', 'wait-exit', 'spend',
    'linkage', 'completion', 'final-record', 'response-loss'])
def test_t4_attempt_errors_preserve_state_spend_and_never_replay(
        k_conversational_project, offline_k_support, monkeypatch, boundary):
    from scieflow.core import turn_admission
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    submission = _marked_fixture(project, ws, monkeypatch, offline_k_support)
    starts, spends = [], []
    real_start, real_wait, real_spend = jobs.start, jobs.wait, service.actions.record_spend
    def start(*a, **kw):
        pair = real_start(*a, **kw)
        starts.append(pair[0])
        if boundary == 'start-unknown':
            real_wait(*pair)  # Actual child exited; service never gets its returned id.
            raise OSError('injected unknown start result')
        return pair
    def wait(*a):
        result = real_wait(*a)
        if boundary == 'wait-exit':
            raise OSError('injected after actual wait exit')
        return result
    def spend(*a, **kw):
        spends.append(kw)
        result = real_spend(*a, **kw)
        if boundary == 'spend':
            raise OSError('injected after spend')
        return result
    def fail(*a, **kw):
        raise OSError('injected finite boundary failure')
    monkeypatch.setattr(jobs, 'start', start)
    monkeypatch.setattr(jobs, 'wait', wait)
    monkeypatch.setattr(service.actions, 'record_spend', spend)
    if boundary == 'artifact':
        monkeypatch.setattr(service, '_create_turn_artifacts', fail)
    elif boundary == 'linkage':
        monkeypatch.setattr(conversation, 'annotate_turn', fail)
    elif boundary == 'completion':
        monkeypatch.setattr(service, '_complete_marked_turn', fail)
    elif boundary == 'final-record':
        monkeypatch.setattr(conversation, 'finalize_turn', fail)
    elif boundary == 'response-loss':
        monkeypatch.setattr(service, 'asdict', fail)
    with pytest.raises(service.ServiceError, match='uncertain'):
        service.say(project, 'r1', submission.message, human_submission=submission)
    assert len(starts) == (0 if boundary == 'artifact' else 1)
    assert len(spends) == (0 if boundary in ('artifact', 'start-unknown') else 1)
    doc = conversation.read(ws)
    assert doc['turns'][0]['text'] == submission.message
    assert len(doc['turns']) == (1 if boundary == 'final-record' else 2)
    if boundary == 'response-loss':
        assert doc['session'] == 'k-session-1' and not doc['recovery_required'] and doc['pending_turn_id'] is None
    else:
        assert doc['recovery_required'] or doc['pending_turn_id']
        assert doc['session'] is None
    if starts:
        actual = jobs.load(Path(starts[0].log).with_suffix('.json'))
        assert actual.state == 'done' and actual.exit_code == 0
        if boundary not in ('start-unknown', 'final-record'):
            assert actual.turn_audit == doc['turns'][0]['audit']
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError):
        service.say(project, 'r1', submission.message, human_submission=submission)
    assert (ws / 'conversation.yml').read_bytes() == before
    assert len(starts) == (0 if boundary == 'artifact' else 1)
    with turn_admission.reserve(project, ws):
        pass


@pytest.mark.parametrize('result', ['missing', 'contradictory', 'incomplete', 'default', 'failed', 'timeout', 'cancelled'])
def test_t4_marked_unusable_completion_requires_deliberate_recovery(
        k_conversational_project, offline_k_support, monkeypatch, result):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    conversation.record_session(ws, 'k-session-1')
    submission = _marked_fixture(project, ws, monkeypatch, offline_k_support, completion=False)
    malformed_reached = []
    if result == 'default':
        real_wait = jobs.wait
        def malformed_wait(job, proc):
            actual = real_wait(job, proc)
            assert actual.state == 'done' and actual.exit_code == 0
            Path(actual.log).write_bytes(b'{"type":"result",')
            malformed_reached.append(actual.id)
            return actual
        monkeypatch.setattr(jobs, 'wait', malformed_wait)
    if result == 'failed':
        p = project.root / 'config/agents.yml'
        data = yaml.safe_load(p.read_text())
        data['agents']['k-protocol-fixture']['resume_cmd'] = '/bin/false {session} {prompt}'
        p.write_text(yaml.safe_dump(data))
    elif result == 'timeout':
        p = project.root / 'config/agents.yml'
        data = yaml.safe_load(p.read_text())
        data['agents']['k-protocol-fixture'].update(timeout_min=0.001,
            resume_cmd="/bin/sh -c 'sleep 5' fixture {session} {prompt}")
        p.write_text(yaml.safe_dump(data))
    elif result == 'cancelled':
        real_wait = jobs.wait
        def wait(job, proc):
            jobs.cancel(job)
            return real_wait(job, proc)
        monkeypatch.setattr(jobs, 'wait', wait)
    if result != 'default':
        monkeypatch.setattr(service, '_complete_marked_turn', lambda *a, **kw:
            service._TurnCompletion(None if result == 'missing' else 'different' if result == 'contradictory' else
                                    'k-session-1', 'finite partial', result != 'incomplete', ''))
    response = service.say(project, 'r1', submission.message, human_submission=submission)
    if result == 'default':
        assert malformed_reached == [response['job']['id']]
        assert Path(response['job']['log']).read_bytes() == b'{"type":"result",'
        tid = response['job']['turn_audit']['turn_id']
        assert (ws / 'logs' / f'turn-{tid}.out.md').read_bytes() == b'{"type":"result",'
    if result in ('failed', 'timeout', 'cancelled'):
        assert response['job']['state'] == result
    else:
        assert response['job']['state'] == 'done' and response['job']['exit_code'] == 0
    doc = conversation.read(ws)
    assert doc['recovery_required'] and doc['session'] is None and doc['pending_turn_id'] is None
    assert doc['turns'][0]['finalized'] and response['turn']['audit']['reason']
    with pytest.raises(service.ServiceError):
        service.say(project, 'r1', 'ordinary must not reuse uncertainty')
    with pytest.raises(service.ServiceError):
        service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', reset_session=True)
    rev = conversation.selection_revision(doc)
    preserved = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision=rev)
    assert preserved['recovery_required']
    reset = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture',
        expected_revision=conversation.selection_revision(preserved), reset_session=True)
    assert not reset['recovery_required'] and reset['pending_turn_id'] is None and reset['session'] is None
    assert len(reset['turns']) == 2


@pytest.mark.parametrize('broken', ['removed', 'disabled', 'family', 'session_cmd', 'resume_cmd', 'binary'])
def test_t4_r5_broken_current_is_not_a_selection_prerequisite(k_conversational_project, monkeypatch, broken):
    from scieflow.core import harness_commands, sandbox
    from scieflow.core.run import charter
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'broken')
    conversation.record_session(ws, 'old-session')
    conversation.add_turn(ws, role='human', text='retained history')
    path = project.root / 'config/agents.yml'
    data = yaml.safe_load(path.read_text())
    cfg = dict(data['agents']['k-protocol-fixture'])
    if broken != 'removed':
        if broken == 'disabled': cfg['enabled'] = False
        elif broken == 'family': cfg['family'] = 'invalid'
        elif broken == 'binary': cfg['session_cmd'] = '/absent/current {prompt}'
        else: cfg.pop(broken)
        data['agents']['broken'] = cfg
    path.write_text(yaml.safe_dump(data))
    def forbidden(*a, **kw):
        pytest.fail('target-only selection touched launch/old current/charter/capability')
    for obj, name in [(agent_run, 'resolve_turn'), (agent_run, 'prepare_turn'), (agent_run, '_capture_execution'),
                      (charter, 'snapshot'), (harness_commands, 'support_for'), (sandbox, 'verify'),
                      (service.actions, 'record_spend'), (jobs, 'start')]:
        monkeypatch.setattr(obj, name, forbidden)
    result = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision='3')
    assert result['selection_generation'] == 4 and result['session'] is None
    assert result['turns'][0]['text'] == 'retained history'
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


def test_t4_r5_same_manager_charter_contrast_and_missing_binary(k_conversational_project):
    project, ws, _ = k_conversational_project
    selected = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    conversation.record_session(ws, 'keep')
    (ws / 'charter.yml').write_text('malformed: [\n')
    same = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision='2')
    assert same['session'] == 'keep' and same['selection_generation'] == 3
    reset = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision='3', reset_session=True)
    assert reset['session'] is None and reset['selection_generation'] == 4
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError, match='charter'):
        service.say(project, 'r1', 'must fully validate')
    assert (ws / 'conversation.yml').read_bytes() == before
    (ws / 'charter.yml').unlink()
    p = project.root / 'config/agents.yml'
    data = yaml.safe_load(p.read_text())
    for key in ('session_cmd', 'resume_cmd'):
        data['agents']['k-protocol-fixture'][key] = '/absent/executable {session} {prompt}' if key == 'resume_cmd' else '/absent/executable {prompt}'
    p.write_text(yaml.safe_dump(data))
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision='4')
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError, match='executable'):
        service.say(project, 'r1', 'selection did not certify existence')
    assert (ws / 'conversation.yml').read_bytes() == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


@pytest.mark.parametrize('fault', ['stale', 'malformed-token', 'bool-generation', 'bad-registry', 'disabled',
                                  'missing-session', 'missing-resume', 'bad-family', 'survivor'])
def test_t4_r5_zero_mutation_reset_negatives(k_conversational_project, monkeypatch, fault):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    token = '0' if fault == 'stale' else '01' if fault == 'malformed-token' else '1'
    if fault == 'bool-generation':
        p = ws / 'conversation.yml'
        data = yaml.safe_load(p.read_text())
        data['selection_generation'] = True
        p.write_text(yaml.safe_dump(data))
    elif fault == 'survivor':
        monkeypatch.setattr(service, '_turn_in_flight', lambda *a: True)
    elif fault not in ('stale', 'malformed-token'):
        p = project.root / 'config/agents.yml'
        data = yaml.safe_load(p.read_text())
        cfg = data['agents']['k-protocol-fixture']
        if fault == 'bad-registry': data['agents'] = []
        elif fault == 'disabled': cfg['enabled'] = False
        elif fault == 'missing-session': cfg.pop('session_cmd')
        elif fault == 'missing-resume': cfg.pop('resume_cmd')
        elif fault == 'bad-family': cfg['family'] = 'invalid'
        p.write_text(yaml.safe_dump(data))
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError):
        service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision=token, reset_session=True)
    assert (ws / 'conversation.yml').read_bytes() == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


def test_t4_direct_generation_drift_cannot_send_a_stale_snapshot(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    original = agent_run.prepare_turn
    def drift(*a, **kw):
        value = original(*a, **kw)
        conversation.set_agent(ws, 'stub2')
        return value
    monkeypatch.setattr(agent_run, 'prepare_turn', drift)
    with pytest.raises(service.ServiceError, match='stale'):
        service.say(project, 'r1', 'do not launch with a changed selection')
    assert conversation.read(ws)['agent'] == 'stub2' and conversation.read(ws)['turns'] == []
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


@pytest.mark.parametrize('mutation', ['replace', 'metadata', 'symlink', 'parent', 'churn', 'missing-facts'])
def test_t4_executable_snapshot_metadata_barriers(k_conversational_project, monkeypatch, mutation):
    import os
    import shlex
    import sys
    from dataclasses import replace
    project, ws, _ = k_conversational_project
    directory = project.root / 'execution'
    directory.mkdir()
    target = directory / 'runner'
    target.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    target.chmod(0o700)
    invoke = directory / 'alias'
    invoke.symlink_to('runner')
    p = project.root / 'config/agents.yml'
    data = yaml.safe_load(p.read_text())
    for key in ('session_cmd', 'resume_cmd'):
        tokens = shlex.split(data['agents']['k-protocol-fixture'][key])
        tokens[0] = str(invoke)
        data['agents']['k-protocol-fixture'][key] = shlex.join(tokens)
    p.write_text(yaml.safe_dump(data))
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    original = agent_run.prepare_turn
    prepared = []
    def alter(*a, **kw):
        value = original(*a, **kw)
        prepared.append(value)
        if mutation == 'replace':
            target.rename(directory / 'original')
            target.write_text('#!/bin/sh\nexit 29\n')
            target.chmod(0o700)
        elif mutation == 'metadata':
            info = target.stat()
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
        elif mutation == 'symlink':
            invoke.unlink()
            invoke.symlink_to('/bin/false')
        elif mutation == 'parent':
            directory.rename(project.root / 'original-directory')
            directory.mkdir()
        elif mutation == 'churn':
            (directory / 'irrelevant-child').write_text('ordinary directory churn')
        else:
            value = replace(value, execution_facts=(value.execution_facts[0],))
        return value
    monkeypatch.setattr(agent_run, 'prepare_turn', alter)
    before = (ws / 'conversation.yml').read_bytes()
    if mutation == 'churn':
        result = service.say(project, 'r1', 'metadata guard keeps directory churn compatible')
        assert result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
        assert result['job']['argv'][0] == str(invoke)
    else:
        with pytest.raises(service.ServiceError):
            service.say(project, 'r1', 'replacement must be preattempt refusal')
        assert (ws / 'conversation.yml').read_bytes() == before
        assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()
    assert len(prepared) == 1


@pytest.mark.parametrize('parent_alias', [False, True])
def test_t4_real_venv_and_parent_alias_preserve_invocation_semantics(k_conversational_project, parent_alias):
    import shlex
    import subprocess
    import venv
    from scieflow.core import sandbox
    project, ws, _ = k_conversational_project
    env = project.root / 'fixture-venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(env)
    alias = project.root / 'venv-bin-alias'
    alias.symlink_to(env / 'bin', target_is_directory=True)
    invoke = alias / 'python' if parent_alias else env / 'bin/python'
    probe = ['-B', '-c', 'import json,sys;print(json.dumps({"prefix":sys.prefix,"exe":sys.executable}))']
    native = subprocess.run(sandbox.wrap([str(invoke), *probe], writable=[ws], cwd=project.root,
                                            layout=sandbox.dispatch_layout(project, run_dir=ws, agent='k-protocol-fixture')),
                            env=sandbox.environment(ws), capture_output=True, text=True, check=True)
    expected = json.loads(native.stdout)
    if not parent_alias:
        assert expected['prefix'] == str(env)
        canonical = subprocess.run(sandbox.wrap([str(invoke.resolve()), *probe], writable=[ws], cwd=project.root,
                                   layout=sandbox.dispatch_layout(project, run_dir=ws, agent='k-protocol-fixture')),
                                   env=sandbox.environment(ws), capture_output=True, text=True, check=True)
        assert json.loads(canonical.stdout)['prefix'] != expected['prefix']
    runner = ws / 'scratch' / 'venv_runner.py'
    runner.parent.mkdir(exist_ok=True)   # the agent home already created scratch/
    runner.write_text('import json, runpy, sys\nprint(json.dumps({"prefix":sys.prefix,"exe":sys.executable}))\n'
                      'sys.argv=sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name="__main__")\n')
    protocol = Path(__file__).resolve().parents[1] / 'fixtures/k_conversation_protocol.py'
    p = project.root / 'config/agents.yml'
    data = yaml.safe_load(p.read_text())
    prefix = shlex.join([str(invoke), '-B', str(runner), str(protocol)])
    data['agents']['k-protocol-fixture'].update(session_cmd=prefix + ' first {prompt}',
        resume_cmd=prefix + ' --resume {session} resume {prompt}')
    p.write_text(yaml.safe_dump(data))
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    for i in range(2):
        response = service.say(project, 'r1', 'actual venv invocation')
        assert response['job']['argv'][0] == str(invoke)
        records = [json.loads(line) for line in Path(response['job']['log']).read_text().splitlines()]
        assert records[0] == expected
        assert response['job']['exit_code'] == 0 and response['turn']['text'] == 'K protocol reply'
        assert conversation.read(ws)['session'] == 'k-session-1'
        if i: assert '--resume' in response['job']['argv']


@pytest.mark.parametrize('changed', ['registry', 'run-config', 'charter', 'policy', 'environment'])
def test_t4_snapshot_semantic_mutations_never_recapture(k_conversational_project, monkeypatch, changed):
    from scieflow.core.run import charter
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    charter.set_text(ws, 'snapshot charter')
    original = agent_run.prepare_turn
    snapshot, launches = [], []
    real_start = jobs.start
    def prepare(*a, **kw):
        value = original(*a, **kw)
        snapshot.append(value)
        if changed == 'registry': (project.root / 'config/agents.yml').write_text('malformed: [\n')
        elif changed == 'run-config': (ws / 'config.yml').write_text('agent_overrides: malformed\n')
        elif changed == 'charter': (ws / 'charter.yml').write_text('malformed: [\n')
        elif changed == 'policy': (project.root / 'config/sandbox.yml').write_text('writable: []\n')
        else: monkeypatch.setenv('K_TASK4_SENTINEL', 'later-environment')
        return value
    def start(*a, **kw):
        launches.append(kw)
        assert a[1] == list(snapshot[0].argv) and kw['env'] == dict(snapshot[0].env_items)
        assert kw['sandbox_mask'] == list(snapshot[0].protection.masks)
        return real_start(*a, **kw)
    monkeypatch.setattr(agent_run, 'prepare_turn', prepare)
    monkeypatch.setattr(jobs, 'start', start)
    if changed == 'policy':
        with pytest.raises(service.ServiceError): service.say(project, 'r1', 'immutable accepted')
        assert not launches and conversation.read(ws)['turns'] == []
    else:
        response = service.say(project, 'r1', 'immutable accepted')
        assert response['job']['exit_code'] == 0 and response['turn']['text'] == 'K protocol reply'
        records = [json.loads(line) for line in Path(response['job']['log']).read_text().splitlines()]
        assert records[-1]['input_sha256'] == hashlib.sha256(snapshot[0].composed_bytes).hexdigest()
        assert len(launches) == 1
    assert len(snapshot) == 1


@pytest.mark.parametrize('fault', ['mismatch', 'actor', 'fake-object', 'stale', 'malformed-charter',
    'empty-certificate', 'unsupported', 'sandbox', 'budget'])
def test_t4_full_preflight_has_no_attempt_artifacts(k_conversational_project, monkeypatch, fault):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.turn_input import accept_human_submission
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    marked = fault in ('actor', 'empty-certificate', 'unsupported')
    token = '0' if fault == 'stale' else '1'
    submission = accept_human_submission('/scieflow-menu', 'harness-command' if marked else 'chat', token)
    text = 'mismatch' if fault == 'mismatch' else submission.message
    actor = 'agent' if fault == 'actor' else 'human'
    if fault == 'fake-object': submission = {'message': text, 'mode': 'harness-command'}
    elif fault == 'malformed-charter': (ws / 'charter.yml').write_text('bad: [\n')
    elif fault == 'unsupported': monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    elif fault == 'sandbox':
        def denied(*a): raise sandbox.SandboxError('finite sandbox refusal')
        monkeypatch.setattr(sandbox, 'verify', denied)
    elif fault == 'budget':
        def denied(*a): raise service.actions.BudgetExhausted(['wall_minutes'])
        monkeypatch.setattr(service.actions, 'guard_budget', denied)
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError):
        service.say(project, 'r1', text, actor=actor, human_submission=submission)
    assert (ws / 'conversation.yml').read_bytes() == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()
    assert not any(e['type'] == 'turn.sent' for e in events.read(ws))


@pytest.mark.parametrize('substitute', ['leaf', 'parent'])
def test_t4_ordinary_transcript_substitution_never_overwrites_external_file(
        k_conversational_project, monkeypatch, substitute):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    outside = project.root / 'outside'
    outside.mkdir()
    real_launch = service._launch_prepared_turn
    outputs, spends = [], []
    real_spend = service.actions.record_spend
    def spend(*a, **kw):
        spends.append(True)
        return real_spend(*a, **kw)
    def launch(project, prepared, *, artifacts, turn_audit):
        job = real_launch(project, prepared, artifacts=artifacts, turn_audit=turn_audit)
        external = outside / artifacts.transcript.name
        external.write_text('external sentinel')
        if substitute == 'leaf':
            artifacts.transcript.unlink()
            artifacts.transcript.symlink_to(external)
        else:
            artifacts.transcript.parent.rename(ws / 'original-logs')
            artifacts.transcript.parent.symlink_to(outside, target_is_directory=True)
        outputs.append(external)
        return job
    monkeypatch.setattr(service, '_launch_prepared_turn', launch)
    monkeypatch.setattr(service.actions, 'record_spend', spend)
    with pytest.raises(service.ServiceError): service.say(project, 'r1', 'ordinary copy boundary')
    assert outputs[0].read_text() == 'external sentinel'
    assert len(spends) == 1 and conversation.read(ws)['session'] is None


def test_t4_start_run_real_inert_opener_separate_admissions(k_conversational_project, monkeypatch):
    from contextlib import contextmanager
    from scieflow.core import turn_admission
    project, _, _ = k_conversational_project
    (project.root / 'config/defaults.yml').write_text('approval: per-campaign\nmax_iterations: 3\n'
        'max_experiment_runs: 10\nmax_wall_minutes: 60\n')
    original_reserve, original_load = turn_admission.reserve, service.config.load_agents
    acquisitions, loads = [], []
    @contextmanager
    def reserve(project, ws):
        with original_reserve(project, ws) as reservation:
            acquisitions.append(reservation)
            yield reservation
    def load(*a):
        loads.append(True)
        return original_load(*a)
    monkeypatch.setattr(turn_admission, 'reserve', reserve)
    monkeypatch.setattr(service.config, 'load_agents', load)
    response = service.start_run(project, 'workspace/new', 'new inert goal', 'k-protocol-fixture')
    ws = project.run_dir('new')
    assert response['slug'] == 'new' and response['turn']['text'] == 'K protocol reply'
    doc = conversation.read(ws)
    assert doc['session'] == 'k-session-1' and doc['selection_generation'] == 4
    actual, = jobs.list_jobs(project, ws)
    assert actual.state == 'done' and actual.exit_code == 0
    assert len(acquisitions) == 2 and acquisitions[0] is not acquisitions[1]
    assert len(loads) == 3  # precreate, target-only selection, then full send
    assert 'new inert goal' in doc['turns'][0]['text']


def test_t4_real_lost_job_and_pending_attempt_never_authorize_replay(k_conversational_project):
    from scieflow.core import sandbox
    from scieflow.core.turn_input import accept_human_submission
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    captured = agent_run.resolve_turn(project, 'k-protocol-fixture', run_dir=ws, session=None)
    prepared = agent_run.prepare_turn(captured, accept_human_submission('actual lost fixture'))
    tid = store.new_id()
    diagnostic = audit(tid, revision='1')
    diagnostic['selected_manager'] = 'k-protocol-fixture'
    conversation.add_turn(ws, role='human', text='/scieflow-menu', turn_id=tid, audit=diagnostic)
    job, proc = jobs.start(project, list(prepared.argv), kind='turn', cwd=prepared.cwd, run_dir=ws,
        env=dict(prepared.env_items), sandbox_writable=list(prepared.protection.writable),
        sandbox_layout=prepared.layout, turn_audit=diagnostic)
    assert proc.wait(timeout=15) == 0  # Simulate coordinator lost before jobs.wait/finalize.
    lost, = jobs.reconcile(project, ws)
    assert lost.id == job.id and lost.state == 'lost'
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError): service.say(project, 'r1', 'do not automatically reuse')
    assert (ws / 'conversation.yml').read_bytes() == before and len(jobs.list_jobs(project, ws)) == 1
    with pytest.raises(service.ServiceError): service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', reset_session=True)
    reset = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture', expected_revision='2', reset_session=True)
    assert reset['pending_turn_id'] is None and len(reset['turns']) == 1
    assert jobs.load(Path(job.log).with_suffix('.json')).state == 'lost'


def test_t4_job_captured_masks_and_audit_are_defensively_copied(k_conversational_project, monkeypatch):
    from scieflow.core import sandbox
    project, ws, _ = k_conversational_project
    tid = store.new_id()
    original_audit = audit(tid, kind='chat')
    masks = []
    real_wrap = sandbox.wrap
    def wrap(*a, **kw):
        assert kw['mask'] == [] and kw['mask'] is not masks
        masks.append(ws / 'not-selected')
        original_audit['reason'] = 'mutated input mapping'
        return real_wrap(*a, **kw)
    def forbidden(*a, **kw): pytest.fail('explicit captured masks recomputed')
    monkeypatch.setattr(sandbox, 'wrap', wrap)
    monkeypatch.setattr(sandbox, 'masks_for', forbidden)
    job, proc = jobs.start(project, ['/bin/true'], kind='turn', cwd=ws, run_dir=ws,
        sandbox_writable=[ws], env=dict(__import__('os').environ), sandbox_mask=masks, turn_audit=original_audit)
    actual = jobs.wait(job, proc)
    assert actual.state == 'done' and actual.exit_code == 0 and actual.turn_audit['reason'] == ''
    assert actual.turn_audit['job_id'] == actual.id and original_audit['job_id'] == ''


@pytest.mark.parametrize('channel', ['slash', 'actor', 'saved-mode', 'saved-prompt', 'environment'])
def test_t4_only_explicit_new_submission_marks_intent(k_conversational_project, monkeypatch, channel):
    from scieflow.core import harness_commands
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    if channel == 'saved-mode':
        p = ws / 'conversation.yml'
        data = yaml.safe_load(p.read_text())
        data.update(input_kind='harness-command', composition_order='command-first', message_sha256='f' * 64)
        p.write_text(yaml.safe_dump(data))
    elif channel == 'saved-prompt':
        (ws / 'logs/forged.md').write_text('/scieflow-menu')
    elif channel == 'environment':
        monkeypatch.setenv('SCIEFLOW_TURN_MODE', 'harness-command')
    def forbidden(*a, **kw): pytest.fail('ordinary text reconstructed marked execution authority')
    monkeypatch.setattr(harness_commands, 'support_for', forbidden)
    monkeypatch.setattr(service, '_complete_marked_turn', forbidden)
    result = service.say(project, 'r1', '/scieflow-menu', actor='agent' if channel == 'actor' else 'human')
    assert result['job']['state'] == 'done' and result['job']['exit_code'] == 0
    assert result['turn']['text'] == 'K protocol reply'
    assert result['job']['turn_audit']['input_kind'] == 'chat'
    assert result['job']['turn_audit']['composition_order'] == 'charter-first'
    assert not conversation.read(ws)['recovery_required']
    for event in events.read(ws):
        assert '/scieflow-menu' not in json.dumps(event) and 'K protocol reply' not in json.dumps(event)


@pytest.mark.parametrize('field,value', [('schema_version', True), ('message_sha256', 'F' * 64),
    ('charter_version', True), ('parse_complete', 1), ('job_id', 'not-an-id'), ('reason', 'x' * 161),
    ('final_revision', '01'), ('accepted_revision', 0), ('selected_manager', {}), ('composed_artifact', '/outside')])
def test_t4_audit_schema_rejects_noncanonical_or_mutable_values(field, value):
    a = audit(store.new_id())
    a[field] = value
    with pytest.raises(conversation.ConversationError): conversation.validate_audit(a)


def test_t4_final_store_failure_is_atomic_pending_evidence(project, monkeypatch):
    ws = project.run_dir('r1')
    tid = store.new_id()
    a = audit(tid)
    conversation.add_turn(ws, role='human', text='/scieflow-menu', turn_id=tid, audit=a)
    p = ws / 'conversation.yml'
    before = p.read_bytes()
    original = store._atomic_write
    def fail(path, doc):
        if Path(path) == p: raise OSError('injected final persistence failure')
        return original(path, doc)
    monkeypatch.setattr(store, '_atomic_write', fail)
    with pytest.raises(OSError):
        conversation.finalize_turn(ws, tid, job_id='', session_id=None, reply='', state='lost',
                                   audit=a, recovery_required=True)
    assert p.read_bytes() == before
    assert conversation.read(ws)['pending_turn_id'] == tid
    assert not conversation.read(ws)['turns'][0]['finalized']


@pytest.mark.parametrize('fault,expected', [
    ('malformed-yaml', 'malformed conversation.yml'),
    ('malformed-generation', 'malformed selection_generation'),
    ('stale-token', 'stale selection revision'),
])
def test_t4_unsupported_ordinary_validation_service_boundary(project, monkeypatch, fault, expected):
    from scieflow.core import turn_admission
    from scieflow.core.turn_input import accept_human_submission
    ws = project.run_dir('r1')
    path = ws / 'conversation.yml'
    raw = ('agent: [\n' if fault == 'malformed-yaml' else
           'agent: fixture\nturns: []\nselection_generation: true\n' if fault == 'malformed-generation' else
           'agent: fixture\nturns: []\nselection_generation: 1\n')
    path.write_text(raw)
    before = {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()}
    monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    launches = []
    def forbidden(*args, **kwargs):
        launches.append(True)
        pytest.fail('invalid unsupported state reached dispatch or artifact creation')
    for obj, name in [(service, '_say_legacy'), (service, '_say_prepared'),
                      (service, 'dispatch_agent'), (jobs, 'start')]:
        monkeypatch.setattr(obj, name, forbidden)
    submission = accept_human_submission('ordinary', 'chat', '0') if fault == 'stale-token' else None
    caught = None
    try:
        service.say(project, 'r1', 'ordinary', human_submission=submission)
    except Exception as exc:
        caught = exc
    assert path.read_bytes() == raw.encode()
    assert {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()} == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()
    assert launches == []
    print(f'T4_COMPATIBILITY fault={fault} error_type={type(caught).__name__} bytes_unchanged=true no_launch=true')
    assert isinstance(caught, service.ServiceError), f'leaked {type(caught).__name__}, not ServiceError'
    assert expected in str(caught)
    assert isinstance(caught.__cause__, conversation.ConversationError)
    assert str(caught.__cause__) == str(caught)


@pytest.mark.parametrize('error_type', [ValueError, OSError, service.ServiceError])
def test_t4_unsupported_validation_preserves_unexpected_and_service_errors(project, monkeypatch, error_type):
    from scieflow.core import turn_admission
    ws = project.run_dir('r1')
    before = {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()}
    monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    error = error_type('unchanged validation sentinel')
    def fail(*args, **kwargs):
        raise error
    def forbidden(*args, **kwargs):
        pytest.fail('validation failure reached legacy launch')
    monkeypatch.setattr(service, '_send_state', fail)
    monkeypatch.setattr(service, '_say_legacy', forbidden)
    monkeypatch.setattr(jobs, 'start', forbidden)
    with pytest.raises(error_type, match='unchanged validation sentinel') as caught:
        service.say(project, 'r1', 'ordinary')
    assert caught.value is error and caught.value.__cause__ is None
    assert {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()} == before


def test_t4_unsupported_marked_refuses_before_state_validation(project, monkeypatch):
    from scieflow.core import turn_admission
    from scieflow.core.turn_input import accept_human_submission
    ws = project.run_dir('r1')
    path = ws / 'conversation.yml'
    path.write_text('agent: [\n')
    before = {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()}
    monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    def forbidden(*args, **kwargs):
        pytest.fail('unsupported marked turn reached state validation or launch')
    monkeypatch.setattr(service, '_send_state', forbidden)
    monkeypatch.setattr(service, '_say_legacy', forbidden)
    monkeypatch.setattr(jobs, 'start', forbidden)
    submission = accept_human_submission('/scieflow-menu', 'harness-command', '0')
    with pytest.raises(service.ServiceError, match='marked turns require supported protected admission') as caught:
        service.say(project, 'r1', submission.message, human_submission=submission)
    assert caught.value.__cause__ is None
    assert {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()} == before


def test_t4_unsupported_ordinary_real_first_resume_compatibility(k_conversational_project, monkeypatch):
    from scieflow.core import turn_admission
    project, ws, _ = k_conversational_project
    monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    conversation.set_agent(ws, 'k-protocol-fixture')
    real_legacy = service._say_legacy
    calls = []
    def legacy(*args, **kwargs):
        calls.append(True)
        return real_legacy(*args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('unsupported ordinary entered supported preparation/admission')
    monkeypatch.setattr(service, '_say_legacy', legacy)
    monkeypatch.setattr(service, '_say_prepared', forbidden)
    monkeypatch.setattr(turn_admission, 'reserve', forbidden)
    first = service.say(project, 'r1', 'ordinary compatibility')
    second = service.say(project, 'r1', 'ordinary resume')
    for result in [first, second]:
        assert result['job']['state'] == 'done' and result['job']['exit_code'] == 0
        assert result['turn']['text'] == 'K protocol reply'
    assert len(calls) == 2 and len(jobs.list_jobs(project, ws)) == 2
    assert conversation.read(ws)['session'] == 'k-session-1'
    assert len(conversation.read(ws)['turns']) == 4
    assert not (project.root / '.scieflow').exists()
    print('T4_COMPATIBILITY real_unsupported_first_resume=true jobs_done=2 session=k-session-1')


@pytest.mark.parametrize('fault', ['busy', 'unsafe'])
def test_t4_supported_refusal_never_uses_compatibility(project, monkeypatch, fault, grant_credentials):
    from contextlib import nullcontext
    from scieflow.core import turn_admission
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'stub')
    before = {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()}
    assert turn_admission.backend_supported()
    if fault == 'unsafe':
        (project.root / 'config/sandbox.yml').write_text('writable:\n  - path: .scieflow\n')
        grant_credentials(project.root, 'stub')     # S4 (A9): the refusal under test is the grant, not the login
    def forbidden(*args, **kwargs):
        pytest.fail('supported admission refusal reached compatibility or launch')
    monkeypatch.setattr(service, '_say_legacy', forbidden)
    monkeypatch.setattr(service, '_launch_prepared_turn', forbidden)
    monkeypatch.setattr(jobs, 'start', forbidden)
    held = turn_admission.reserve(project, ws) if fault == 'busy' else nullcontext()
    with held:
        with pytest.raises(service.ServiceError, match='admission busy|writable grant'):
            service.say(project, 'r1', 'refused')
    assert {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('fault', ['unknown', 'ambiguous'])
def test_t4_annotation_valid_input_reaches_stored_refusal(project, monkeypatch, fault):
    ws = project.run_dir('r1')
    tid, jid = store.new_id(), store.new_id()
    conversation.add_turn(ws, role='human', text='inert', turn_id=tid, audit=audit(tid))
    path = ws / 'conversation.yml'
    doc = yaml.safe_load(path.read_text())
    if fault == 'unknown':
        tid = store.new_id()
    else:
        doc['turns'].append(dict(doc['turns'][0]))
    path.write_text(yaml.safe_dump(doc))
    before = path.read_bytes()
    valid = audit(tid)
    valid.update(job_id=jid, parse_complete=True)
    assert conversation.validate_audit(valid) == valid
    real_correlated = conversation._correlated
    reached = []
    def correlated(doc, turn_id):
        reached.append(turn_id)
        return real_correlated(doc, turn_id)
    monkeypatch.setattr(conversation, '_correlated', correlated)
    with pytest.raises(conversation.ConversationError, match='unknown or ambiguous human turn id'):
        conversation.annotate_turn(ws, tid, job_id=jid, audit=valid)
    assert reached == [tid]
    assert path.read_bytes() == before


def test_t4_valid_annotation_then_one_atomic_completion(project, monkeypatch):
    ws = project.run_dir('r1')
    tid, jid = store.new_id(), store.new_id()
    conversation.add_turn(ws, role='human', text='inert', turn_id=tid, audit=audit(tid))
    complete = audit(tid)
    complete.update(job_id=jid, parse_complete=True)
    assert conversation.validate_audit(complete) == complete
    conversation.annotate_turn(ws, tid, job_id=jid, audit=complete)
    assert conversation.read(ws)['selection_generation'] == 1
    real_update = store.update_yaml
    writes = []
    def update(path, fn):
        writes.append(path)
        return real_update(path, fn)
    monkeypatch.setattr(store, 'update_yaml', update)
    result = conversation.finalize_turn(ws, tid, job_id=jid, session_id='fixture-session',
        reply='inert reply', state='done', audit=complete, recovery_required=False)
    current = conversation.read(ws)
    assert writes == [ws / 'conversation.yml']
    assert current['selection_generation'] == 2 and current['session'] == 'fixture-session'
    assert current['pending_turn_id'] is None and current['recovery_required'] is False
    assert len(current['turns']) == 2 and current['turns'][0]['finalized'] is True
    assert current['turns'][0]['text'] == 'inert'
    assert current['turns'][0]['job_id'] == jid
    assert [t for t in current['turns'] if t['role'] == 'agent'] == [result]
    assert result['audit']['final_revision'] == '2'
