"""Pinned, descriptor-owned regular artifacts; no unchecked consumer paths.

The root is trusted host input, not authorization of a client-supplied path.
This boundary detects identity/content metadata drift, not privileged-host
atomicity or cryptographic immutability. Directory entry churn is allowed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import sys

_MAX_READ = 65536
_TOKEN = object()


class ArtifactError(ValueError):
    """Acquisition/consumption failed safely; messages contain no raw data."""


class _WriteFailure(ArtifactError):
    """A real write returned a count before post-write ownership failed."""
    def __init__(self, count):
        super().__init__('target-post-write-invalidated')
        self.bytes_written = count


def _identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _file_facts(info):
    return (*_identity(info), info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _relative(value):
    if not isinstance(value, (str, Path)):
        raise ArtifactError('invalid-relative-path')
    raw = str(value)
    parts = raw.split('/')
    if not raw or '\0' in raw or any(part in ('', '.', '..') for part in parts):
        raise ArtifactError('invalid-relative-path')
    return parts


def _supported():
    if (sys.platform != 'linux' or not all(hasattr(os, name) for name in
            ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK', 'O_CLOEXEC', 'pread')) or
            os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd or
            os.stat not in os.supports_follow_symlinks):
        raise ArtifactError('safe-descriptor-proof-unavailable')


class _Directories:
    def __init__(self):
        self._entries = []

    def open(self, name, parent=None, expected=None):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        try:
            info = os.fstat(fd)
            identity = _identity(info)
            if not stat.S_ISDIR(info.st_mode) or (expected is not None and identity != expected):
                raise ArtifactError('directory-identity-changed')
            entry = (fd, parent, name, identity)
            self._entries.append(entry)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def verify(self):
        for fd, parent, name, identity in self._entries:
            if (_identity(os.fstat(fd)) != identity or
                    _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity):
                raise ArtifactError('directory-identity-changed')

    def close(self):
        entries, self._entries = self._entries, []
        failure = None
        for fd, _, _, _ in reversed(entries):
            try:
                os.close(fd)
            except OSError as exc:
                failure = failure or exc
        if failure is not None:
            raise ArtifactError('descriptor-cleanup-failed') from failure


class ArtifactRoot:
    """One active context's pinned absolute directory chain."""
    def __init__(self, token, path, directories, fd):
        if token is not _TOKEN:
            raise ArtifactError('root-not-owned')
        self._token = token
        self._path, self._directories, self._fd = path, directories, fd
        self._identity = _identity(os.fstat(fd))
        self._active = True

    @property
    def path(self):
        return self._path

    @property
    def identity(self):
        return self._identity

    def _verify(self):
        if not self._active:
            raise ArtifactError('root-closed')
        try:
            self._directories.verify()
        except OSError as exc:
            raise ArtifactError('root-check-failed') from exc

    def _close(self):
        if self._active:
            self._active = False
            self._directories.close()


@contextmanager
def pin_artifact_root(canonical_path: Path, *, expected_identity=None):
    _supported()
    if not isinstance(canonical_path, (Path, str)):
        raise ArtifactError('invalid-canonical-root')
    raw = str(canonical_path)
    path = Path(raw)
    if path.anchor != '/' or str(path) != raw or '..' in path.parts or '\0' in raw:
        raise ArtifactError('invalid-canonical-root')
    if expected_identity is not None and (type(expected_identity) is not tuple or
            len(expected_identity) != 3 or any(type(i) is not int for i in expected_identity)):
        raise ArtifactError('invalid-expected-identity')
    directories = _Directories()
    root = None
    try:
        fd = directories.open('/')
        for part in path.parts[1:]:
            fd = directories.open(part, fd)
        root = ArtifactRoot(_TOKEN, path, directories, fd)
        root._verify()
        if expected_identity is not None and root.identity != expected_identity:
            raise ArtifactError('root-identity-changed')
    except OSError as exc:
        directories.close()
        raise ArtifactError('root-acquisition-failed') from exc
    except BaseException:
        directories.close()
        raise
    try:
        yield root
    finally:
        root._close()


def _root(value):
    if type(value) is not ArtifactRoot or getattr(value, '_token', None) is not _TOKEN:
        raise ArtifactError('root-not-owned')
    value._verify()
    return value


