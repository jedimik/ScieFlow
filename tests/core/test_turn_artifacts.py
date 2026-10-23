"""Real-file controls for the descriptor-owning artifact boundary."""
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scieflow.core import turn_artifacts as artifacts


def test_regular_read_exclusive_write_and_lifetime(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'abcdef')
    with artifacts.pin_artifact_root(tmp_path) as root:
        assert root.path == tmp_path
        assert root.identity == (tmp_path.stat().st_dev, tmp_path.stat().st_ino, stat.S_IFDIR)
        with artifacts.open_run_artifact(root, 'source') as handle:
            assert handle.snapshot_size == 6
            assert handle.read_at(1, 3) == b'bcd'
            assert handle.read_at(0, 6) == b'abcdef'
            assert handle.read_at(5, 65536) == b'f'
            assert handle.read_at(10, 1) == b''
            handle.verify_unchanged()
        with artifacts.create_run_artifact(root, 'output') as target:
            assert target.write(b'xyz') == 3
            target.verify_owned()
        assert (tmp_path / 'output').read_bytes() == b'xyz'
        assert stat.S_IMODE((tmp_path / 'output').stat().st_mode) == 0o600
        with pytest.raises(artifacts.ArtifactError):
            handle.read_at(0, 1)
        with pytest.raises(artifacts.ArtifactError):
            target.write(b'x')
    with pytest.raises(artifacts.ArtifactError):
        with artifacts.open_run_artifact(root, 'source'):
            pytest.fail('closed root accepted')


@pytest.mark.parametrize('path', ['', '.', '..', '/source', 'a//b', 'a/./b', 'a/../b', 'a/', 'x\0'])
def test_relative_spelling_rejected(tmp_path, path):
    with artifacts.pin_artifact_root(tmp_path) as root:
        for acquire in (artifacts.open_run_artifact, artifacts.create_run_artifact):
            with pytest.raises(artifacts.ArtifactError):
                with acquire(root, path):
                    pytest.fail('invalid spelling accepted')


def test_root_symlink_and_expected_identity_refused(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(artifacts.ArtifactError):
        with artifacts.pin_artifact_root(alias):
            pytest.fail('symlink root accepted')
    with artifacts.pin_artifact_root(real) as root:
        original = root.identity
    with artifacts.pin_artifact_root(real, expected_identity=original):
        pass
    with pytest.raises(artifacts.ArtifactError):
        with artifacts.pin_artifact_root(real, expected_identity=(original[0], original[1] + 1, original[2])):
            pytest.fail('wrong original root accepted')


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'link', 'hardlink'])
def test_unsafe_leaf_no_outside_read(tmp_path, monkeypatch, kind):
    root_path = tmp_path / 'root'
    root_path.mkdir()
    outside = tmp_path / 'sentinel'
    outside.write_bytes(b'outside-private')
    leaf = root_path / 'leaf'
    if kind == 'directory':
        leaf.mkdir()
    elif kind == 'fifo':
        os.mkfifo(leaf)
    elif kind == 'link':
        leaf.symlink_to(outside)
    else:
        os.link(outside, leaf)
    real_pread = os.pread
    reads = []
    def observed(fd, size, offset):
        reads.append((size, offset))
        return real_pread(fd, size, offset)
    monkeypatch.setattr(artifacts.os, 'pread', observed)
    with artifacts.pin_artifact_root(root_path) as root:
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'leaf'):
                pytest.fail('unsafe leaf accepted')
    assert reads == []
    assert outside.read_bytes() == b'outside-private'


@pytest.mark.parametrize('kind', ['file', 'link'])
def test_exclusive_never_overwrites(tmp_path, kind):
    outside = tmp_path / 'outside'
    outside.write_bytes(b'unchanged')
    leaf = tmp_path / 'output'
    if kind == 'file':
        leaf.write_bytes(b'original')
    else:
        leaf.symlink_to(outside)
    with artifacts.pin_artifact_root(tmp_path) as root:
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.create_run_artifact(root, 'output'):
                pytest.fail('preexisting output accepted')
    assert outside.read_bytes() == b'unchanged'
    assert leaf.read_bytes() == (b'original' if kind == 'file' else b'unchanged')


