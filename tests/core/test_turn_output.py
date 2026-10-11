"""Bounded completed protocol parsing and exact finite raw-copy controls."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
import os


import pytest

from scieflow.core import turn_artifacts as artifacts, turn_output as output


def record(event, newline=True):
    return json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode() + (b'\n' if newline else b'')


def protocol(text='reply', session='s-1'):
    return (record({'type': 'system', 'subtype': 'init', 'session_id': session}) +
            record({'type': 'assistant', 'session_id': session,
                    'message': {'content': [{'type': 'text', 'text': text}]}}) +
            record({'type': 'result', 'session_id': session, 'result': text}))


class CountedBytes(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []

    def read(self, size: int | None = -1):
        assert type(size) is int and 0 <= size <= 65536, 'unbounded materialization'
        self.requests.append(size)
        return super().read(size)


@contextmanager
def source(tmp_path, data, monkeypatch=None):
    path = tmp_path / 'source'
    path.write_bytes(data)
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        stream = CountedBytes(data)
        consumed = []
        if monkeypatch is not None:
            real_read = handle.read_at
            def counted(offset, size):
                handle.verify_unchanged()
                stream.seek(offset)
                value = stream.read(size)
                # The counted seam delegates real fd acquisition AND actual read.
                assert value == real_read(offset, size)
                consumed.append(len(value))
                return value
            monkeypatch.setattr(handle, 'read_at', counted)
        yield root, handle, stream, consumed
    assert path.read_bytes() == data


def test_counted_sentinel_rejects_unbounded_read():
    stream = CountedBytes(b'positive sentinel')
    with pytest.raises(AssertionError, match='unbounded'):
        stream.read(-1)
    assert stream.read(8) == b'positive'


def test_completed_valid_nonduplicated_and_frozen(tmp_path, monkeypatch):
    data = b'ordinary warning\n' + protocol('Žluťoučký 🦊')
    with source(tmp_path, data, monkeypatch) as (_, handle, stream, consumed):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed == output.BoundedSession('s-1', 'Žluťoučký 🦊', True, '')
        assert max(stream.requests) <= 65536 and sum(consumed) == len(data)
        with pytest.raises(FrozenInstanceError):
            parsed.complete = False
        for family in ('codex', 'agy', 'unknown'):
            unsupported = output.read_completed_turn(handle, family)
            assert unsupported.id is None and not unsupported.complete and unsupported.reason == 'unsupported-family'
        assert output.read_completed_turn(handle, 'claude') == parsed


def test_stable_large_raw_copy_no_parse_cap(tmp_path, monkeypatch):
    data = (b'\xff\0\x1b]52;unchanged\x07' * 150000) + protocol()
    assert len(data) > 2097152
    with source(tmp_path, data, monkeypatch) as (root, handle, stream, consumed):
        with artifacts.create_run_artifact(root, 'copy') as target:
            result = output.copy_raw_log(handle, target)
            assert result == output.RawCopyResult(len(data), True, '')
            with pytest.raises(FrozenInstanceError):
                result.complete = False
        assert max(stream.requests) <= 65536
        assert sum(consumed) == len(data)
        assert hashlib.sha256((tmp_path / 'copy').read_bytes()).digest() == hashlib.sha256(data).digest()


def test_assistant_fallback_final_no_newline(tmp_path):
    data = record({'type': 'assistant', 'session_id': 'only',
                   'message': {'content': [{'type': 'text', 'text': 'first'},
                                           {'type': 'tool_use', 'name': 'not text'},
                                           {'type': 'text', 'text': 'second'}]}}, newline=False)
    with source(tmp_path, data) as (_, handle, _, _):
        assert output.read_completed_turn(handle, 'claude') == output.BoundedSession('only', 'first\n\nsecond', True, '')


@pytest.mark.parametrize('chunk', [0, -1, 65537, True, '1'])
def test_invalid_chunk_arguments(tmp_path, chunk):
    with source(tmp_path, b'abc') as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        with pytest.raises(ValueError):
            output.copy_raw_log(handle, target, chunk_bytes=chunk)


def test_unowned_path_not_a_consumer(tmp_path):
    with pytest.raises(ValueError):
        output.read_completed_turn(tmp_path / 'log', 'claude')
    with pytest.raises(ValueError):
        output.copy_raw_log(tmp_path / 'log', tmp_path / 'copy')


def noise(count):
    line = b'n' * 1000 + b'\n'
    whole, remaining = divmod(count, len(line))
    return line * whole + (b'n' * (remaining - 1) + b'\n' if remaining else b'')


@pytest.mark.parametrize('extra', [0, 1, 65537])
def test_default_aggregate_boundary_late_session(tmp_path, monkeypatch, extra):
    valid = protocol('late reply', 'late-id')
    data = noise(2097152 - len(valid)) + valid + b'x' * extra
    with source(tmp_path, data, monkeypatch) as (_, handle, stream, consumed):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.complete is (extra == 0)
        assert parsed.id == ('late-id' if not extra else None)
        assert parsed.text == 'late reply'
        assert parsed.reason == ('' if not extra else 'parse-limit')
        assert max(stream.requests) == 65536
        assert sum(consumed) == 2097152


def test_id_beyond_budget_not_recovered(tmp_path, monkeypatch):
    data = noise(2097152) + protocol('never parsed', 'beyond')
    with source(tmp_path, data, monkeypatch) as (_, handle, stream, consumed):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed == output.BoundedSession(None, '', False, 'parse-limit')
        assert sum(consumed) == 2097152 and len(stream.requests) == 32


@pytest.mark.parametrize('newline', [False, True])
@pytest.mark.parametrize('extra', [0, 1])
def test_default_record_boundary_includes_delimiter(tmp_path, monkeypatch, newline, extra):
    event = {'type': 'result', 'session_id': 'edge', 'result': ''}
    base = record(event, newline=newline)
    event['result'] = 't' * (16384 - len(base) + extra)
    data = record(event, newline=newline)
    assert len(data) == 16384 + extra
    retained = []
    append = output._Records._append
    def measured(self, piece):
        append(self, piece)
        retained.append(len(self._pending))
        assert len(self._pending) <= 16384
    monkeypatch.setattr(output._Records, '_append', measured)
    with source(tmp_path, data, monkeypatch) as (_, handle, _, _):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.complete is (extra == 0)
        assert parsed.id == ('edge' if not extra else None)
        assert parsed.text == (event['result'] if not extra else '')
        assert parsed.reason == ('' if not extra else 'record-limit')
    assert max(retained, default=0) <= 16384


@pytest.mark.parametrize('kind', ['json-prefix', 'json-suffix', 'osc', 'no-newline'])
def test_overflow_discards_whole_record_sticky(tmp_path, monkeypatch, kind):
    valid = record({'type': 'result', 'session_id': 'overflow-id', 'result': 'must not appear'}, newline=False)
    if kind == 'json-prefix':
        giant = valid + b' ' * 200000
    elif kind == 'json-suffix':
        giant = b' ' * 200000 + valid
    elif kind == 'osc':
        giant = b'\x1b]52;' + b'x' * 200000 + b'\x07'
    else:
        giant = b'x' * 200000
    data = giant if kind == 'no-newline' else giant + b'\n' + protocol('later diagnostic', 'later')
    retained = []
    append = output._Records._append
    def measured(self, piece):
        append(self, piece)
        retained.append(len(self._pending))
    monkeypatch.setattr(output._Records, '_append', measured)
    with source(tmp_path, data, monkeypatch) as (_, handle, stream, consumed):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.id is None and not parsed.complete and parsed.reason == 'record-limit'
        assert parsed.text == ('' if kind == 'no-newline' else 'later diagnostic')
        assert max(stream.requests) <= 65536 and sum(consumed) == len(data)
        assert max(retained, default=0) <= 16384


def test_utf8_and_json_split_at_default_chunk_boundary(tmp_path, monkeypatch):
    raw = record({'type': 'result', 'session_id': 'utf8', 'result': '🦊 reply'}, newline=False)
    first_byte = raw.index('🦊'.encode())
    data = noise(65535 - first_byte) + raw
    assert data[65535:65539] == '🦊'.encode()
    with source(tmp_path, data, monkeypatch) as (_, handle, stream, _):
        assert output.read_completed_turn(handle, 'claude') == output.BoundedSession('utf8', '🦊 reply', True, '')
        assert stream.requests[0] == 65536 and len(stream.requests) == 2


@pytest.mark.parametrize('bad,reason', [
    (b'{"type":"result","session_id":"partial"', 'invalid-json'),
    (b'{"type":"result",no-json}\n', 'invalid-json'),
    (b'x\xff\n', 'invalid-utf8'),
    (b'\xf0\x9f', 'invalid-utf8'),
    (b'{"bad":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}\n', 'invalid-json'),
    (b'[]\n', 'invalid-record'),
])
def test_malformed_record_never_reuses_prior_id(tmp_path, bad, reason):
    data = protocol('bounded partial') + bad
    with source(tmp_path, data) as (_, handle, _, _):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed == output.BoundedSession(None, 'bounded partial', False, reason)


@pytest.mark.parametrize('event,reason', [
    ({'type': 'result', 'session_id': 'different', 'result': 'later'}, 'contradictory-session'),
    ({'type': 'system', 'session_id': 42}, 'invalid-session'),
    ({'type': 'assistant', 'session_id': None, 'message': {'content': []}}, 'invalid-session'),
    ({'type': 'result', 'session_id': '', 'result': 'later'}, 'missing-session'),
    ({'type': 'result', 'result': {'not': 'text'}}, 'invalid-field'),
    ({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 1}]}}, 'invalid-field'),
    ({'type': 'assistant', 'message': 'not an object'}, 'invalid-field'),
    ({'type': 'assistant', 'message': {'content': 'not a list'}}, 'invalid-field'),
])
def test_session_and_text_fields_not_stringified(tmp_path, event, reason):
    data = protocol('prior') + record(event)
    with source(tmp_path, data) as (_, handle, _, _):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.id is None and parsed.complete is False and parsed.reason == reason
        assert len(parsed.text.encode()) <= len(data)


@pytest.mark.parametrize('data,text', [(b'plain noise\n', ''),
    (record({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'diagnostic'}]}}), 'diagnostic'),
    (record({'type': 'unrecognized', 'session_id': 'not-authority'}), '')])
def test_missing_relevant_session_no_raw_fallback(tmp_path, data, text):
    with source(tmp_path, data) as (_, handle, _, _):
        assert output.read_completed_turn(handle, 'claude') == output.BoundedSession(None, text, False, 'missing-session')


def test_mutation_between_parse_and_copy_no_resnapshot(tmp_path):
    original = protocol()
    path = tmp_path / 'source'
    path.write_bytes(original)
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        assert output.read_completed_turn(handle, 'claude').complete
        with path.open('ab') as stream:
            stream.write(protocol('later', 'changed'))
        assert handle.snapshot_size == len(original)
        with artifacts.create_run_artifact(root, 'copy') as target:
            result = output.copy_raw_log(handle, target)
            assert not result.complete and result.bytes_copied == 0
        assert (tmp_path / 'copy').read_bytes() == b''
        assert not output.read_completed_turn(handle, 'claude').complete
    assert path.read_bytes() == original + protocol('later', 'changed')


@pytest.mark.parametrize('operation', ['parse', 'copy'])
@pytest.mark.parametrize('mutation', ['grow', 'truncate', 'rewrite', 'metadata', 'root', 'parent', 'leaf', 'alias'])
def test_operation_mutation_deterministic_and_fd_cleanup(tmp_path, monkeypatch, operation, mutation):
    home = tmp_path / 'root'
    home.mkdir()
    parent = home / 'dir'
    parent.mkdir()
    path = parent / 'source'
    data = protocol() + noise(80000)
    path.write_bytes(data)
    (home / 'alias').symlink_to('dir')
    before = set(os.listdir('/proc/self/fd'))
    with artifacts.pin_artifact_root(home) as root:
        name = 'alias/source' if mutation == 'alias' else 'dir/source'
        with artifacts.open_run_artifact(root, name, allow_contained_aliases=mutation == 'alias') as handle:
            read = handle.read_at
            mutated = []
            def mutate_after_real_read(offset, size):
                value = read(offset, size)
                if not mutated:
                    mutated.append(True)
                    if mutation == 'grow':
                        with path.open('ab') as stream:
                            stream.write(b'x')
                    elif mutation == 'truncate':
                        path.write_bytes(b'')
                    elif mutation == 'rewrite':
                        info = path.stat()
                        path.write_bytes(b'x' * len(data))
                        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
                    elif mutation == 'metadata':
                        path.chmod(0o400)
                    elif mutation == 'alias':
                        (home / 'alias').unlink()
                        (home / 'alias').symlink_to('different')
                    else:
                        moved = {'root': home, 'parent': parent, 'leaf': path}[mutation]
                        moved.rename(moved.with_name(moved.name + '-old'))
                        if mutation == 'leaf':
                            moved.write_bytes(b'outside-sentinel')
                        else:
                            moved.mkdir()
                return value
            monkeypatch.setattr(handle, 'read_at', mutate_after_real_read)
            if operation == 'parse':
                parsed = output.read_completed_turn(handle, 'claude')
                assert parsed.id is None and not parsed.complete and parsed.reason == 'source-invalidated'
            else:
                with artifacts.create_run_artifact(root, 'copy') as target:
                    result = output.copy_raw_log(handle, target)
                    assert not result.complete and result.reason == 'copy-invalidated'
                    assert result.bytes_copied == (0 if mutation == 'root' else 65536)
            assert mutated == [True]
            if mutation == 'leaf':
                assert path.read_bytes() == b'outside-sentinel'
    assert set(os.listdir('/proc/self/fd')) == before


def test_real_short_write_preserves_actual_partial_bytes(tmp_path, monkeypatch):
    data = b'raw\xff\0bytes'
    real_write = os.write
    with source(tmp_path, data) as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        monkeypatch.setattr(artifacts.os, 'write', lambda fd, value: real_write(fd, value[:3]))
        result = output.copy_raw_log(handle, target)
        assert result == output.RawCopyResult(3, False, 'target-short-write')
    assert (tmp_path / 'copy').read_bytes() == data[:3]


def test_target_swap_after_real_write_counts_partial_no_unlink(tmp_path, monkeypatch):
    data = b'x' * 80000
    real_write = os.write
    with source(tmp_path, data) as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        def write_and_replace(fd, value):
            count = real_write(fd, value)
            (tmp_path / 'copy').rename(tmp_path / 'copy-original')
            (tmp_path / 'copy').write_bytes(b'outside-sentinel')
            return count
        monkeypatch.setattr(artifacts.os, 'write', write_and_replace)
        result = output.copy_raw_log(handle, target)
        assert result == output.RawCopyResult(65536, False, 'copy-invalidated')
    assert (tmp_path / 'copy').read_bytes() == b'outside-sentinel'
    assert (tmp_path / 'copy-original').read_bytes() == data[:65536]


def test_short_read_io_failure_and_closed_handle_incomplete(tmp_path, monkeypatch):
    with source(tmp_path, protocol()) as (root, handle, _, _):
        read = handle.read_at
        monkeypatch.setattr(handle, 'read_at', lambda offset, size: read(offset, size)[:-1])
        assert output.read_completed_turn(handle, 'claude').reason == 'source-short-read'
        with artifacts.create_run_artifact(root, 'copy') as target:
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(0, False, 'source-short-read')
        def io_failure(*args):
            raise OSError('injected I/O')
        monkeypatch.setattr(handle, 'read_at', io_failure)
        assert not output.read_completed_turn(handle, 'claude').complete
        with artifacts.create_run_artifact(root, 'copy2') as target:
            assert not output.copy_raw_log(handle, target).complete
    assert output.read_completed_turn(handle, 'claude').id is None


@pytest.mark.parametrize('raw', [
    b'{"type":"result","session_id":"first","session_id":"other","result":"reply"}\n',
    b'{"type":"result","session_id":"id","result":"first","result":"other"}\n',
    b'{"type":"result","session_id":"id","result":"reply","other":NaN}\n',
])
def test_ambiguous_or_nonstandard_json_not_authority(tmp_path, raw):
    with source(tmp_path, raw) as (_, handle, _, _):
        assert output.read_completed_turn(handle, 'claude') == output.BoundedSession(None, '', False, 'invalid-json')


@pytest.mark.parametrize('depth', [127, 128])
def test_depth_bound_and_structural_bytes_in_strings(tmp_path, depth):
    raw = (b'{"type":"result","session_id":"depth","result":"brackets [\\\"{]", "other":' +
           b'[' * depth + b'0' + b']' * depth + b'}\n')
    with source(tmp_path, raw) as (_, handle, _, _):
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.complete is (depth == 127)
        assert parsed.id == ('depth' if depth == 127 else None)
        assert parsed.reason == ('' if depth == 127 else 'invalid-json')


def test_large_parse_then_whole_raw_copy_same_handle(tmp_path, monkeypatch):
    data = protocol() + noise(2097152) + b'\xff\0\x1b]unchanged'
    with source(tmp_path, data, monkeypatch) as (root, handle, stream, consumed):
        parsed = output.read_completed_turn(handle, 'claude')
        assert not parsed.complete and parsed.id is None and parsed.reason == 'parse-limit'
        parse_reads = len(stream.requests)
        assert sum(consumed) == 2097152
        with artifacts.create_run_artifact(root, 'copy') as target:
            result = output.copy_raw_log(handle, target)
            assert result == output.RawCopyResult(len(data), True, '')
        assert sum(consumed[parse_reads:]) == len(data)
        assert max(stream.requests) == 65536
        assert hashlib.sha256((tmp_path / 'copy').read_bytes()).digest() == hashlib.sha256(data).digest()


@pytest.mark.parametrize('chunk', [1, 10000, 65536])
def test_finite_raw_copy_custom_chunk_and_empty(tmp_path, chunk):
    data = b'\xff\0finite'
    with source(tmp_path, data) as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        assert output.copy_raw_log(handle, target, chunk_bytes=chunk) == output.RawCopyResult(len(data), True, '')
    assert (tmp_path / 'copy').read_bytes() == data
    (tmp_path / 'source').write_bytes(b'')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        assert output.read_completed_turn(handle, 'claude') == output.BoundedSession(None, '', False, 'missing-session')
        with artifacts.create_run_artifact(root, 'empty') as target:
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(0, True, '')


def test_copy_refuses_nonempty_and_previously_consumed_target(tmp_path):
    data = b'finite raw'
    with source(tmp_path, data) as (root, handle, _, _):
        with artifacts.create_run_artifact(root, 'prefix') as target:
            assert target.write(b'existing evidence') == len(b'existing evidence')
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(0, False, 'target-not-fresh')
        assert (tmp_path / 'prefix').read_bytes() == b'existing evidence'
        with artifacts.create_run_artifact(root, 'copy') as target:
            assert output.copy_raw_log(handle, target).complete
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(0, False, 'target-not-fresh')
        assert (tmp_path / 'copy').read_bytes() == data


@pytest.mark.parametrize('mutation', ['grow', 'truncate', 'metadata'])
def test_target_content_mutation_after_real_write_incomplete(tmp_path, monkeypatch, mutation):
    data = b'x' * 80000
    real_write = os.write
    with source(tmp_path, data) as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        def changed(fd, value):
            count = real_write(fd, value)
            if mutation == 'grow':
                with (tmp_path / 'copy').open('ab') as stream:
                    stream.write(b'external')
            elif mutation == 'truncate':
                (tmp_path / 'copy').write_bytes(b'')
            else:
                (tmp_path / 'copy').chmod(0o400)
            return count
        monkeypatch.setattr(artifacts.os, 'write', changed)
        result = output.copy_raw_log(handle, target)
        assert result == output.RawCopyResult(65536, False, 'copy-invalidated')


def test_target_mutation_before_copy_incomplete_no_adoption(tmp_path):
    with source(tmp_path, b'raw') as (root, handle, _, _), artifacts.create_run_artifact(root, 'copy') as target:
        (tmp_path / 'copy').write_bytes(b'external content')
        result = output.copy_raw_log(handle, target)
        assert not result.complete and result.bytes_copied == 0
    assert (tmp_path / 'copy').read_bytes() == b'external content'


@pytest.mark.parametrize('corrupt', [False, True], ids=['stable', 'prior-prefix-rewrite'])
def test_boundaries_copy_rejects_post_write_prefix_adoption(tmp_path, monkeypatch, corrupt):
    data = b'a' * output.CHUNK_BYTES + b'z' * 31
    path, target_path = tmp_path / 'source', tmp_path / 'copy'
    path.write_bytes(data)
    before_fds = set(os.listdir('/proc/self/fd'))
    real_write = os.write
    counts, mutations = [], []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        with artifacts.create_run_artifact(root, 'copy') as target:
            def writing(fd, value):
                assert fd == target._fd
                count = real_write(fd, value)
                counts.append(count)
                if corrupt and len(counts) == 2:
                    before = target_path.stat()
                    with target_path.open('r+b') as other:
                        other.write(b'!')
                    os.utime(target_path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                    after = target_path.stat()
                    assert (after.st_dev, after.st_ino, after.st_size) == (before.st_dev, before.st_ino, before.st_size)
                    assert after.st_mtime_ns != before.st_mtime_ns
                    mutations.append(True)
                return count
            monkeypatch.setattr(artifacts.os, 'write', writing)
            result = output.copy_raw_log(handle, target)
            assert result.bytes_copied == len(data)
            assert result.complete is (not corrupt)
            assert result.reason == ('copy-invalidated' if corrupt else '')
            if corrupt:
                with pytest.raises(artifacts.ArtifactError):
                    target.verify_owned()
            else:
                target.verify_owned()
            handle.verify_unchanged()
    assert counts == [output.CHUNK_BYTES, 31] and mutations == ([True] if corrupt else [])
    assert target_path.read_bytes() == (b'!' + data[1:] if corrupt else data)
    assert path.read_bytes() == data and set(os.listdir('/proc/self/fd')) == before_fds


@pytest.mark.parametrize('data,chunk', [(b'', 65536), (b'\xff\0finite', 1),
    (b'\xff\0\x1b]52;raw\x07' * 250000 + b'end', 65536)], ids=['empty', 'small-chunks', 'above-parse-cap'])
def test_boundaries_copy_one_linear_owned_readback(tmp_path, monkeypatch, data, chunk):
    path, target_path = tmp_path / 'source', tmp_path / 'copy'
    path.write_bytes(data)
    before_fds = set(os.listdir('/proc/self/fd'))
    real_pread = os.pread
    source_reads, target_reads = [], []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        with artifacts.create_run_artifact(root, 'copy') as target:
            def reading(fd, size, offset):
                assert fd in (handle._fd, target._fd) and 0 < size <= output.CHUNK_BYTES
                value = real_pread(fd, size, offset)
                (source_reads if fd == handle._fd else target_reads).append((offset, size, len(value)))
                return value
            monkeypatch.setattr(artifacts.os, 'pread', reading)
            assert output.copy_raw_log(handle, target, chunk_bytes=chunk) == output.RawCopyResult(len(data), True, '')
            assert sum(n for _, _, n in source_reads) == len(data)
            assert sum(n for _, _, n in target_reads) == len(data)
            assert sum(n for _, n, _ in target_reads) == len(data)
            assert [offset for offset, _, _ in target_reads] == list(range(0, len(data), output.CHUNK_BYTES))
            assert all(requested == actual for _, requested, actual in source_reads + target_reads)
    assert hashlib.sha256(target_path.read_bytes()).digest() == hashlib.sha256(data).digest()
    assert path.read_bytes() == data and set(os.listdir('/proc/self/fd')) == before_fds


@pytest.mark.parametrize('fault', ['short-read', 'io-error', 'path', 'content', 'metadata', 'parent', 'root', 'source'])
def test_boundaries_copy_fault_during_target_readback(tmp_path, monkeypatch, fault):
    home = tmp_path / 'home'
    home.mkdir()
    parent = home / 'dir'
    parent.mkdir()
    path, target_path = parent / 'source', parent / 'copy'
    data = b'raw\xff\0' * 20000
    path.write_bytes(data)
    before_fds = set(os.listdir('/proc/self/fd'))
    real_pread = os.pread
    reads, mutations = [], []
    with artifacts.pin_artifact_root(home) as root, artifacts.open_run_artifact(root, 'dir/source') as handle:
        with artifacts.create_run_artifact(root, 'dir/copy') as target:
            def reading(fd, size, offset):
                value = real_pread(fd, size, offset)
                if fd == target._fd:
                    assert 0 < size <= output.CHUNK_BYTES
                    reads.append((offset, size, len(value)))
                    if not mutations:
                        mutations.append(fault)
                        if fault == 'short-read':
                            return value[:-1]
                        if fault == 'io-error':
                            raise OSError('injected after real readback')
                        if fault in ('content', 'source'):
                            changed = path if fault == 'source' else target_path
                            before = changed.stat()
                            with changed.open('r+b') as other:
                                other.write(b'!')
                            os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                        elif fault == 'metadata':
                            target_path.chmod(0o400)
                        elif fault == 'path':
                            target_path.rename(parent / 'old-copy')
                            target_path.write_bytes(b'outside-sentinel')
                        else:
                            moved = parent if fault == 'parent' else home
                            moved.rename(moved.with_name('old-' + moved.name))
                            moved.mkdir()
                return value
            monkeypatch.setattr(artifacts.os, 'pread', reading)
            result = output.copy_raw_log(handle, target)
            assert mutations == [fault] and reads
            assert result == output.RawCopyResult(len(data), False, 'copy-invalidated')
            if fault == 'source':
                target.verify_owned()  # target still exact; final SOURCE check must reject copy
                with pytest.raises(artifacts.ArtifactError):
                    handle.verify_unchanged()
            else:
                monkeypatch.setattr(artifacts.os, 'pread', real_pread)
                with pytest.raises(artifacts.ArtifactError):
                    target.verify_owned()
                with pytest.raises(artifacts.ArtifactError):
                    target.write(b'no retry')
    kept_parent = (home / 'old-dir' if fault == 'parent' else
                   tmp_path / 'old-home/dir' if fault == 'root' else parent)
    kept_target = kept_parent / ('old-copy' if fault == 'path' else 'copy')
    assert kept_target.read_bytes() == (b'!' + data[1:] if fault == 'content' else data)
    assert (kept_parent / 'source').read_bytes() == (b'!' + data[1:] if fault == 'source' else data)
    if fault == 'path':
        assert target_path.read_bytes() == b'outside-sentinel'
    assert set(os.listdir('/proc/self/fd')) == before_fds


def test_boundaries_short_write_then_binding_failure_retains_count(tmp_path, monkeypatch):
    data = b'intended raw bytes'
    path, target_path = tmp_path / 'source', tmp_path / 'copy'
    path.write_bytes(data)
    before_fds = set(os.listdir('/proc/self/fd'))
    real_write = os.write
    counts = []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        with artifacts.create_run_artifact(root, 'copy') as target:
            def writing(fd, value):
                count = real_write(fd, value[:3])
                counts.append(count)
                target_path.rename(tmp_path / 'old-copy')
                target_path.write_bytes(b'outside-sentinel')
                return count
            monkeypatch.setattr(artifacts.os, 'write', writing)
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(3, False, 'copy-invalidated')
            with pytest.raises(artifacts.ArtifactError):
                target.verify_owned()
            assert output.copy_raw_log(handle, target) == output.RawCopyResult(0, False, 'copy-invalidated')
    assert counts == [3] and (tmp_path / 'old-copy').read_bytes() == data[:3]
    assert target_path.read_bytes() == b'outside-sentinel' and path.read_bytes() == data
    assert set(os.listdir('/proc/self/fd')) == before_fds


@pytest.mark.parametrize('edge', ['true-eof', 'invalid-suffix', 'physical-overflow', 'newline-at-cap'])
def test_boundaries_parse_budget_is_not_physical_eof(tmp_path, monkeypatch, edge):
    prior_text, late_text = 'earlier whole record', 'late whole result'
    prior = record({'type': 'assistant', 'session_id': 'edge-session',
                    'message': {'content': [{'type': 'text', 'text': prior_text}]}})
    late = record({'type': 'result', 'session_id': 'edge-session', 'result': late_text},
                  newline=edge == 'newline-at-cap')
    prefix = prior + noise(output.PARSE_BYTES - len(prior) - len(late)) + late
    suffix = {'true-eof': b'', 'invalid-suffix': b'not-json\n',
              'physical-overflow': b' ' * (output.RECORD_BYTES + 1) + b'\n',
              'newline-at-cap': b'not consumed\n'}[edge]
    assert len(prefix) == 2097152 and output.PARSE_BYTES == 2097152
    assert output.RECORD_BYTES == 16384 and output.CHUNK_BYTES == 65536
    assert prefix.endswith(b'\n') is (edge == 'newline-at-cap')
    data = prefix + suffix
    path = tmp_path / 'source'
    path.write_bytes(data)
    before_fds = set(os.listdir('/proc/self/fd'))
    requests = []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        real_read = handle.read_at
        def reading(offset, size):
            assert 0 < size <= output.CHUNK_BYTES
            value = real_read(offset, size)
            requests.append((offset, size, len(value)))
            return value
        monkeypatch.setattr(handle, 'read_at', reading)
        parsed = output.read_completed_turn(handle, 'claude')
        assert parsed.text == (prior_text if edge in ('invalid-suffix', 'physical-overflow') else late_text)
        assert parsed.complete is (edge == 'true-eof')
        assert parsed.id == ('edge-session' if edge == 'true-eof' else None)
        assert parsed.reason == ('' if edge == 'true-eof' else 'parse-limit')
    assert sum(size for _, size, _ in requests) == output.PARSE_BYTES
    assert sum(actual for _, _, actual in requests) == output.PARSE_BYTES
    assert max(size for _, size, _ in requests) == output.CHUNK_BYTES
    assert path.read_bytes() == data and set(os.listdir('/proc/self/fd')) == before_fds