class RegularArtifact:
    """Finite original extent, position-independent bounded reads."""
    def __init__(self, token, root, directories, parent, name, fd, info, aliases=()):
        if token is not _TOKEN:
            raise ArtifactError('artifact-not-owned')
        self._root, self._directories = root, directories
        self._parent, self._name, self._fd = parent, name, fd
        self._facts, self._size = _file_facts(info), info.st_size
        self._aliases = aliases
        self._active = True

    @property
    def snapshot_size(self):
        return self._size

    def verify_unchanged(self):
        if not self._active:
            raise ArtifactError('artifact-closed')
        try:
            self._root._verify()
            self._directories.verify()
            _verify_aliases(self._aliases)
            if (_file_facts(os.fstat(self._fd)) != self._facts or
                    _file_facts(os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)) != self._facts):
                raise ArtifactError('artifact-changed')
        except OSError as exc:
            raise ArtifactError('artifact-check-failed') from exc

    def read_at(self, offset: int, size: int) -> bytes:
        if type(offset) is not int or type(size) is not int or offset < 0 or not 0 <= size <= _MAX_READ:
            raise ValueError('invalid bounded read')
        self.verify_unchanged()
        try:
            extent = min(size, max(0, self._size - offset))
            data = os.pread(self._fd, extent, offset) if extent else b''
        except OSError as exc:
            raise ArtifactError('artifact-read-failed') from exc
        self.verify_unchanged()
        return data

    def _close(self):
        if self._active:
            self._active = False
            try:
                os.close(self._fd)
            finally:
                self._directories.close()


class ExclusiveArtifact:
    """Original exclusive target; never truncates/removes an uncertain path."""
    def __init__(self, token, root, directories, parent, name, fd, info):
        if token is not _TOKEN:
            raise ArtifactError('artifact-not-owned')
        self._root, self._directories = root, directories
        self._parent, self._name, self._fd = parent, name, fd
        self._identity = _identity(info)
        self._facts = _file_facts(info)
        self._written, self._copy_started = 0, False
        self._expected_digest = hashlib.sha256()
        self._invalid = False
        self._active = True

    def _verify_binding(self):
        """Small ownership/facts check, not proof of intended content."""
        if not self._active:
            raise ArtifactError('artifact-closed')
        if self._invalid:
            raise ArtifactError('target-invalidated')
        try:
            self._root._verify()
            self._directories.verify()
            info = os.fstat(self._fd)
            named = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
            if (info.st_size != self._written or _identity(info) != self._identity or
                    _file_facts(info) != self._facts or _file_facts(named) != self._facts):
                raise ArtifactError('target-identity-changed')
        except ArtifactError:
            self._invalid = True
            raise
        except OSError as exc:
            self._invalid = True
            raise ArtifactError('target-check-failed') from exc

    def verify_owned(self):
        """Verify the finite intended byte stream on the SAME owned descriptor."""
        self._verify_binding()
        observed, offset = hashlib.sha256(), 0
        try:
            while offset < self._written:
                self._verify_binding()
                requested = min(_MAX_READ, self._written - offset)
                data = os.pread(self._fd, requested, offset)
                self._verify_binding()
                if len(data) != requested:
                    raise ArtifactError('target-short-read')
                observed.update(data)
                offset += len(data)
            self._verify_binding()
            if observed.digest() != self._expected_digest.digest():
                raise ArtifactError('target-content-changed')
        except ArtifactError:
            self._invalid = True
            raise
        except OSError as exc:
            self._invalid = True
            raise ArtifactError('target-read-failed') from exc

    def _begin_copy(self):
        self.verify_owned()
        if self._written or self._copy_started:
            return False
        self._copy_started = True
        return True

    def write(self, data: bytes) -> int:
        if type(data) is not bytes or len(data) > _MAX_READ:
            raise ValueError('invalid bounded write')
        self._verify_binding()
        try:
            count = os.write(self._fd, data)
        except OSError as exc:
            self._invalid = True
            raise ArtifactError('target-write-failed') from exc
        # Expectation comes ONLY from caller bytes actually written, never from
        # a readback/current target extent. Preserve count if later proof fails.
        self._expected_digest.update(data[:count])
        self._written += count
        try:
            info = os.fstat(self._fd)
            facts = _file_facts(info)
            # Owned-write metadata bookkeeping is NOT content integrity proof.
            # The independent intended-byte digest survives this transition.
            if facts[:7] != self._facts[:7] or info.st_size != self._written:
                raise ArtifactError('target-write-invalidated')
            self._facts = facts
            self._verify_binding()
        except (ArtifactError, OSError) as exc:
            self._invalid = True
            raise _WriteFailure(count) from exc
        return count

    def _close(self):
        if self._active:
            self._active = False
            try:
                os.close(self._fd)
            finally:
                self._directories.close()


