"""Safe state files: locked, atomic writes and appends, plus run ids.

Every writer in core used plain `write_text`, so a browser request and an agent
in a terminal writing the same status.yml could interleave and truncate it.
These helpers take a sibling `<file>.lock` and replace the file atomically.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import yaml
from filelock import FileLock

LOCK_TIMEOUT = 30
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id() -> str:
    """A ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32.

    Sorts by creation time, so ids double as an ordering for events and jobs.
    """
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


@contextmanager
def locked(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=LOCK_TIMEOUT):
        yield


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    with locked(path):
        _atomic_write(path, text)


def write_yaml(path: Path, data) -> None:
    write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_yaml(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text())


def update_yaml(path: Path, fn: Callable[[dict], dict]) -> dict:
    """Read, change and write under one lock — no lost updates."""
    path = Path(path)
    with locked(path):
        current = yaml.safe_load(path.read_text()) if path.exists() else {}
        updated = fn(current or {})
        _atomic_write(path, yaml.safe_dump(updated, sort_keys=False, allow_unicode=True))
    return updated


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    with locked(path):
        with path.open("a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
