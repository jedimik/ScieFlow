"""Actual Task6 consumers: authenticated HTTP, owned ASGI, DOM and sockets."""
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from starlette.requests import Request

from scieflow.core import jobs, turn_artifacts as artifacts
from scieflow.web import pages
import importlib.util

_socket_spec = importlib.util.spec_from_file_location('task6_socket_fixtures', Path(__file__).with_name('test_sse.py'))
_socket_module = importlib.util.module_from_spec(_socket_spec)
_socket_spec.loader.exec_module(_socket_module)
live, read_frames = _socket_module.live, _socket_module.read_frames

RUN = Path('/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008')
JS = Path('/home/ubuntu/.hermes/hermes-agent/node_modules/jsdom/lib/api.js')


def units(text):
    return len(text.encode('utf-16-le')) // 2


def selected_job(project):
    return jobs.list_jobs(project, project.run_dir('r1'))[0]


def request(client, method='GET', headers=()):
    return Request({'type': 'http', 'method': method, 'path': '/', 'headers': headers,
                    'app': client.app, 'http_version': '1.1'})


def consume(response, req, on_send=None, disconnect=False):
    messages = []
    async def run():
        async def send(message):
            messages.append(message)
            if on_send:
                on_send(message)
        async def receive():
            if disconnect:
                return {'type': 'http.disconnect'}
            await asyncio.sleep(60)
        await response(req.scope, receive, send)
    asyncio.run(asyncio.wait_for(run(), 5))
    return messages


def test_task6_http_baseline(client, project):
    """Run BEFORE replacing the file route; fixed installed compatibility."""
    import starlette
    from starlette import responses
    ws = project.run_dir('r1')
    raw = b'0123456789abcdef'
    (ws / 'baseline.png').write_bytes(raw)
    (ws / 'special &+.txt').write_text('hello')
    (ws / 'contained.txt').symlink_to('special &+.txt')
    (ws / 'baseline.html').write_text('<script>evil()</script>')
    records = []
    for path in ('special &+.txt', 'contained.txt', 'baseline.html', 'baseline.png'):
        result = client.get('/runs/r1/file', params={'path': path})
        assert result.status_code == 200
        records.append({'path': path, 'status': result.status_code, 'headers': dict(result.headers), 'body': result.text})
    assert records[2]['headers']['content-type'] == 'application/octet-stream'
    assert 'attachment' in records[2]['headers']['content-disposition']
    cases = [('bytes=2-5', 206, raw[2:6]), ('bytes=-4', 206, raw[-4:]),
             ('bytes=0-1,4-5', 206, None), ('bogus', 400, None), ('bytes=100-', 416, b'')]
    for header, status, expected in cases:
        result = client.get('/runs/r1/file', params={'path': 'baseline.png'}, headers={'Range': header})
        assert result.status_code == status
        if expected is not None:
            assert result.content == expected
        if ',' in header:
            assert b'01' in result.content and b'45' in result.content
            assert len(result.content) == int(result.headers['content-length'])
        records.append({'range': header, 'status': result.status_code, 'headers': dict(result.headers), 'body': result.text})
    full = client.get('/runs/r1/file', params={'path': 'baseline.png'})
    for if_range, status in ((full.headers['etag'], 206), (full.headers['last-modified'], 206), ('no-match', 200)):
        result = client.get('/runs/r1/file', params={'path': 'baseline.png'}, headers={'Range': 'bytes=0-1', 'If-Range': if_range})
        assert result.status_code == status
        records.append({'if_range': if_range, 'status': status, 'body': result.text})
    head = client.head('/runs/r1/file', params={'path': 'baseline.png'})
    assert head.status_code == 405 and head.content == b''
    print('TASK6B_HTTP_BASELINE', json.dumps({'starlette': starlette.__version__, 'source': responses.__file__, 'records': records, 'HEAD': 405}, sort_keys=True))


def test_task6_job_no_whole_read_and_sanitized(client, project, monkeypatch):
    job = selected_job(project)
    raw = ('\x1b[31mred\x1b[0m\r\u202e<script>bad()</script>😀' * 18000).encode()
    Path(job.log).write_bytes(raw)
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        if path.suffix in ('.log', '.err'):
            raise AssertionError('whole log read')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', guarded)
    result = client.get(f'/runs/r1/jobs/{job.id}')
    assert result.status_code == 200
    from html import unescape
    text = unescape(result.text.split('id="out">', 1)[1].split('</pre>', 1)[0])
    assert units(text) <= 200000
    assert '\x1b' not in text and '\r' not in text and '\u202e' not in text
    assert '\\x0d' in text and '\\u202e' in text and 'raw artifact retained' in text
    assert '<script>bad()</script>' not in result.text
    assert hashlib.sha256(Path(job.log).read_bytes()).digest() == hashlib.sha256(raw).digest()


