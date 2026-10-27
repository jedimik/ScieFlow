"""Snapshot store of the literature kernel (M-C1 Part A 5.5).

A snapshot is one recorded answer to one provider request, kept inside its run:

    workspace/<run>/snapshots/literature/<provider>/<key>.json

<key> is the Gateway's cache key (64 lowercase hex). Only the Gateway writes here, in record mode;
replay and offline read it. Every read checks the file's shape, key, request and body hash, and a
symbolic link anywhere on the path is refused. Messages name the relative path, never the body.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scieflow._vendor.researcher_core.model import canonical_json, sha256_hex
from scieflow.research.lib import providers

SCHEMA_VERSION = 1
BODY_KINDS = ("json", "text")
KEYS = frozenset({"schema_version", "source", "endpoint", "url", "request_params", "request_key",
                  "body_kind", "response_body", "response_hash", "results", "retrieved_at"})
_KEY = re.compile(r"[0-9a-f]{64}")


class SnapshotCorrupt(providers.ProviderRefused):
    """A snapshot that fails its checks, or a path through a symbolic link (exit 3)."""


class SnapshotWriteError(ValueError):
    """A snapshot the Gateway could not write. The search ran; the command fails (exit 1)."""


def path(ws, provider: str, key: str) -> Path:
    """The file of one snapshot. The provider must be a catalogued one; the key 64 hex digits."""
    if provider not in providers.CATALOGUE:
        raise ValueError(f"unknown provider {provider!r}")
    if not _KEY.fullmatch(key):
        raise ValueError("snapshot key must be 64 lowercase hex digits")
    return Path(ws) / "snapshots" / "literature" / provider / f"{key}.json"


def _rel(ws: Path, target: Path) -> str:
    return target.relative_to(Path(ws)).as_posix()


def _no_links(ws, target: Path) -> None:
    ws = Path(ws)
    cur = ws
    for part in target.relative_to(ws).parts:
        cur = cur / part
        if cur.is_symlink():
            raise SnapshotCorrupt(f"{_rel(ws, cur)} is a symbolic link; snapshots are never "
                                  "read or written through one")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_failed(ws, provider: str, key: str, exc: OSError) -> SnapshotWriteError:
    """The refusal for a snapshot the disk would not take: names the relative path only."""
    rel = _rel(Path(ws), path(ws, provider, key))
    return SnapshotWriteError(f"{rel} could not be written ({exc.strerror or 'OS error'})")


def ensure_writable(ws, *, provider: str, key: str) -> None:
    """Before a record-mode request is sent: the snapshot's place is plain and can be created.
    A symbolic link or an unwritable folder refuses here, so nothing is sent for it."""
    target = path(ws, provider, key)
    _no_links(ws, target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise write_failed(ws, provider, key, exc) from None


def write(ws, *, provider: str, op: str, key: str, url: str, request_params: dict,
          body_kind: str, response_body, results: int) -> dict:
    """Store one answer atomically (temp file, then rename). Returns the record written."""
    if body_kind not in BODY_KINDS or (body_kind == "text") != isinstance(response_body, str):
        raise ValueError("body_kind must match the body: json, or text for a string")
    target = path(ws, provider, key)
    _no_links(ws, target)
    record = {
        "schema_version": SCHEMA_VERSION, "source": provider, "endpoint": op, "url": url,
        "request_params": request_params, "request_key": key, "body_kind": body_kind,
        "response_body": response_body, "response_hash": sha256_hex(canonical_json(response_body)),
        "results": results, "retrieved_at": _now(),
    }
    text = json.dumps(record, ensure_ascii=False, indent=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return record


def load(ws, *, provider: str, op: str, key: str, url: str, request_params: dict) -> dict | None:
    """The recorded answer for this request, checked; None when there is none."""
    target = path(ws, provider, key)
    _no_links(ws, target)
    if not target.exists():
        return None
    rel = _rel(ws, target)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise SnapshotCorrupt(f"{rel} is not readable JSON") from None
    if not isinstance(data, dict) or set(data) != KEYS:
        raise SnapshotCorrupt(f"{rel} does not have the snapshot keys")
    try:
        digest = sha256_hex(canonical_json(data["response_body"]))
    except (TypeError, ValueError):
        raise SnapshotCorrupt(f"{rel}: its body cannot be hashed") from None
    checks = (
        (data["schema_version"] == SCHEMA_VERSION, "unknown schema version"),
        (data["request_key"] == key and target.name == f"{key}.json",
         "its key does not match its name"),
        (data["source"] == provider and data["endpoint"] == op,
         "its provider or operation differs from the request"),
        (data["url"] == url and data["request_params"] == request_params,
         "its request differs from the one asked for"),
        (data["body_kind"] in BODY_KINDS
         and (data["body_kind"] == "text") == isinstance(data["response_body"], str),
         "its body kind does not match its body"),
        (data["response_hash"] == digest, "its body does not match its hash"),
        (isinstance(data["results"], int) and not isinstance(data["results"], bool)
         and data["results"] >= 0, "its result count is not a non-negative integer"),
        (isinstance(data["retrieved_at"], str), "its retrieval time is not a string"),
    )
    for ok, why in checks:
        if not ok:
            raise SnapshotCorrupt(f"{rel} is corrupt: {why}")
    return data