@pytest.mark.parametrize('offset,size', [(-1, 1), (0, -1), (0, 65537), (True, 1), (0, True)])
def test_bounded_read_arguments(tmp_path, offset, size):
    (tmp_path / 'source').write_bytes(b'hello')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        with pytest.raises(ValueError):
            handle.read_at(offset, size)


@pytest.mark.parametrize('what', ['root', 'parent', 'leaf'])
@pytest.mark.parametrize('operation', ['read', 'write'])
def test_path_drift_before_io(tmp_path, what, operation):
    home = tmp_path / 'root'
    home.mkdir()
    parent = home / 'dir'
    parent.mkdir()
    leaf = parent / 'source'
    leaf.write_bytes(b'original')
    with artifacts.pin_artifact_root(home) as root:
        acquire = artifacts.open_run_artifact if operation == 'read' else artifacts.create_run_artifact
        name = 'dir/source' if operation == 'read' else 'dir/output'
        with acquire(root, name) as handle:
            path = {'root': home, 'parent': parent, 'leaf': home / name}[what]
            path.rename(path.with_name(path.name + '-old'))
            if what == 'leaf':
                path.write_bytes(b'outside-sentinel')
            else:
                path.mkdir()
            with pytest.raises(artifacts.ArtifactError):
                if operation == 'read':
                    handle.read_at(0, 1)
                else:
                    handle.write(b'refused')
            if what == 'leaf':
                assert path.read_bytes() == b'outside-sentinel'


def test_contained_alias_positive_and_retarget(tmp_path):
    (tmp_path / 'dir').mkdir()
    (tmp_path / 'dir/source').write_bytes(b'owned')
    (tmp_path / 'alias').symlink_to('dir', target_is_directory=True)
    (tmp_path / 'dir/leaf').symlink_to('../alias/source')
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_run_artifact(root, 'alias/leaf', allow_contained_aliases=True) as handle:
            assert handle.read_at(0, 100) == b'owned'
            (tmp_path / 'unrelated').write_bytes(b'churn')
            handle.verify_unchanged()
            (tmp_path / 'alias').unlink()
            (tmp_path / 'alias').symlink_to('other', target_is_directory=True)
            with pytest.raises(artifacts.ArtifactError):
                handle.read_at(0, 1)


@pytest.mark.parametrize('kind', ['outside', 'loop', 'dangling'])
def test_contained_alias_negative_without_leaf_read(tmp_path, kind, monkeypatch):
    home = tmp_path / 'root'
    home.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'private')
    targets = {'outside': '../outside', 'loop': 'alias', 'dangling': 'missing'}
    (home / 'alias').symlink_to(targets[kind])
    with artifacts.pin_artifact_root(home) as root:
        def forbidden(*args):
            pytest.fail('outside/nonexistent artifact bytes read')
        monkeypatch.setattr(artifacts.os, 'pread', forbidden)
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'alias', allow_contained_aliases=True):
                pytest.fail('unproved alias accepted')
    assert outside.read_bytes() == b'private'


@pytest.mark.parametrize('mutation', ['grow', 'truncate', 'rewrite', 'metadata', 'hardlink'])
def test_file_facts_invalidation_and_atime_control(tmp_path, mutation):
    source = tmp_path / 'source'
    source.write_bytes(b'original')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        assert handle.read_at(0, 8) == b'original'
        handle.verify_unchanged()  # read-induced atime must not invalidate
        if mutation == 'grow':
            with source.open('ab') as stream:
                stream.write(b'x')
        elif mutation == 'truncate':
            source.write_bytes(b'')
        elif mutation == 'rewrite':
            info = source.stat()
            source.write_bytes(b'changed!')
            # A same-tick same-size write may retain all compared stat facts.
            # Drive actual metadata drift explicitly, not timing/sleep or a
            # false claim of cryptographic same-stat content immutability.
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        elif mutation == 'metadata':
            source.chmod(0o400)
        else:
            os.link(source, tmp_path / 'new-link')
        with pytest.raises(artifacts.ArtifactError):
            handle.read_at(0, 1)