@pytest.mark.parametrize('field', ['label', 'argv', 'audit', 'error'])
def test_task6_selected_fields(client, project, field):
    from scieflow.core import service, store
    from scieflow.core.run import conversation
    ws = project.run_dir('r1')
    evil = '\x1b[31mvisible\x1b[0m\r\u202e<script>bad()</script>'
    job = selected_job(project)
    if field == 'label':
        job.label = evil
        jobs.save(job)
    elif field == 'argv':
        job.argv = [evil] * 15000
        jobs.save(job)
    elif field == 'audit':
        conversation.add_turn(ws, role='human', text=evil, job_id=job.id)
        conversation.add_turn(ws, role='agent', text=evil, job_id=job.id)
    result = client.get('/runs/r1', params={'error': evil}) if field in ('audit', 'error') else client.get(f'/runs/r1/jobs/{job.id}')
    assert result.status_code == 200
    assert '\x1b' not in result.text and '\r' not in result.text and '\u202e' not in result.text
    assert '\\x0d' in result.text and '\\u202e' in result.text
    assert '<script>bad()</script>' not in result.text
    if field == 'argv':
        from html import unescape
        argv = unescape(result.text.split('<code>', 1)[1].split('</code>', 1)[0])
        assert units(argv) <= 200000 and 'raw artifact retained' in argv


@pytest.mark.parametrize('field', ['log', 'err', 'run_dir'])
def test_task6_forged_record_refused(client, project, field):
    job = selected_job(project)
    outside = project.root / 'OUTSIDE.log'
    outside.write_text('OUTSIDE SECRET')
    doc = json.loads(Path(job.log).with_suffix('.json').read_text())
    doc[field] = str(outside)
    Path(job.log).with_suffix('.json').write_text(json.dumps(doc))
    result = client.get(f'/runs/r1/jobs/{job.id}')
    assert result.status_code == 404 and 'OUTSIDE SECRET' not in result.text


def test_task6_file_inline_guard_and_raw_link(client, project, monkeypatch):
    ws = project.run_dir('r1')
    raw = b'\x1b[31mred\x1b[0m\r<script>bad()</script>'
    (ws / 'a &+.log').write_bytes(raw)
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        if path.name == 'a &+.log':
            raise AssertionError('whole inline read')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', guarded)
    result = client.get('/runs/r1/file', params={'path': 'a &+.log'})
    assert result.status_code == 200 and '\x1b' not in result.text and '\r' not in result.text
    assert 'path=a%20%26%2B.log' in result.text and 'download=true' in result.text
    result = client.get('/runs/r1/file', params={'path': 'a &+.log', 'download': 'true'})
    assert result.content == raw
    assert result.headers['content-type'] == 'application/octet-stream'
    assert 'attachment' in result.headers['content-disposition'] and result.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('kind', ['leaf', 'ancestor', 'root', 'alias', 'hardlink'])
def test_task6_response_after_endpoint_swap(client, project, kind):
    ws = project.run_dir('r1')
    (ws / 'dir').mkdir()
    source = ws / 'dir' / 'x.png'
    source.write_bytes(b'original')
    rel = 'dir/x.png'
    if kind == 'alias':
        (ws / 'alias.png').symlink_to('dir/x.png')
        rel = 'alias.png'
    req = request(client)
    response = pages.file_view(req, 'r1', rel)
    if kind == 'leaf':
        source.rename(source.with_name('old.png')); source.write_bytes(b'OUTSIDE')
    elif kind == 'ancestor':
        (ws / 'dir').rename(ws / 'old-dir'); (ws / 'dir').mkdir(); (ws / 'dir' / 'x.png').write_bytes(b'OUTSIDE')
    elif kind == 'root':
        ws.rename(ws.with_name('old-r1')); ws.mkdir(); (ws / 'dir').mkdir(); source.write_bytes(b'OUTSIDE')
    elif kind == 'alias':
        (ws / 'alias.png').unlink(); (ws / 'alias.png').symlink_to('../OUTSIDE.png')
        (ws.parent / 'OUTSIDE.png').write_bytes(b'OUTSIDE')
    else:
        os.link(source, ws / 'other.png')
    messages = consume(response, req)
    assert messages[0]['status'] == 400
    assert b'OUTSIDE' not in b''.join(m.get('body', b'') for m in messages)
    assert not response.handle._active and not response.handle._root._active