def _verify_aliases(aliases):
    for parent, name, facts, target in aliases:
        if (_file_facts(os.stat(name, dir_fd=parent, follow_symlinks=False)) != facts or
                os.readlink(name, dir_fd=parent) != target):
            raise ArtifactError('alias-identity-changed')


def _contained_route(root, parts, directories):
    """Resolve captured contained aliases without reading/reopening the leaf."""
    pending, canonical, aliases = list(parts), [], []
    stack = [root._fd]
    while pending:
        root._verify()
        directories.verify()
        _verify_aliases(aliases)
        name = pending.pop(0)
        if name in ('', '.'):
            continue
        if name == '..':
            if not canonical:
                raise ArtifactError('alias-outside-root')
            canonical.pop()
            stack.pop()
            continue
        parent = stack[-1]
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            if len(aliases) >= 40:
                raise ArtifactError('alias-chain-limit')
            target = os.readlink(name, dir_fd=parent)
            aliases.append((parent, name, _file_facts(info), target))
            if target.startswith('/'):
                root_parts = list(root.path.parts[1:])
                target_parts = target.split('/')[1:]
                if target_parts[:len(root_parts)] != root_parts:
                    raise ArtifactError('alias-outside-root')
                combined = target_parts[len(root_parts):]
            else:
                combined = canonical + target.split('/')
            # Resolve each directory/symlink BEFORE interpreting later '..'.
            # Lexical collapse would select a different contained file.
            pending = combined + pending
            if not pending or len(pending) > 4096:
                raise ArtifactError('invalid-alias-route')
            stack, canonical = [root._fd], []
        elif pending:
            stack.append(directories.open(name, parent, expected=_identity(info)))
            canonical.append(name)
        else:
            _verify_aliases(aliases)
            return parent, name, tuple(aliases), _file_facts(info)
    raise ArtifactError('invalid-alias-route')


@contextmanager
def _acquire(root, relative_path, *, create=False, allow_contained_aliases=False):
    root = _root(root)
    if type(allow_contained_aliases) is not bool:
        raise ValueError('invalid alias selection')
    parts = _relative(relative_path)
    directories, fd, handle = _Directories(), None, None
    try:
        aliases, expected = (), None
        if allow_contained_aliases:
            parent, name, aliases, expected = _contained_route(root, parts, directories)
        else:
            parent = root._fd
            for part in parts[:-1]:
                parent = directories.open(part, parent)
            name = parts[-1]
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        flags |= (os.O_RDWR | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
        fd = os.open(name, flags, 0o600, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArtifactError('unsafe-artifact-type-or-links')
        if create and info.st_size != 0:
            raise ArtifactError('target-not-empty')
        if expected is not None and _file_facts(info) != expected:
            raise ArtifactError('artifact-identity-changed')
        root._verify()
        directories.verify()
        _verify_aliases(aliases)
        if _file_facts(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _file_facts(info):
            raise ArtifactError('artifact-identity-changed')
        if create:
            handle = ExclusiveArtifact(_TOKEN, root, directories, parent, name, fd, info)
        else:
            handle = RegularArtifact(_TOKEN, root, directories, parent, name, fd, info, aliases)
        fd = None
    except OSError as exc:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            directories.close()
        raise ArtifactError('artifact-acquisition-failed') from exc
    except BaseException:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            directories.close()
        raise
    try:
        yield handle
    finally:
        handle._close()


def open_run_artifact(root_identity: ArtifactRoot, relative_path: str | Path, *,
                      allow_contained_aliases: bool = False):
    return _acquire(root_identity, relative_path, allow_contained_aliases=allow_contained_aliases)


def create_run_artifact(root_identity: ArtifactRoot, relative_path: str | Path):
    return _acquire(root_identity, relative_path, create=True)
