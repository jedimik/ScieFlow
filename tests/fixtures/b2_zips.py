"""T-9 t2 synthetic zip fixtures: members written with zipfile, nothing from a real run or remote.

Every archive is built under a temporary project. No real home, no real workspace, no network.
"""

from __future__ import annotations

import os
import stat
import struct
import zipfile
from pathlib import Path

from scieflow.core import archives as a
from tests.fixtures.b2_archives import zip_path

STAMP = (2026, 11, 7, 0, 0, 0)


def write_zip(path: Path, entries, *, compression: int = zipfile.ZIP_STORED) -> Path:
    """entries: (name, bytes) or (ZipInfo, bytes). Names are written exactly as given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression, allowZip64=True) as zf:
        for head, data in entries:
            if isinstance(head, zipfile.ZipInfo):
                zf.writestr(head, data)
            else:
                zf.writestr(head, data, compress_type=compression)
    return path


def put_zip(project, slug: str, entries, **kwargs) -> Path:
    return write_zip(zip_path(project, slug), entries, **kwargs)


def info(name: str, *, compress_type: int = zipfile.ZIP_STORED, date_time=STAMP) -> zipfile.ZipInfo:
    out = zipfile.ZipInfo(name, date_time=date_time)
    out.compress_type = compress_type
    return out


def symlink_info(name: str) -> zipfile.ZipInfo:
    out = info(name)
    out.external_attr = (stat.S_IFLNK | 0o777) << 16
    return out


def zip_tree(src: Path, dest: Path, *, compression: int = zipfile.ZIP_STORED) -> Path:
    """Pack a directory the way build_zip does: directories as `name/`, names relative to src."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames.sort()
        base = Path(dirpath)
        for d in dirnames:
            entries.append((info(base.joinpath(d).relative_to(src).as_posix() + "/"), b""))
        for f in sorted(filenames):
            full = base / f
            entries.append((info(full.relative_to(src).as_posix(), compress_type=compression),
                            full.read_bytes()))
    return write_zip(dest, entries, compression=compression)


def make_tree(src: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        target = src / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return src


def set_flag_bit(path: Path, member: str, bit: int = 0x1) -> None:
    """Set a flag bit (0x1 = encrypted) on one member's local and central headers, in place.

    zipfile cannot write encrypted members; the synthetic archive is patched after writing.
    The member's size and data are unchanged, so the archive is otherwise valid.
    """
    data = bytearray(path.read_bytes())
    pos = data.find(b"PK\x01\x02")
    while pos >= 0:
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        name = bytes(data[pos + 46: pos + 46 + name_len]).decode("utf-8")
        if name == member:
            flags = struct.unpack_from("<H", data, pos + 8)[0] | bit
            struct.pack_into("<H", data, pos + 8, flags)
            local = struct.unpack_from("<I", data, pos + 42)[0]
            lflags = struct.unpack_from("<H", data, local + 6)[0] | bit
            struct.pack_into("<H", data, local + 6, lflags)
            break
        pos = data.find(b"PK\x01\x02", pos + 46 + name_len + extra_len + comment_len)
    else:
        raise AssertionError(f"no central header for {member!r}")
    path.write_bytes(bytes(data))


def reset_index() -> None:
    """Empty the process-wide zip index cache (tests only)."""
    a._INDEX_LRU.clear()