@pytest.mark.parametrize('kind', ['grow', 'truncate', 'rewrite'])
def test_task6_response_mid_transfer_mutation(client, project, kind):
    ws = project.run_dir('r1'); source = ws / 'large.png'
    source.write_bytes(b'a' * 140000)
    req = request(client); response = pages.file_view(req, 'r1', 'large.png')
    messages = []
    def mutate(message):
        messages.append(message)
        if message['type'] == 'http.response.body' and message.get('body'):
            if kind == 'grow':
                with source.open('ab') as f: f.write(b'x')
            elif kind == 'truncate': source.write_bytes(b'')
            else:
                with source.open('r+b') as f: f.write(b'changed')
    with pytest.raises(Exception, match='artifact|transfer'):
        consume(response, req, mutate)
    assert messages[0]['status'] == 200
    assert not any(m['type'] == 'http.response.body' and not m.get('more_body', False) for m in messages)
    assert not response.handle._active and not response.handle._root._active


@pytest.mark.parametrize('method,headers', [('GET', ()), ('HEAD', ()), ('GET', ((b'range', b'bytes=1-3'),)), ('GET', ((b'range', b'wrong'),)), ('GET', ((b'range', b'bytes=100-'),)), ('GET', ((b'range', b'bytes=0-1,4-5'),))])
def test_task6_response_owned_cleanup(client, project, method, headers, monkeypatch):
    ws = project.run_dir('r1'); (ws / 'x.png').write_bytes(b'abcdefghij')
    req = request(client, method, headers)
    response = pages.file_view(req, 'r1', 'x.png')
    original = os.pread
    reads = []
    def bounded(fd, size, offset):
        assert size <= 65536
        reads.append((fd, size, offset))
        return original(fd, size, offset)
    monkeypatch.setattr(os, 'pread', bounded)
    req.scope['extensions'] = {'http.response.pathsend': {}}
    messages = consume(response, req)
    assert not any(m['type'] == 'http.response.pathsend' for m in messages)
    assert not response.handle._active and not response.handle._root._active
    if reads: assert {r[0] for r in reads} == {response.handle._fd}


@pytest.mark.parametrize('mode', ['send-error', 'cancel', 'disconnect'])
def test_task6_response_error_cleanup(client, project, mode):
    ws = project.run_dir('r1'); (ws / 'x.png').write_bytes(b'x' * 200000)
    req = request(client); response = pages.file_view(req, 'r1', 'x.png')
    def fail(message):
        if message['type'] == 'http.response.body':
            raise asyncio.CancelledError() if mode == 'cancel' else RuntimeError('synthetic send failure')
    if mode == 'disconnect':
        consume(response, req, disconnect=True)
    else:
        with pytest.raises(BaseException): consume(response, req, fail)
    assert not response.handle._active and not response.handle._root._active


@pytest.mark.parametrize('kind', ['fifo', 'socket', 'directory', 'outside', 'hardlink'])
def test_task6_file_unsafe_pre_acquisition(client, project, kind):
    import socket
    ws = project.run_dir('r1'); source = ws / 'unsafe.png'; sock = None
    if kind == 'fifo': os.mkfifo(source)
    elif kind == 'socket':
        # Kernel AF_UNIX pathname limit is independent of fixture ancestry.
        # Bind the SAME directory via its short descriptor spelling only.
        parent = os.open(ws, os.O_RDONLY | os.O_DIRECTORY)
        try:
            sock = socket.socket(socket.AF_UNIX); sock.bind(f'/proc/self/fd/{parent}/unsafe.png')
        finally:
            os.close(parent)
    elif kind == 'directory': source.mkdir()
    else:
        outside = project.root / 'outside.png'; outside.write_bytes(b'SECRET')
        if kind == 'outside': source.symlink_to(outside)
        else: os.link(outside, source)
    try:
        result = client.get('/runs/r1/file', params={'path': 'unsafe.png'})
        assert result.status_code == 400 and b'SECRET' not in result.content
    finally:
        if sock: sock.close()