def test_acquisition_swap_refuses_before_read(tmp_path, monkeypatch):
    (tmp_path / 'dir').mkdir()
    (tmp_path / 'dir/source').write_bytes(b'original')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'sentinel')
    real_open = os.open
    with artifacts.pin_artifact_root(tmp_path) as root:
        def swapping(name, flags, *args, **kwargs):
            fd = real_open(name, flags, *args, **kwargs)
            if name == 'source':
                (tmp_path / 'dir/source').rename(tmp_path / 'dir/old')
                (tmp_path / 'dir/source').symlink_to(outside)
            return fd
        monkeypatch.setattr(artifacts.os, 'open', swapping)
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'dir/source'):
                pytest.fail('leaf replacement adopted')
    assert outside.read_bytes() == b'sentinel'


def test_real_device_refused_without_read(monkeypatch):
    with artifacts.pin_artifact_root(Path('/dev')) as root:
        monkeypatch.setattr(artifacts.os, 'pread', lambda *args: pytest.fail('device read'))
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'null'):
                pytest.fail('device accepted')


def test_cleanup_on_success_acquisition_error_and_close_error(tmp_path, monkeypatch):
    (tmp_path / 'dir').mkdir()
    (tmp_path / 'dir/source').write_bytes(b'hello')
    before = set(os.listdir('/proc/self/fd'))
    real_close = os.close
    once = []
    def fail_after_close(fd):
        real_close(fd)
        if not once:
            once.append(fd)
            raise OSError('injected cleanup failure after real close')
    with pytest.raises((OSError, artifacts.ArtifactError)):
        with artifacts.pin_artifact_root(tmp_path) as root:
            monkeypatch.setattr(artifacts.os, 'close', fail_after_close)
    monkeypatch.setattr(artifacts.os, 'close', real_close)
    assert set(os.listdir('/proc/self/fd')) == before
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_run_artifact(root, 'dir/source') as handle:
            assert handle.read_at(0, 10) == b'hello'
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'dir/missing'):
                pytest.fail('missing accepted')
    assert set(os.listdir('/proc/self/fd')) == before


