"""Real inert Task5 consumer composition; never harness certification."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

from scieflow.core import agent_run, jobs, service, sessions, turn_admission, turn_artifacts, turn_output
from scieflow.core.run import charter, conversation
from scieflow.core.turn_input import accept_human_submission


def bind(project, ws, monkeypatch, offline_k_support):
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    real = agent_run.resolve_turn
    resolved = []
    def resolve(*a, **kw):
        value = real(*a, **kw)
        offline_k_support(value.harness_identity)
        resolved.append(value)
        return value
    monkeypatch.setattr(agent_run, 'resolve_turn', resolve)
    return resolved


def marked(project, ws):
    submission = accept_human_submission('/scieflow-menu', 'harness-command',
        conversation.selection_revision(conversation.read(ws)))
    return service.say(project, 'r1', submission.message, human_submission=submission)


def raw_result(ws, response):
    audit = response['job']['turn_audit']
    return Path(response['job']['log']), ws / 'logs' / f"turn-{audit['turn_id']}.out.md"


def assert_recovery(project, ws, response):
    doc = conversation.read(ws)
    assert doc['recovery_required'] and doc['session'] is None and doc['pending_turn_id'] is None
    assert doc['turns'][0]['finalized'] and response['turn']['audit']['reason']
    before = (ws / 'conversation.yml').read_bytes()
    with pytest.raises(service.ServiceError):
        marked(project, ws)
    assert (ws / 'conversation.yml').read_bytes() == before
    reset = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture',
        expected_revision=conversation.selection_revision(doc), reset_session=True)
    assert reset['session'] is None and not reset['recovery_required']
    assert reset['turns'] == doc['turns']
    with turn_admission.reserve(project, ws):
        pass


@pytest.mark.parametrize('force_stdin', [False, True])
def test_t5_real_first_resume_and_bounds(k_conversational_project, offline_k_support, monkeypatch, force_stdin):
    project, ws, _ = k_conversational_project
    charter.set_text(ws, 'T5 captured charter')
    resolved = bind(project, ws, monkeypatch, offline_k_support)
    if force_stdin:
        monkeypatch.setattr(agent_run, 'PROMPT_ARGV_LIMIT', 10)
    prepared, launches, spends, sources, copies, requests, accumulators, identities = [], [], [], [], [], [], [], []
    real_prepare, real_start = agent_run.prepare_turn, jobs.start
    real_spend, real_parse, real_copy = service.actions.record_spend, turn_output.read_completed_turn, turn_output.copy_raw_log
    real_read, real_append = turn_artifacts.RegularArtifact.read_at, turn_output._Records._append
    real_create = service._create_turn_artifacts
    def create(artifacts, value):
        assert not artifacts.transcript.exists()
        result = real_create(artifacts, value)
        assert not artifacts.transcript.exists(), 'marked transcript must be late exclusive'
        identities.append((result[0].st_dev, result[0].st_ino))
        return result
    def prepare(*a, **kw):
        value = real_prepare(*a, **kw)
        prepared.append(value)
        return value
    def start(*a, **kw):
        value = prepared[-1]
        assert a[1] == list(value.argv) and kw['stdin_text'] == value.stdin_text
        assert kw['sandbox_writable'] == list(value.protection.writable)
        assert kw['sandbox_mask'] == list(value.protection.masks)
        assert value.protection is resolved[-1].protection
        launches.append(kw)
        return real_start(*a, **kw)
    def spend(*a, **kw):
        spends.append(kw)
        return real_spend(*a, **kw)
    def parse(source, family):
        sources.append((source, source.snapshot_size))
        return real_parse(source, family)
    def copy(source, target, **kw):
        assert source is sources[-1][0] and source.snapshot_size == sources[-1][1]
        copies.append((source, target))
        return real_copy(source, target, **kw)
    def read(self, offset, size):
        assert 0 <= size <= 65536
        requests.append(size)
        return real_read(self, offset, size)
    def append(self, piece):
        result = real_append(self, piece)
        accumulators.append(len(self._pending))
        assert len(self._pending) <= 16384
        return result
    def forbidden(*a, **kw):
        raise AssertionError('old whole-output consumer reached')
    real_read_text, real_open = Path.read_text, Path.open
    guard_active = [False]
    class BoundedRead:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return self.stream.__exit__(*a)
        def read(self, size=-1):
            assert type(size) is int and 0 <= size <= 65536, 'unbounded raw read'
            return self.stream.read(size)
    def opened(path, mode='r', *a, **kw):
        stream = real_open(path, mode, *a, **kw)
        if guard_active[0] and (path.suffix in ('.log', '.err') or path.name.endswith('.out.md')) and 'r' in mode:
            return BoundedRead(stream)
        return stream
    def read_text(path, *a, **kw):
        if path.suffix in ('.log', '.err') or path.name.endswith('.out.md'):
            forbidden()
        return real_read_text(path, *a, **kw)
    monkeypatch.setattr(service, '_create_turn_artifacts', create)
    monkeypatch.setattr(agent_run, 'prepare_turn', prepare)
    monkeypatch.setattr(jobs, 'start', start)
    monkeypatch.setattr(service.actions, 'record_spend', spend)
    monkeypatch.setattr(turn_output, 'read_completed_turn', parse)
    monkeypatch.setattr(turn_output, 'copy_raw_log', copy)
    monkeypatch.setattr(turn_artifacts.RegularArtifact, 'read_at', read)
    monkeypatch.setattr(turn_output._Records, '_append', append)
    monkeypatch.setattr(service, 'dispatch_agent', forbidden)
    monkeypatch.setattr(service, '_write_ordinary_transcript', forbidden)
    monkeypatch.setattr(sessions, 'parse', forbidden)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'open', opened)
    with pytest.raises(AssertionError, match='whole-output'):
        (ws / 'self-test.log').read_text()
    (ws / 'self-test.log').write_bytes(b'sentinel')
    guard_active[0] = True
    with (ws / 'self-test.log').open('rb') as stream:
        with pytest.raises(AssertionError, match='unbounded raw read'):
            stream.read(-1)
        assert stream.read(8) == b'sentinel'
    guard_active[0] = False
    fd_before = len(os.listdir('/proc/self/fd'))
    for i in range(2):
        guard_active[0] = True
        response = marked(project, ws)
        guard_active[0] = False
        assert response['job']['state'] == 'done' and response['job']['exit_code'] == 0
        assert response['job']['sandboxed'] is True
        assert response['turn']['text'] == 'K protocol reply'
        doc = conversation.read(ws)
        assert doc['session'] == 'k-session-1' and not doc['recovery_required'] and doc['pending_turn_id'] is None
        assert doc['selection_generation'] == 3 + 2 * i
        value = prepared[-1]
        assert value.composed_bytes == ('/scieflow-menu\n\n---\n\n' + agent_run.CHARTER_HEADER + '\n\nT5 captured charter').encode()
        assert (value.stdin_text is not None) == force_stdin
        if force_stdin:
            assert value.stdin_text.encode() == value.composed_bytes
            assert value.argv[-1] == ('resume' if i else 'first')
        else:
            assert value.argv[-1].encode() == value.composed_bytes
        if i:
            assert value.session == 'k-session-1' and value.argv[value.argv.index('--resume') + 1] == 'k-session-1'
        log, transcript = raw_result(ws, response)
        assert log.read_bytes() == transcript.read_bytes()
        records = [json.loads(line) for line in log.read_bytes().splitlines()]
        assert records[-1]['input_sha256'] == hashlib.sha256(value.composed_bytes).hexdigest()
        audit = response['job']['turn_audit']
        assert audit['parse_complete'] is True and not audit['reason']
        assert (ws / audit['composed_artifact']).read_bytes() == value.composed_bytes
        assert (ws / 'logs' / f"turn-{audit['turn_id']}.md").read_bytes() == value.accepted_bytes
        assert all(not s._active and not t._active for s, t in copies)
        with turn_admission.reserve(project, ws):
            pass
        assert len(os.listdir('/proc/self/fd')) == fd_before
    assert len(resolved) == len(prepared) == len(launches) == len(spends) == len(sources) == len(copies) == len(identities) == 2
    assert requests and max(requests) <= 65536 and accumulators and max(accumulators) <= 16384


@pytest.mark.parametrize('raw,reason', [
    (b'{"type":"result",', 'invalid-json'),
    (b'{"type":"result","result":"partial"}\n', 'missing-session'),
    (b'{"type":"system","session_id":"a"}\n{"type":"result","session_id":"b","result":"partial"}\n', 'contradictory-session'),
    (b'\xff\x00\x1b', 'invalid-utf8'),
    (b'{"type":"result","session_id":"k-session-1","result":"' + b'a' * 16384 + b'"}\n', 'record-limit'),
    (b'noise\n' * 350000 + b'{"type":"result","session_id":"k-session-1","result":"late"}\n', 'parse-limit'),
], ids=['malformed', 'missing', 'contradictory', 'invalid-utf8', 'record-limit', 'parse-limit'])
def test_t5_real_incomplete_raw_is_exact_uncapped(k_conversational_project, offline_k_support, monkeypatch, raw, reason):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    real_wait = jobs.wait
    reached = []
    def wait(*a):
        value = real_wait(*a)
        assert value.state == 'done' and value.exit_code == 0
        Path(value.log).write_bytes(raw)
        reached.append(value.id)
        return value
    monkeypatch.setattr(jobs, 'wait', wait)
    response = marked(project, ws)
    assert reached == [response['job']['id']]
    log, transcript = raw_result(ws, response)
    assert log.read_bytes() == transcript.read_bytes() == raw
    assert response['job']['state'] == 'done' and response['turn']['audit']['reason'] == reason
    assert response['turn']['audit']['parse_complete'] is False
    assert_recovery(project, ws, response)


@pytest.mark.parametrize('fault', ['source-between', 'short-write', 'target-corrupt', 'logs-parent', 'run-root',
    'file', 'symlink', 'hardlink', 'fifo'])
def test_t5_real_copy_refusal_and_cleanup(k_conversational_project, offline_k_support, monkeypatch, fault):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    real_parse, real_write, real_launch = turn_output.read_completed_turn, turn_artifacts.ExclusiveArtifact.write, service._launch_prepared_turn
    sentinel = ws.parent / 'outside-sentinel'
    sentinel.write_bytes(b'outside-secret-not-consumed')
    reached, owned, output = [], [], []
    def launch(project, value, *, artifacts, turn_audit):
        result = real_launch(project, value, artifacts=artifacts, turn_audit=turn_audit)
        output.append(artifacts.transcript)
        if fault == 'logs-parent':
            artifacts.raw.parent.rename(ws / 'original-logs')
            artifacts.raw.parent.mkdir()
        elif fault == 'run-root':
            ws.rename(ws.with_name('original-run'))
            ws.mkdir()
        elif fault == 'file':
            artifacts.transcript.write_bytes(sentinel.read_bytes())
        elif fault == 'symlink':
            artifacts.transcript.symlink_to(sentinel)
        elif fault == 'hardlink':
            os.link(sentinel, artifacts.transcript)
        elif fault == 'fifo':
            os.mkfifo(artifacts.transcript)
        if fault in ('logs-parent', 'run-root', 'file', 'symlink', 'hardlink', 'fifo'):
            reached.append(fault)
        return result
    def parse(source, family):
        result = real_parse(source, family)
        assert result.complete and result.id == 'k-session-1'
        owned.append(source)
        if fault == 'source-between':
            with (ws / 'jobs' / source._name).open('ab') as stream:
                stream.write(b'mutated-after-parse')
            reached.append(fault)
        return result
    def write(target, raw):
        count = real_write(target, raw[:1] if fault == 'short-write' else raw)
        owned.append(target)
        if fault == 'short-write':
            reached.append(fault)
        elif fault == 'target-corrupt':
            with output[-1].open('r+b') as stream:
                stream.write(b'!')
            reached.append(fault)
        return count
    monkeypatch.setattr(service, '_launch_prepared_turn', launch)
    monkeypatch.setattr(turn_output, 'read_completed_turn', parse)
    monkeypatch.setattr(turn_artifacts.ExclusiveArtifact, 'write', write)
    if fault == 'run-root':
        # Admission itself correctly rejects a changed protected parent.
        with pytest.raises(service.ServiceError, match='uncertain'):
            marked(project, ws)
    else:
        response = marked(project, ws)
        assert response['job']['state'] == 'done' and response['job']['exit_code'] == 0
        assert response['turn']['audit']['parse_complete'] is False
        assert_recovery(project, ws, response)
        if fault in ('file', 'symlink', 'hardlink'):
            assert output[-1].read_bytes() == b'outside-secret-not-consumed'
        elif fault == 'fifo':
            assert output[-1].is_fifo()
        elif fault == 'short-write':
            assert output[-1].stat().st_size == 1
    assert reached and sentinel.read_bytes() == b'outside-secret-not-consumed'
    assert all(not item._active for item in owned)


@pytest.mark.parametrize('field', ['id', 'run_dir', 'log', 'err'])
@pytest.mark.parametrize('error_site', [False, True])
def test_t5_forged_job_never_becomes_save_destination(k_conversational_project, offline_k_support, monkeypatch, field, error_site):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    sentinel = ws.parent / 'outside.json'
    sentinel.write_bytes(b'outside-unchanged')
    real_wait, real_save = jobs.wait, jobs.save
    forged, saves = [], []
    def wait(*a):
        value = real_wait(*a)
        assert value.state == 'done' and value.exit_code == 0
        setattr(value, field, 'bad/id' if field == 'id' else str(sentinel.with_suffix('.log')) if field == 'log' else str(sentinel))
        forged.append(value)
        return value
    def save(value):
        if forged and value is forged[-1]:
            saves.append(value.id)
            pytest.fail('forged marked job reached jobs.save')
        return real_save(value)
    def fail(*a, **kw):
        raise OSError('real completion boundary exception')
    monkeypatch.setattr(jobs, 'wait', wait)
    monkeypatch.setattr(jobs, 'save', save)
    if error_site:
        monkeypatch.setattr(service, '_complete_marked_turn', fail)
    with pytest.raises(service.ServiceError, match='uncertain'):
        marked(project, ws)
    assert len(forged) == 1 and not saves and sentinel.read_bytes() == b'outside-unchanged'
    doc = conversation.read(ws)
    assert doc['pending_turn_id'] or doc['recovery_required']
    assert doc['session'] is None


@pytest.mark.parametrize('state', ['failed', 'timeout', 'cancelled', 'lost'])
def test_t5_actual_process_states_not_completion_states(k_conversational_project, offline_k_support, monkeypatch, state):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    path = project.root / 'config/agents.yml'
    data = yaml.safe_load(path.read_text())
    cfg = data['agents']['k-protocol-fixture']
    if state == 'failed':
        cfg['session_cmd'] = '/bin/false {prompt}'
    elif state in ('timeout', 'cancelled'):
        # The timeout clock starts at jobs.wait, not at launch: a child that outlives
        # the start->wait window under host load exits 0 first and reads as 'done'.
        # 30 s keeps that window far away; timeout/cancel kill it at once anyway.
        cfg['session_cmd'] = "/bin/sh -c 'sleep 30' fixture {prompt}"
        if state == 'timeout':
            cfg['timeout_min'] = 0.001
    path.write_text(yaml.safe_dump(data))
    reached = []
    real_wait = jobs.wait
    if state == 'cancelled':
        def wait(job, proc):
            jobs.cancel(job)
            result = real_wait(job, proc)
            reached.append(result.state)
            return result
        monkeypatch.setattr(jobs, 'wait', wait)
    elif state == 'lost':
        def wait(job, proc):
            # Real abandoned record: child exits, no _finish; reconcile discovers loss.
            assert proc.wait(timeout=5) == 0
            lost = jobs.reconcile(project, ws)
            assert len(lost) == 1 and lost[0].id == job.id
            reached.append(lost[0].state)
            return lost[0]
        monkeypatch.setattr(jobs, 'wait', wait)
    response = marked(project, ws)
    assert response['job']['state'] == state
    actual = jobs.load(Path(response['job']['log']).with_suffix('.json'))
    assert actual.state == state
    if state in ('cancelled', 'lost'):
        assert reached == [state]
    log, transcript = raw_result(ws, response)
    assert log.read_bytes() == transcript.read_bytes()
    assert_recovery(project, ws, response)


@pytest.mark.parametrize('boundary', ['final-record', 'response-loss'])
def test_t5_actual_completion_record_uncertainty(k_conversational_project, offline_k_support, monkeypatch, boundary):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    calls = []
    def fail(*a, **kw):
        calls.append(boundary)
        raise OSError('record uncertainty')
    monkeypatch.setattr(conversation if boundary == 'final-record' else service,
        'finalize_turn' if boundary == 'final-record' else 'asdict', fail)
    with pytest.raises(service.ServiceError, match='uncertain'):
        marked(project, ws)
    assert calls
    doc = conversation.read(ws)
    if boundary == 'final-record':
        assert doc['pending_turn_id'] and doc['session'] is None
    else:
        assert not doc['pending_turn_id'] and not doc['recovery_required'] and doc['session'] == 'k-session-1'
    assert len(jobs.list_jobs(project, ws)) == 1
    with turn_admission.reserve(project, ws):
        pass


def test_t5_ordinary_first_resume_and_uncapped_parser(k_conversational_project, monkeypatch):
    project, ws, _ = k_conversational_project
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    real_parse = sessions.parse
    observed = []
    def parse(cfg, raw):
        observed.append(len(raw.encode()))
        return real_parse(cfg, raw)
    monkeypatch.setattr(sessions, 'parse', parse)
    for i in range(2):
        response = service.say(project, 'r1', 'ordinary charter-first')
        assert response['job']['state'] == 'done' and response['job']['exit_code'] == 0
        assert response['turn']['text'] == 'K protocol reply'
        assert conversation.read(ws)['session'] == 'k-session-1'
        if i:
            assert '--resume' in response['job']['argv']
    real_wait = jobs.wait
    raw = json.dumps({'type': 'result', 'session_id': 'k-session-1', 'result': 'o' * (2097152 + 1)})
    def wait(*a):
        result = real_wait(*a)
        assert result.state == 'done' and result.exit_code == 0
        Path(result.log).write_text(raw)
        return result
    monkeypatch.setattr(jobs, 'wait', wait)
    response = service.say(project, 'r1', 'ordinary remains uncapped')
    assert len(response['turn']['text']) == 2097153 and observed[-1] > 2097152


def test_t5_default_caps_and_same_full_raw_snapshot(k_conversational_project, offline_k_support, monkeypatch):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    # A valid diagnostic before a >2MiB undelimited record: no whole-line materialization.
    raw = b'{"type":"assistant","message":{"content":[{"type":"text","text":"retained"}]}}\n' + b'x' * (2097152 + 123) + b'\xff\x00'
    real_wait, real_read = jobs.wait, turn_artifacts.RegularArtifact.read_at
    real_parse, real_copy, real_append = turn_output.read_completed_turn, turn_output.copy_raw_log, turn_output._Records._append
    source_seen, parse_requests, copy_requests, pending = [], [], [], []
    stage = []
    def wait(*a):
        result = real_wait(*a)
        assert result.state == 'done' and result.exit_code == 0
        Path(result.log).write_bytes(raw)
        return result
    def read(source, offset, size):
        if stage and source is source_seen[-1]:
            (parse_requests if stage[-1] == 'parse' else copy_requests).append((offset, size))
        assert 0 <= size <= 65536
        return real_read(source, offset, size)
    def parse(source, family):
        assert source.snapshot_size == len(raw)
        source_seen.append(source)
        stage.append('parse')
        result = real_parse(source, family)
        stage.pop()
        assert result.text == 'retained' and result.id is None and result.reason == 'parse-limit'
        return result
    def copy(source, target, **kw):
        assert source is source_seen[-1] and source.snapshot_size == len(raw)
        stage.append('copy')
        result = real_copy(source, target, **kw)
        stage.pop()
        assert result.complete and result.bytes_copied == len(raw)
        return result
    def append(records, piece):
        result = real_append(records, piece)
        pending.append(len(records._pending))
        assert len(records._pending) <= 16384
        return result
    monkeypatch.setattr(jobs, 'wait', wait)
    monkeypatch.setattr(turn_artifacts.RegularArtifact, 'read_at', read)
    monkeypatch.setattr(turn_output, 'read_completed_turn', parse)
    monkeypatch.setattr(turn_output, 'copy_raw_log', copy)
    monkeypatch.setattr(turn_output._Records, '_append', append)
    response = marked(project, ws)
    assert sum(size for _, size in parse_requests) == 2097152
    assert max(offset + size for offset, size in parse_requests) == 2097152
    assert sum(size for _, size in copy_requests) == len(raw)
    assert pending and max(pending) <= 16384 and not source_seen[-1]._active
    log, transcript = raw_result(ws, response)
    assert log.read_bytes() == transcript.read_bytes() == raw
    assert response['turn']['text'] == 'retained'
    assert_recovery(project, ws, response)


@pytest.mark.parametrize('fault', ['source-short-read', 'read-error', 'logs-between-parse-copy'])
def test_t5_real_io_incomplete_has_fixed_reason(k_conversational_project, offline_k_support, monkeypatch, fault):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)
    real_read, real_parse = turn_artifacts.RegularArtifact.read_at, turn_output.read_completed_turn
    reached = []
    def read(source, offset, size):
        raw = real_read(source, offset, size)
        if source._name.endswith('.log') and fault != 'logs-between-parse-copy':
            reached.append(fault)
            if fault == 'read-error':
                raise OSError('private-error-not-for-audit')
            return raw[:-1]
        return raw
    def parse(source, family):
        result = real_parse(source, family)
        if fault == 'logs-between-parse-copy':
            assert result.complete and result.id == 'k-session-1'
            (ws / 'logs').rename(ws / 'saved-logs')
            (ws / 'logs').mkdir()
            reached.append(fault)
        return result
    monkeypatch.setattr(turn_artifacts.RegularArtifact, 'read_at', read)
    monkeypatch.setattr(turn_output, 'read_completed_turn', parse)
    response = marked(project, ws)
    assert reached and response['job']['state'] == 'done'
    reason = response['turn']['audit']['reason']
    assert reason in ('source-short-read', 'copy-invalidated', 'artifact-acquisition-incomplete')
    assert 'private-error' not in reason and response['turn']['audit']['parse_complete'] is False
    assert_recovery(project, ws, response)


def test_t5_exact_consumer_producer_imports():
    source = Path(__file__).resolve().parents[2] / 'src/scieflow/core'
    for module in (jobs, service, turn_artifacts, turn_output):
        assert type(module.__file__) is str
        actual = Path(module.__file__).resolve()
        assert actual == source / (module.__name__.rsplit('.', 1)[-1] + '.py')
        print(f'TASK5_IMPORT_ORIGIN module={module.__name__} file={actual}', flush=True)


def test_pf6_real_oversize_user_record_turn_completes(k_conversational_project, offline_k_support, monkeypatch):
    project, ws, _ = k_conversational_project
    bind(project, ws, monkeypatch, offline_k_support)

    def line(event):
        return json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    body = 'u' * 14500
    user = {'type': 'user',
            'message': {'role': 'user', 'content': [{'tool_use_id': 'toolu_pf6', 'type': 'tool_result',
                                                     'content': body}]},
            'parent_tool_use_id': None, 'session_id': 'k-session-1', 'uuid': 'pf6-user',
            'timestamp': '2026-10-21T19:00:00Z',
            'tool_use_result': {'type': 'text', 'file': {'filePath': '/synthetic/AGENTS.md', 'content': body,
                                                         'numLines': 1, 'startLine': 1, 'totalLines': 1}},
            'tool_result_meta': [{'id': 'toolu_pf6', 'permission_decision':
                                  {'decision': 'allow', 'source': 'config', 'reason_type': 'rule'}}]}
    assert len(line(user)) >= 28336
    raw = (line({'type': 'system', 'subtype': 'init', 'session_id': 'k-session-1'}) +
           line({'type': 'assistant', 'session_id': 'k-session-1',
                 'message': {'content': [{'type': 'tool_use', 'id': 'toolu_pf6', 'name': 'Read',
                                          'input': {'file_path': '/synthetic/AGENTS.md'}}]}}) +
           line(user) +
           line({'type': 'assistant', 'session_id': 'k-session-1',
                 'message': {'content': [{'type': 'text', 'text': 'pf6 reply'}]}}) +
           line({'type': 'result', 'session_id': 'k-session-1', 'result': 'pf6 reply'}))
    real_wait = jobs.wait
    reached = []

    def wait(*a):
        value = real_wait(*a)
        assert value.state == 'done' and value.exit_code == 0
        Path(value.log).write_bytes(raw)
        reached.append(value.id)
        return value
    monkeypatch.setattr(jobs, 'wait', wait)
    response = marked(project, ws)
    assert reached == [response['job']['id']]
    log, transcript = raw_result(ws, response)
    assert log.read_bytes() == transcript.read_bytes() == raw
    assert response['job']['state'] == 'done' and response['job']['exit_code'] == 0
    assert response['turn']['text'] == 'pf6 reply'
    audit = response['turn']['audit']
    assert audit['parse_complete'] is True and audit['reason'] == ''
    doc = conversation.read(ws)
    assert not doc['recovery_required'] and doc['session'] == 'k-session-1' and doc['pending_turn_id'] is None