def test_task6_dom_actual_template(client, project, tmp_path):
    job = selected_job(project); job.state = 'running'; jobs.save(job)
    html = client.get(f'/runs/r1/jobs/{job.id}').text
    source = tmp_path / 'rendered.html'; source.write_text(html)
    # The jsdom harness takes ~6 s alone; on a shared 4-CPU host it exceeded the old 15 s cap (G6D-7 flake). 60 s
    # bounds a real hang without failing on load.
    harness = Path(__file__).with_name('k_task6_dom.cjs')
    result = subprocess.run(['node', str(harness), str(source), str(JS)], capture_output=True, text=True, timeout=60)
    print('TASK6B_REAL_DOM', result.stdout, result.stderr)
    assert result.returncode == 0
    assert 'REAL_DOM_PASS' in result.stdout


@pytest.mark.parametrize('raw,expected', [(b'\x1b[31mred\x1b[0m\r\xe2\x80\xae\n', 'red\\x0d\\u202e'), (b'x' * 16385 + b'\nrecovered\n', 'recovered'), (b'\x1b]' + b'x' * 30000 + b'\nsecret\x07public\n', 'public'), (b'\x01' * 10000 + b'\n', 'truncated')], ids=['controls', 'raw-record-discard', 'osc-cross-record-discard', 'wire-expansion'])
def test_task6_socket_bounded_sse(live, project, raw, expected):
    job = selected_job(project); Path(job.log).write_bytes(raw)
    frames = []
    with live.stream('GET', f'/api/v1/jobs/{job.id}/log/stream', timeout=6) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith('data: '):
                assert len((line + '\n\n').encode()) <= 16384
                assert '\x1b' not in line and '\r' not in line and '\u202e' not in line
                frames.append(line)
                if expected in line: break
    assert any(expected in frame for frame in frames)
    if expected == 'public': assert not any('secret' in f for f in frames)
    assert Path(job.log).read_bytes() == raw


def test_task6_socket_append_responsive_o1_cleanup(live, project, monkeypatch):
    from scieflow.web import auth, sse
    job, proc = jobs.start(project, [sys.executable, '-c', 'import time; time.sleep(60)'], kind='agent', cwd=project.root, run_dir=project.run_dir('r1'))
    original = jobs.locate_artifacts; calls = []; acquired = []
    from contextlib import contextmanager
    @contextmanager
    def tracked(*args, **kwargs):
        calls.append(args[1])
        with original(*args, **kwargs) as location:
            acquired.append(location.root)
            yield location
    monkeypatch.setattr(jobs, 'locate_artifacts', tracked)
    def no_find(*args, **kwargs): raise AssertionError('global polling via find')
    monkeypatch.setattr(jobs, 'find', no_find)
    try:
        with live.stream('GET', f'/api/v1/jobs/{job.id}/log/stream', timeout=8) as response:
            assert response.status_code == 200
            with Path(job.log).open('ab') as f: f.write(b'hello wor')
            time.sleep(0.7)
            with Path(job.log).open('ab') as f: f.write(b'ld\n')
            assert next(line for line in response.iter_lines() if line.startswith('data: ')) == 'data: hello world'
            start = time.monotonic(); assert live.get('/healthz').status_code == 200
            assert time.monotonic() - start < 2
        deadline = time.monotonic() + 3
        while acquired and acquired[0]._active and time.monotonic() < deadline: time.sleep(0.02)
        assert len(calls) == 1 and acquired and not acquired[0]._active
        assert jobs.peek_state(Path(job.log)) == 'running'
    finally:
        jobs.cancel(job); jobs.wait(job, proc)


def test_task6_socket_final_partial_after_unread_extent(live, project):
    job = selected_job(project)
    raw = (b'line\n' * 15000) + b'last-partial'
    Path(job.log).write_bytes(raw)
    frames = read_frames(live, f'/api/v1/jobs/{job.id}/log/stream', want=15001, timeout=10)
    assert frames[-1] == 'last-partial' and frames[:-1] == ['line'] * 15000


def test_task6_socket_replacement_stops_view_only(live, project):
    job = selected_job(project); Path(job.log).write_bytes(b'first\n'); job.state = 'running'; jobs.save(job)
    record = Path(job.log).with_suffix('.json').read_bytes()
    with live.stream('GET', f'/api/v1/jobs/{job.id}/log/stream', timeout=6) as response:
        lines = response.iter_lines()
        assert next(line for line in lines if line.startswith('data: ')) == 'data: first'
        Path(job.log).rename(Path(job.log).with_suffix('.old'))
        Path(job.log).write_bytes(b'OUTSIDE\n')
        frames = [line for line in lines if line.startswith('data: ')]
        assert frames and any('invalidated' in f for f in frames)
        assert not any('OUTSIDE' in f for f in frames)
    assert Path(job.log).with_suffix('.json').read_bytes() == record