def test_contained_alias_dotdot_uses_real_chain_not_lexical_collapse(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b/sub').mkdir()
    (tmp_path / 'source').write_bytes(b'wrong lexical file')
    (tmp_path / 'b/source').write_bytes(b'real contained file')
    (tmp_path / 'a/jump').symlink_to('../b/sub', target_is_directory=True)
    (tmp_path / 'alias').symlink_to('a/jump/../source')
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_run_artifact(root, 'alias', allow_contained_aliases=True) as handle:
            assert handle.read_at(0, 100) == b'real contained file'


def test_contained_absolute_alias_and_strict_descendant_default(tmp_path):
    (tmp_path / 'dir').mkdir()
    (tmp_path / 'dir/source').write_bytes(b'contained')
    (tmp_path / 'alias').symlink_to(tmp_path / 'dir')
    with artifacts.pin_artifact_root(tmp_path) as root:
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(root, 'alias/source'):
                pytest.fail('strict descendant alias followed')
        with artifacts.open_run_artifact(root, 'alias/source', allow_contained_aliases=True) as handle:
            assert handle.read_at(0, 100) == b'contained'


@pytest.mark.parametrize('stage', ['root', 'parent', 'leaf', 'fifo', 'alias', 'output'])
def test_acquisition_barriers_preserve_sentinel_and_close_fds(tmp_path, monkeypatch, stage):
    home = tmp_path / 'root'
    home.mkdir()
    (home / 'dir').mkdir()
    (home / 'dir/source').write_bytes(b'owned')
    (home / 'alias').symlink_to('dir/source')
    sentinel = tmp_path / 'sentinel'
    sentinel.write_bytes(b'outside sentinel')
    before = set(os.listdir('/proc/self/fd'))
    swaps = []
    if stage == 'root':
        real_directory_open = artifacts._Directories.open
        def opening_root(self, name, *args, **kwargs):
            fd = real_directory_open(self, name, *args, **kwargs)
            if name == 'root' and not swaps:
                swaps.append(True)
                home.rename(tmp_path / 'old-root')
                home.mkdir()
            return fd
        monkeypatch.setattr(artifacts._Directories, 'open', opening_root)
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.pin_artifact_root(home):
                pytest.fail('substituted root accepted')
    else:
        real_open = os.open
        with artifacts.pin_artifact_root(home) as root:
            def opening(name, flags, *args, **kwargs):
                if stage == 'fifo' and name == 'source' and not swaps:
                    swaps.append(True)
                    (home / 'dir/source').unlink()
                    os.mkfifo(home / 'dir/source')
                fd = real_open(name, flags, *args, **kwargs)
                if not swaps and ((stage == 'parent' and name == 'dir') or
                        (stage in ('leaf', 'alias') and name == 'source') or
                        (stage == 'output' and name == 'output')):
                    swaps.append(True)
                    if stage == 'parent':
                        (home / 'dir').rename(home / 'old-dir')
                        (home / 'dir').symlink_to(tmp_path)
                    elif stage == 'alias':
                        (home / 'alias').unlink()
                        (home / 'alias').symlink_to(sentinel)
                    else:
                        path = home / ('output' if stage == 'output' else 'dir/source')
                        path.rename(path.with_name('original'))
                        path.symlink_to(sentinel)
                return fd
            monkeypatch.setattr(artifacts.os, 'open', opening)
            acquire = artifacts.create_run_artifact if stage == 'output' else artifacts.open_run_artifact
            name = 'output' if stage == 'output' else 'alias' if stage == 'alias' else 'dir/source'
            kwargs = {'allow_contained_aliases': True} if stage == 'alias' else {}
            with pytest.raises(artifacts.ArtifactError):
                with acquire(root, name, **kwargs):
                    pytest.fail('acquisition replacement accepted')
    assert swaps == [True]
    assert sentinel.read_bytes() == b'outside sentinel'
    assert set(os.listdir('/proc/self/fd')) == before


def test_socket_nonregular_refused(tmp_path, monkeypatch):
    import socket
    monkeypatch.chdir(tmp_path)  # AF_UNIX basename avoids mount-path length limits
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.bind('socket')
        with artifacts.pin_artifact_root(tmp_path) as root:
            monkeypatch.setattr(artifacts.os, 'pread', lambda *args: pytest.fail('socket bytes read'))
            with pytest.raises(artifacts.ArtifactError):
                with artifacts.open_run_artifact(root, 'socket'):
                    pytest.fail('socket accepted')


def test_private_fd_and_fake_root_and_unsupported_backend(tmp_path, monkeypatch):
    (tmp_path / 'source').write_bytes(b'owned')
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.open_run_artifact(root, 'source') as handle:
        assert not hasattr(handle, 'fd') and not hasattr(handle, 'fileno')
        with artifacts.create_run_artifact(root, 'output') as target:
            assert not hasattr(target, 'fd') and not hasattr(target, 'fileno')
            with pytest.raises(ValueError):
                target.write(b'x' * 65537)
        with pytest.raises(artifacts.ArtifactError):
            with artifacts.open_run_artifact(tmp_path, 'source'):
                pytest.fail('unchecked root accepted')
    monkeypatch.setattr(artifacts.sys, 'platform', 'unsupported')
    with pytest.raises(artifacts.ArtifactError, match='unavailable'):
        with artifacts.pin_artifact_root(tmp_path):
            pytest.fail('unsupported proof fallback')


def test_handle_post_read_invalidation_and_close_failure(tmp_path, monkeypatch):
    path = tmp_path / 'source'
    path.write_bytes(b'owned')
    before = set(os.listdir('/proc/self/fd'))
    real_pread, real_close = os.pread, os.close
    closed = []
    with artifacts.pin_artifact_root(tmp_path) as root:
        with pytest.raises((OSError, artifacts.ArtifactError)):
            with artifacts.open_run_artifact(root, 'source') as handle:
                def mutate(fd, size, offset):
                    value = real_pread(fd, size, offset)
                    path.write_bytes(b'changed')
                    return value
                monkeypatch.setattr(artifacts.os, 'pread', mutate)
                with pytest.raises(artifacts.ArtifactError):
                    handle.read_at(0, 5)
                def cleanup(fd):
                    real_close(fd)
                    if not closed:
                        closed.append(fd)
                        raise OSError('real-close error injection')
                monkeypatch.setattr(artifacts.os, 'close', cleanup)
        monkeypatch.setattr(artifacts.os, 'close', real_close)
        handle._close()  # second close is harmless, never reused fd
    assert set(os.listdir('/proc/self/fd')) == before


def test_removed_pinned_root_acquisition_error_is_safe(tmp_path):
    home = tmp_path / 'root'
    home.mkdir()
    with artifacts.pin_artifact_root(home) as root:
        home.rmdir()
        for acquire in (artifacts.open_run_artifact, artifacts.create_run_artifact):
            with pytest.raises(artifacts.ArtifactError):
                with acquire(root, 'leaf'):
                    pytest.fail('removed root accepted')


def test_double_slash_root_is_not_canonical(tmp_path):
    with pytest.raises(artifacts.ArtifactError):
        with artifacts.pin_artifact_root(Path('/' + str(tmp_path))):
            pytest.fail('noncanonical absolute root accepted')


def test_producer_imports_are_exact_k():
    from scieflow.core import turn_output
    root = Path(__file__).resolve().parents[2]
    for module in (artifacts, turn_output):
        assert isinstance(module.__file__, str)
        actual = Path(module.__file__).resolve()
        expected = root / 'src/scieflow/core' / (module.__name__.rsplit('.', 1)[-1] + '.py')
        print(f'NEW_IMPORT_ORIGIN module={module.__name__} file={actual}', flush=True)
        assert actual == expected


@pytest.mark.parametrize('corrupt', [False, True], ids=['stable', 'prior-prefix-rewrite'])
def test_boundaries_direct_verify_intended_bytes_not_post_write_facts(tmp_path, monkeypatch, corrupt):
    data = b'a' * 65536 + b'z' * 31
    path = tmp_path / 'target'
    before_fds = set(os.listdir('/proc/self/fd'))
    real_write, real_pread = os.write, os.pread
    counts, reads, mutations = [], [], []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.create_run_artifact(root, 'target') as target:
        def writing(fd, value):
            assert fd == target._fd
            count = real_write(fd, value)
            counts.append(count)
            if corrupt and len(counts) == 2:
                before = path.stat()
                with path.open('r+b') as other:
                    other.write(b'!')
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                after = path.stat()
                assert (after.st_dev, after.st_ino, after.st_size) == (before.st_dev, before.st_ino, before.st_size)
                assert after.st_mtime_ns != before.st_mtime_ns
                mutations.append(True)
            return count
        def reading(fd, size, offset):
            assert fd == target._fd and 0 < size <= 65536
            value = real_pread(fd, size, offset)
            reads.append((offset, size, len(value)))
            return value
        monkeypatch.setattr(artifacts.os, 'write', writing)
        monkeypatch.setattr(artifacts.os, 'pread', reading)
        assert target.write(data[:65536]) == 65536
        assert target.write(data[65536:]) == 31
        assert reads == []  # per-write binding checks must not rescan growing prefixes
        if corrupt:
            with pytest.raises(artifacts.ArtifactError):
                target.verify_owned()
            with pytest.raises(artifacts.ArtifactError):
                target.verify_owned()
            with pytest.raises(artifacts.ArtifactError):
                target.write(b'not adopted')
        else:
            target.verify_owned()
            assert reads == [(0, 65536, 65536), (65536, 31, 31)]
            target.verify_owned()  # explicit callers get a fresh full check, not a cached seal
            assert reads[2:] == reads[:2]
    assert counts == [65536, 31] and mutations == ([True] if corrupt else [])
    assert path.read_bytes() == (b'!' + data[1:] if corrupt else data)
    assert set(os.listdir('/proc/self/fd')) == before_fds


@pytest.mark.parametrize('fault', ['short-read', 'io-error'])
def test_boundaries_direct_readback_failure_permanently_invalidates(tmp_path, monkeypatch, fault):
    path = tmp_path / 'target'
    before_fds = set(os.listdir('/proc/self/fd'))
    real_pread = os.pread
    calls = []
    with artifacts.pin_artifact_root(tmp_path) as root, artifacts.create_run_artifact(root, 'target') as target:
        assert target.write(b'intended bytes') == 14
        def broken(fd, size, offset):
            assert fd == target._fd and size == 14 and offset == 0
            value = real_pread(fd, size, offset)
            calls.append(value)
            if fault == 'io-error':
                raise OSError('injected after real target pread')
            return value[:-1]
        monkeypatch.setattr(artifacts.os, 'pread', broken)
        with pytest.raises(artifacts.ArtifactError):
            target.verify_owned()
        assert calls == [b'intended bytes']
        monkeypatch.setattr(artifacts.os, 'pread', real_pread)
        with pytest.raises(artifacts.ArtifactError):
            target.verify_owned()
        with pytest.raises(artifacts.ArtifactError):
            target.write(b'no new baseline')
    assert path.read_bytes() == b'intended bytes'
    assert set(os.listdir('/proc/self/fd')) == before_fds


@pytest.mark.parametrize('during_acquisition', [False, True])
def test_live6b_append_between_fd_and_named_observations(tmp_path, monkeypatch, during_acquisition):
    path = tmp_path / 'live'
    path.write_bytes(b'prefix')
    original = os.stat
    appended = []
    def changing(name, *args, **kwargs):
        if name == 'live' and not appended:
            with path.open('ab') as f:
                f.write(b'-append')
            appended.append(True)
        return original(name, *args, **kwargs)
    with artifacts.pin_artifact_root(tmp_path) as root:
        if during_acquisition:
            monkeypatch.setattr(os, 'stat', changing)
        with artifacts.open_live_artifact(root, 'live') as source:
            if not during_acquisition:
                monkeypatch.setattr(os, 'stat', changing)
            assert source.extent() == 13
            assert source.read_available(0, 65536) == b'prefix-append'
    assert appended == [True]


_LIVE_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
                'st_size', 'st_mtime_ns', 'st_ctime_ns')


