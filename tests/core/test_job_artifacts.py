"""Trusted jobs-reader behavior and finite descriptor ownership."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat

import pytest

from scieflow.core import jobs, store, turn_artifacts


def record(project, run=None, *, jid=None):
    root = project.state_dir if run is None else run
    directory = root / 'jobs'
    directory.mkdir(parents=True, exist_ok=True)
    jid = jid or store.new_id()
    value = jobs.Job(id=jid, kind='turn', argv=['ordinary-data'], cwd=str(root), run_dir=str(run) if run else None,
        log=str(directory / f'{jid}.log'), err=str(directory / f'{jid}.err'), state='running')
    jobs.save(value)
    Path(value.log).write_bytes(b'raw-log')
    Path(value.err).write_bytes(b'raw-error')
    return value


def identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


@pytest.mark.parametrize('global_mode', [False, True])
def test_location_selected_global_positive_and_lifetime(project, global_mode):
    ws = project.run_dir('r1')
    value = record(project, ws)
    fd_before = len(os.listdir('/proc/self/fd'))
    with jobs.locate_artifacts(project, value.id, run_dir=None if global_mode else ws,
            expected_run_identity=None if global_mode else identity(ws)) as location:
        assert location.job_id == value.id and location.run_dir == ws and location.root.path == ws
        assert location.relative_log == f'jobs/{value.id}.log' and location.relative_err == f'jobs/{value.id}.err'
        assert location.read_job() == value and location.peek_state() == 'running'
        with location.open_log() as source:
            assert source.read_at(0, 65536) == b'raw-log'
        assert not source._active
        with location.open_log('stderr') as error:
            assert error.read_at(0, 65536) == b'raw-error'
        with pytest.raises((AttributeError, TypeError)):
            location.run_dir = project.root
    assert not location.root._active and location.peek_state() is None
    with pytest.raises(jobs.JobArtifactError):
        location.read_job()
    assert len(os.listdir('/proc/self/fd')) == fd_before


def test_global_unscoped_and_state_override_once(project, monkeypatch, tmp_path):
    state = tmp_path / 'host-state'
    monkeypatch.setenv('SCIEFLOW_STATE_DIR', str(state))
    value = record(project)
    with jobs.locate_artifacts(project, value.id) as location:
        assert location.run_dir is None and location.root.path == state
        monkeypatch.setenv('SCIEFLOW_STATE_DIR', str(tmp_path / 'different-state'))
        assert location.peek_state() == 'running'
        assert location.read_job().run_dir is None
        with location.open_log() as source:
            assert source.read_at(0, 65536) == b'raw-log'


@pytest.mark.parametrize('bad', [None, True, 4, '', '.', '..', '../outside', '0' * 25, '0' * 27,
    '8' + '0' * 25, 'z' * 26, 'I' * 26, 'L' * 26, 'O' * 26, 'U' * 26, '0' * 26 + '\n',
    ' ' + '0' * 26, '0' * 25 + '\0', '0' * 25 + '/', '0' * 25 + '\\'])
def test_invalid_id_before_any_io(project, monkeypatch, bad):
    def forbidden(*a, **kw):
        pytest.fail('invalid ULID reached filesystem I/O')
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(os, 'stat', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    with pytest.raises(jobs.JobArtifactError, match='invalid-job-id'):
        with jobs.locate_artifacts(project, bad, run_dir=project.run_dir('r1')):
            pass


@pytest.mark.parametrize('field,value', [('id', 'wrong'), ('run_dir', None), ('run_dir', 'outside-sensitive'),
    ('log', 'outside-sensitive'), ('err', 'outside-sensitive'), ('state', 'invented'),
    ('turn_audit', {'secret-invalid-field': 'outside-sensitive'}), ('argv', 'wrong-type')])
@pytest.mark.parametrize('global_mode', [False, True])
def test_forged_record_refuses_no_raw_error(project, field, value, global_mode):
    ws = project.run_dir('r1')
    original = record(project, ws)
    path = Path(original.log).with_suffix('.json')
    data = asdict(original)
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(jobs.JobArtifactError) as caught:
        with jobs.locate_artifacts(project, original.id, run_dir=None if global_mode else ws):
            pass
    assert 'outside-sensitive' not in str(caught.value)


@pytest.mark.parametrize('unsafe', ['duplicate-key', 'array', 'symlink', 'hardlink', 'fifo', 'directory'])
def test_unsafe_record_and_outside_sentinel(project, unsafe):
    ws = project.run_dir('r1')
    value = record(project, ws)
    path = Path(value.log).with_suffix('.json')
    outside = ws.parent / 'outside-record'
    outside.write_bytes(path.read_bytes())
    original = outside.read_bytes()
    path.unlink()
    if unsafe == 'duplicate-key':
        path.write_text('{"id":"outside-secret","id":"' + value.id + '"}')
    elif unsafe == 'array':
        path.write_text('[]')
    elif unsafe == 'symlink':
        path.symlink_to(outside)
    elif unsafe == 'hardlink':
        os.link(outside, path)
    elif unsafe == 'fifo':
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, value.id):
            pass
    assert outside.read_bytes() == original


def test_global_duplicate_is_not_first_match_or_claimed_owner(project):
    ws = project.run_dir('r1')
    one = record(project, ws)
    second = project.run_dir('r2')
    record(project, second, jid=one.id)
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, one.id):
            pass
    # Even a duplicate that lies about ownership refuses, rather than being skipped.
    path = second / 'jobs' / f'{one.id}.json'
    data = json.loads(path.read_text())
    data.update(run_dir=one.run_dir, log=one.log, err=one.err)
    path.write_text(json.dumps(data))
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, one.id):
            pass


@pytest.mark.parametrize('route', ['alias', 'outside', 'nested', 'changed-root', 'jobs-link'])
def test_selected_root_authority(project, route):
    ws = project.run_dir('r1')
    value = record(project, ws)
    original_identity = identity(ws)
    selected = ws
    if route == 'alias':
        selected = ws.with_name('alias')
        selected.symlink_to(ws, target_is_directory=True)
    elif route == 'outside':
        selected = project.root / 'outside'
        selected.mkdir()
    elif route == 'nested':
        selected = ws / 'nested'
        selected.mkdir()
    elif route == 'changed-root':
        ws.rename(ws.with_name('old-run'))
        ws.mkdir()
    else:
        (ws / 'jobs').rename(ws / 'real-jobs')
        (ws / 'jobs').symlink_to(ws / 'real-jobs', target_is_directory=True)
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, value.id, run_dir=selected, expected_run_identity=original_identity):
            pass


@pytest.mark.parametrize('unsafe', ['symlink', 'hardlink', 'fifo', 'directory'])
def test_log_no_alias_type_link_and_cleanup(project, unsafe):
    ws = project.run_dir('r1')
    value = record(project, ws)
    outside = ws.parent / 'outside-log'
    outside.write_bytes(b'outside-secret')
    path = Path(value.log)
    path.unlink()
    if unsafe == 'symlink': path.symlink_to(outside)
    elif unsafe == 'hardlink': os.link(outside, path)
    elif unsafe == 'fifo': os.mkfifo(path)
    else: path.mkdir()
    before = len(os.listdir('/proc/self/fd'))
    with jobs.locate_artifacts(project, value.id, run_dir=ws) as location:
        with pytest.raises(jobs.JobArtifactError):
            with location.open_log():
                pytest.fail('unsafe log yielded')
        with pytest.raises(jobs.JobArtifactError):
            with location.open_log('../outside'):
                pass
    assert outside.read_bytes() == b'outside-secret' and len(os.listdir('/proc/self/fd')) == before


def test_selected_poll_same_parent_o1_atomic_replacement_and_transients(project, monkeypatch):
    ws = project.run_dir('r1')
    value = record(project, ws)
    with jobs.locate_artifacts(project, value.id, run_dir=ws) as location:
        def forbidden(*a, **kw):
            pytest.fail('selected poll rediscovered project')
        monkeypatch.setattr(jobs, 'find', forbidden)
        monkeypatch.setattr(jobs, 'list_jobs', forbidden)
        monkeypatch.setattr(Path, 'glob', forbidden)
        monkeypatch.setattr(os, 'scandir', forbidden)
        for state in ['queued', 'running', 'done']:
            value.state = state
            jobs.save(value)  # Real atomic replacement must work with fresh record handles.
            assert location.peek_state() == state
        path = Path(value.log).with_suffix('.json')
        path.write_bytes(b'{')
        assert location.peek_state() is None
        path.unlink()
        assert location.peek_state() is None
        jobs.save(value)
        assert location.peek_state() == 'done'
        (ws / 'jobs').rename(ws / 'old-jobs')
        (ws / 'jobs').mkdir()
        jobs.save(value)
        assert location.peek_state() is None, 'must not adopt replacement jobs parent'


def test_record_finite_bounded_reads_no_metadata_cap(project, monkeypatch):
    ws = project.run_dir('r1')
    value = record(project, ws)
    value.argv = ['x' * (2097152 + 1)]
    jobs.save(value)
    real_read = turn_artifacts.RegularArtifact.read_at
    requests = []
    def read(self, offset, size):
        requests.append(size)
        assert 0 <= size <= 65536
        return real_read(self, offset, size)
    monkeypatch.setattr(turn_artifacts.RegularArtifact, 'read_at', read)
    with jobs.locate_artifacts(project, value.id, run_dir=ws) as location:
        assert location.read_job().argv == value.argv
    assert requests and max(requests) <= 65536


def test_global_expected_identity_invalid_and_missing_no_mkdir(project):
    ws = project.run_dir('r1')
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, store.new_id(), expected_run_identity=identity(ws)):
            pass
    before = sorted(str(p) for p in project.root.rglob('*'))
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, store.new_id()):
            pass
    assert sorted(str(p) for p in project.root.rglob('*')) == before


@pytest.mark.parametrize('fault', ['record-mutation', 'record-short-read', 'root-replaced', 'log-replaced'])
def test_location_consumption_mutation_and_no_retarget(project, monkeypatch, fault):
    ws = project.run_dir('r1')
    value = record(project, ws)
    real_read = turn_artifacts.RegularArtifact.read_at
    reached = []
    before = len(os.listdir('/proc/self/fd'))
    if fault in ('record-mutation', 'record-short-read'):
        def read(source, offset, size):
            raw = real_read(source, offset, size)
            if source._name.endswith('.json'):
                reached.append(fault)
                if fault == 'record-short-read':
                    return raw[:-1]
                with Path(value.log).with_suffix('.json').open('ab') as stream:
                    stream.write(b' ')
            return raw
        monkeypatch.setattr(turn_artifacts.RegularArtifact, 'read_at', read)
        with pytest.raises(jobs.JobArtifactError):
            with jobs.locate_artifacts(project, value.id, run_dir=ws):
                pass
    else:
        with jobs.locate_artifacts(project, value.id, run_dir=ws) as location:
            if fault == 'root-replaced':
                ws.rename(ws.with_name('old-run'))
                ws.mkdir()
                record(project, ws, jid=value.id)
                reached.append(fault)
                assert location.peek_state() is None
                with pytest.raises(jobs.JobArtifactError):
                    with location.open_log():
                        pass
            else:
                with location.open_log() as source:
                    Path(value.log).rename(ws / 'saved.log')
                    Path(value.log).write_bytes(b'decoy-never-consumed')
                    reached.append(fault)
                    with pytest.raises(turn_artifacts.ArtifactError):
                        source.read_at(0, 65536)
                assert not source._active
    assert reached and len(os.listdir('/proc/self/fd')) == before


def test_global_never_reads_symlink_run_or_jobs_outside(project, monkeypatch):
    ws = project.run_dir('r1')
    value = record(project, ws)
    original = Path(value.log).with_suffix('.json').read_bytes()
    outside = project.root / 'outside'
    (outside / 'jobs').mkdir(parents=True)
    sentinel = outside / 'jobs' / f'{value.id}.json'
    sentinel.write_bytes(original)
    forbidden_inode = sentinel.stat().st_ino
    real_pread = os.pread
    reads = []
    def pread(fd, size, offset):
        assert os.fstat(fd).st_ino != forbidden_inode, 'outside record consumed'
        reads.append(size)
        return real_pread(fd, size, offset)
    monkeypatch.setattr(os, 'pread', pread)
    alias = ws.with_name('outside-alias')
    alias.symlink_to(outside, target_is_directory=True)
    with jobs.locate_artifacts(project, value.id) as location:
        assert location.run_dir == ws
    (ws / 'jobs').rename(ws / 'saved-jobs')
    (ws / 'jobs').symlink_to(outside / 'jobs', target_is_directory=True)
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, value.id):
            pass
    assert reads and sentinel.read_bytes() == original


def test_record_final_consumption_revalidation(project, monkeypatch):
    ws = project.run_dir('r1')
    value = record(project, ws)
    path = Path(value.log).with_suffix('.json')
    real_post_init = jobs.Job.__post_init__
    reached = []
    def changed(job):
        real_post_init(job)
        with path.open('ab') as stream:
            stream.write(b' ')
        reached.append(job.id)
    monkeypatch.setattr(jobs.Job, '__post_init__', changed)
    with pytest.raises(jobs.JobArtifactError):
        with jobs.locate_artifacts(project, value.id, run_dir=ws):
            pass
    assert reached == [value.id]


@pytest.mark.parametrize('global_mode', [False, True])
def test_task6_live_location_append_lifetime_and_selectors(project, global_mode):
    ws = project.run_dir('r1')
    value = record(project, None if global_mode else ws)
    before = set(os.listdir('/proc/self/fd'))
    with jobs.locate_artifacts(project, value.id, run_dir=None if global_mode else ws) as location:
        with location.open_live_log() as source:
            assert source.read_available(0, 65536) == b'raw-log'
            with Path(value.log).open('ab') as f:
                f.write(b'-append')
            assert source.extent() == 14 and source.read_available(7, 65536) == b'-append'
        with location.open_live_log('stderr') as source:
            assert source.read_available(0, 65536) == b'raw-error'
        for bad in ('other', None, 1):
            with pytest.raises(jobs.JobArtifactError):
                with location.open_live_log(bad):
                    pass
    assert set(os.listdir('/proc/self/fd')) == before