def test_task6_anonymous_and_invalid_sse(client, project):
    from fastapi.testclient import TestClient
    from scieflow.web.app import create_app
    with TestClient(create_app(project, 'token')) as anonymous:
        assert anonymous.get('/runs/r1/file', params={'path': 'status.yml'}).status_code == 401
        assert anonymous.get('/api/v1/jobs/bad/log/stream').status_code == 401
    assert client.get('/api/v1/jobs/bad/log/stream').status_code == 404


def test_task6_guards_reject_whole_read_positive(tmp_path, monkeypatch):
    p = tmp_path / 'positive.log'; p.write_text('x')
    def refuse(*args, **kwargs): raise AssertionError('guard detected read')
    monkeypatch.setattr(Path, 'read_text', refuse)
    with pytest.raises(AssertionError, match='guard detected read'): p.read_text()


def test_task6_live_positive_append_and_snapshot_strict(tmp_path):
    path = tmp_path / 'live'; path.write_bytes(b'')
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_live_artifact(root, 'live') as source, artifacts.open_run_artifact(root, 'live') as snapshot:
            assert source.extent() == 0 and source.read_available(0, 65536) == b''
            with path.open('ab') as f: f.write(b'first')
            assert source.extent() == 5
            assert source.read_available(0, 65536) == b'first'
            with pytest.raises(artifacts.ArtifactError): snapshot.verify_unchanged()
            with path.open('ab') as f: f.write(b'-second')
            assert source.read_available(5, 65536) == b'-second'
            fd = source._fd
        with pytest.raises(OSError): os.fstat(fd)


@pytest.mark.parametrize('kind', ['replace', 'truncate', 'metadata', 'hardlink', 'ancestor'])
def test_task6_live_negative_mutation_sticky(tmp_path, kind):
    (tmp_path / 'd').mkdir(); path = tmp_path / 'd' / 'live'; path.write_bytes(b'initial')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_live_artifact(root, 'd/live') as source:
        assert source.extent() == 7
        if kind == 'replace':
            path.rename(path.with_name('old')); path.write_bytes(b'changed')
        elif kind == 'truncate': path.write_bytes(b'')
        elif kind == 'metadata': os.chmod(path, 0o600)
        elif kind == 'hardlink': os.link(path, path.with_name('link'))
        else:
            (tmp_path / 'd').rename(tmp_path / 'old'); (tmp_path / 'd').mkdir()
        with pytest.raises(artifacts.ArtifactError): source.extent()
        with pytest.raises(artifacts.ArtifactError): source.read_available(0, 1)


@pytest.mark.parametrize('offset,size', [(-1, 1), (True, 1), (0, True), (0, -1), (0, 65537)])
def test_task6_live_read_exact_integer_bounds(tmp_path, offset, size):
    (tmp_path / 'live').write_bytes(b'initial')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_live_artifact(root, 'live') as source:
        with pytest.raises(ValueError): source.read_available(offset, size)


def test_task6_live_pre_post_read_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'live'; path.write_bytes(b'initial')
    original = os.pread
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_live_artifact(root, 'live') as source:
        def mutate(fd, size, offset):
            data = original(fd, size, offset)
            path.write_bytes(b'changed')
            # Deliberate metadata drift, not a claim that identical coarse
            # timestamps detect every privileged same-inode rewrite.
            info = os.stat(path)
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
            return data
        monkeypatch.setattr(os, 'pread', mutate)
        with pytest.raises(artifacts.ArtifactError): source.read_available(0, 7)


def test_task6_socket_unscoped_ordinary_and_duplicate_refusal(live, project):
    job = jobs.run_blocking(project, [sys.executable, '-c', 'print("ordinary")'], kind='ordinary', cwd=project.root)
    assert read_frames(live, f'/api/v1/jobs/{job.id}/log/stream', 1) == ['ordinary']
    ws = project.run_dir('r1'); d = ws / 'jobs'; d.mkdir(exist_ok=True)
    doc = json.loads(Path(job.log).with_suffix('.json').read_text())
    doc['run_dir'] = str(ws); doc['log'] = str(d / f'{job.id}.log'); doc['err'] = str(d / f'{job.id}.err')
    (d / f'{job.id}.json').write_text(json.dumps(doc))
    result = live.get(f'/api/v1/jobs/{job.id}/log/stream')
    assert result.status_code == 404