def _stat_like(base, **changes):
    values = {name: getattr(base, name) for name in _LIVE_FIELDS}
    values.update(changes)
    return SimpleNamespace(**values)


def _attempt(*stats):
    """One scripted sample chain in observation order; the last stat is the latest."""
    return stats[-1], tuple(artifacts._file_facts(stat_) for stat_ in stats)


def _script(source, attempts):
    remaining = iter(attempts)
    source._live_sample = lambda: next(remaining)


def test_live_append_in_flight_is_resampled_then_accepted(tmp_path, monkeypatch):
    path = tmp_path / 'live'
    path.write_bytes(b'prefix')
    pauses = []
    monkeypatch.setattr(artifacts, '_resample_pause', lambda: pauses.append(True), raising=False)
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_live_artifact(root, 'live') as source:
            base = os.fstat(source._fd)
            # The writer stamps mtime/ctime before i_size moves: same size, newer times.
            stamped = _stat_like(base, st_mtime_ns=base.st_mtime_ns + 1, st_ctime_ns=base.st_ctime_ns + 1)
            grown = _stat_like(base, st_size=13, st_mtime_ns=base.st_mtime_ns + 2, st_ctime_ns=base.st_ctime_ns + 2)
            _script(source, [_attempt(stamped, stamped, stamped), _attempt(grown, grown, grown)])
            assert source.extent() == 13
            assert len(pauses) == 1 and not source._invalid
            assert source._observed == artifacts._file_facts(grown)


