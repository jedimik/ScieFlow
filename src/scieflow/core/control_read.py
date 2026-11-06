"""Descriptor-anchored, bounded reads of run control files (T-12 t5, plan 5.6).

The safe-read primitives behind the workspace index and, later, the run health
page. No path is trusted between a check and a read: every component below the
resolved project root is opened relative to the previous descriptor with
O_NOFOLLOW; a control is opened non-blocking, verified to be a regular file by
its own descriptor, read at most CONTROL_CAP + 1 bytes, checked again for
change, and only then decoded and parsed.

A result carries a category and a fixed reason sentence, never file content,
so a secret-looking value in a control never reaches a page or an API reply.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml

CONTROL_CAP = 256 * 1024
STATUSES = ("ok", "missing", "symlink", "not-regular", "oversized", "unreadable",
            "malformed", "invalid-shape", "changed", "unsupported")

_REASONS = {
    "missing": "no such file",
    "symlink": "a symbolic link (not followed)",
    "not-regular": "not a regular file",
    "oversized": "larger than the control size limit (not read)",
    "unreadable": "permission or read error",
    "malformed": "not valid UTF-8 or YAML",
    "invalid-shape": "not a YAML mapping",
    "changed": "changed while it was read",
    "unsupported": "no descriptor-anchored reads on this platform",
}
_ERRNO_STATUS = {
    errno.ENOENT: "missing",
    errno.ELOOP: "symlink",
    errno.EACCES: "unreadable",
    errno.EPERM: "unreadable",
    errno.ENXIO: "not-regular",
    errno.ENOTDIR: "not-regular",
}


def _flag(name: str) -> int:
    return getattr(os, name, 0)


_DIR_FLAGS = os.O_RDONLY | _flag("O_DIRECTORY") | _flag("O_NOFOLLOW") | _flag("O_CLOEXEC")
_FILE_FLAGS = os.O_RDONLY | _flag("O_NOFOLLOW") | _flag("O_NONBLOCK") | _flag("O_CLOEXEC")


def _probe() -> bool:
    needed = ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC")
    return (all(hasattr(os, name) for name in needed)
            and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.readlink in os.supports_dir_fd and os.scandir in os.supports_fd)


_SUPPORTED = _probe()   # a platform property, decided once at import


def supported() -> bool:
    """True when this platform has the no-follow, non-blocking, dir_fd primitives."""
    return _SUPPORTED


@dataclass(frozen=True)
class ControlRead:
    status: str
    data: object = None
    reason: str = ""


def _fail(status: str) -> ControlRead:
    return ControlRead(status, None, _REASONS[status])


def status_for_error(exc: OSError) -> str:
    """The control status a failed open or stat stands for."""
    return _ERRNO_STATUS.get(exc.errno or 0, "unreadable")


def open_subdir(dir_fd: int, name: str) -> int:
    """A descriptor for the directory `name` under `dir_fd`; OSError for a symlink or non-directory."""
    if not supported():
        raise OSError(errno.ENOTSUP, "no descriptor-anchored open on this platform")
    return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)


def open_dir(root: Path, parts: tuple[str, ...]) -> int:
    """A descriptor for root/parts. The project root is resolved once (the caller's own,
    trusted location); every component below it is opened with O_NOFOLLOW."""
    if not supported():
        raise OSError(errno.ENOTSUP, "no descriptor-anchored open on this platform")
    real = Path(os.path.realpath(root))
    fd = os.open("/", _DIR_FLAGS)
    try:
        for name in (*real.parts[1:], *parts):
            nxt = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def read_yaml(dir_fd: int, name: str, cap: int = CONTROL_CAP) -> ControlRead:
    """One control file as a mapping, or a category explaining why not."""
    if not supported():
        return _fail("unsupported")
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        return _fail(status_for_error(exc))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return _fail("not-regular")
        if before.st_size > cap:
            return _fail("oversized")
        data = bytearray()
        while len(data) <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > cap:
            return _fail("oversized")
        if _identity(os.fstat(fd)) != _identity(before):
            return _fail("changed")
    except OSError:
        return _fail("unreadable")
    finally:
        os.close(fd)
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        return _fail("malformed")
    try:
        value = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, ValueError):
        # RecursionError: nesting deeper than the parser allows; ValueError: a value the
        # loader rejects (e.g. 2026-02-30). Both are a malformed control, never a 500.
        return _fail("malformed")
    if not isinstance(value, dict):
        return _fail("invalid-shape")
    return ControlRead("ok", value, "")


def stat_entry(dir_fd: int, name: str) -> str:
    """What `name` is, without following it: file, dir, symlink, other or missing."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "other"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "other"


def mtime_of(dir_fd: int, name: str) -> int | None:
    """Modification time (ns) of a regular file, read without following links; else None."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None
    return st.st_mtime_ns if stat.S_ISREG(st.st_mode) else None


def read_link(dir_fd: int, name: str) -> str | None:
    """A symlink's target text, or None when it cannot be read."""
    try:
        return os.readlink(name, dir_fd=dir_fd)
    except OSError:
        return None


def scan_dir(dir_fd: int, limit: int) -> tuple[list[str], bool]:
    """Up to `limit` entry names, streamed (no listing, no sort), and whether more remain."""
    names: list[str] = []
    with os.scandir(dir_fd) as entries:
        for entry in entries:
            if len(names) == limit:
                return names, True
            names.append(entry.name)
    return names, False
