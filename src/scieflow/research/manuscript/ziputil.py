"""Deterministic zip writer for the submission package (M-F 5.13).

Entries are written in sorted order with a fixed date (1980-01-01), a fixed unix mode (0644), a fixed
creator system and deflate compression, so the same names and bytes always give the same archive.
"""

from __future__ import annotations

import io
import zipfile

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644
UNIX_CREATOR = 3


def _safe(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and all(p not in ("", ".", "..") for p in parts)


def zip_bytes(entries: dict) -> bytes:
    """The archive of `entries` (archive name, posix relative path -> bytes). Output depends on content only."""
    for name in entries:
        if not _safe(name):
            raise ValueError(f"unsafe archive name {name!r}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = UNIX_CREATOR
            info.external_attr = FILE_MODE << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue()