def test_same_size_rewrite_stays_invalid_after_bounded_resampling(tmp_path, monkeypatch):
    path = tmp_path / 'live'
    path.write_bytes(b'prefix')
    pauses = []
    monkeypatch.setattr(artifacts, '_resample_pause', lambda: pauses.append(True), raising=False)
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_live_artifact(root, 'live') as source:
            base = os.fstat(source._fd)
            attempts = [_attempt(*(_stat_like(base, st_mtime_ns=base.st_mtime_ns + n),) * 3)
                        for n in range(1, artifacts._LIVE_ATTEMPTS + 1)]
            _script(source, attempts)
            with pytest.raises(artifacts.ArtifactError, match='live-artifact-invalidated'):
                source.extent()
            assert len(pauses) == artifacts._LIVE_ATTEMPTS - 1 and source._invalid
            with pytest.raises(artifacts.ArtifactError, match='live-artifact-invalidated'):
                source.extent()  # sticky: no further sampling after invalidation
            assert len(pauses) == artifacts._LIVE_ATTEMPTS - 1


@pytest.mark.parametrize('what', ['truncation', 'inode'])
def test_truncation_and_identity_change_are_refused_without_resampling(tmp_path, monkeypatch, what):
    path = tmp_path / 'live'
    path.write_bytes(b'prefix')
    pauses = []
    monkeypatch.setattr(artifacts, '_resample_pause', lambda: pauses.append(True), raising=False)
    with artifacts.pin_artifact_root(tmp_path) as root:
        with artifacts.open_live_artifact(root, 'live') as source:
            base = os.fstat(source._fd)
            if what == 'truncation':
                bad = _stat_like(base, st_size=0, st_mtime_ns=base.st_mtime_ns + 1)
            else:
                bad = _stat_like(base, st_ino=base.st_ino + 1, st_size=base.st_size + 7)
            _script(source, [_attempt(bad, bad, bad)])
            with pytest.raises(artifacts.ArtifactError, match='live-artifact-invalidated'):
                source.extent()
            assert pauses == [] and source._invalid


def test_live_acquisition_resamples_stamp_then_append(tmp_path, monkeypatch):
    path = tmp_path / 'live'
    path.write_bytes(b'prefix')
    base_ns = path.stat().st_mtime_ns
    pauses = []
    monkeypatch.setattr(artifacts, '_resample_pause', lambda: pauses.append(True), raising=False)
    original = os.stat
    named_calls = []

    def writer(name, *args, **kwargs):
        if name == 'live':
            named_calls.append(True)
            if len(named_calls) == 1:  # writer stamps times before i_size moves
                os.utime(path, ns=(base_ns + 1000, base_ns + 1000))
            elif len(named_calls) == 2:  # the append lands during the resample
                with path.open('ab') as f:
                    f.write(b'-append')
        return original(name, *args, **kwargs)

    with artifacts.pin_artifact_root(tmp_path) as root:
        monkeypatch.setattr(os, 'stat', writer)
        with artifacts.open_live_artifact(root, 'live') as source:
            monkeypatch.setattr(os, 'stat', original)
            assert source.extent() == 13
            assert source.read_available(0, 65536) == b'prefix-append'
    assert len(pauses) == 1
