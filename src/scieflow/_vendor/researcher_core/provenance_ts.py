"""Provenance normalisers: a verbatim excerpt of researcher v1.0.0 provenance.py,
lines 118-224 (MIT; see VENDORED.md). The ledger itself is not vendored."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?([-+.][0-9A-Za-z.-]+)?$")
_HASH_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProvenanceError(RuntimeError):
    """A provenance event or ledger operation was rejected."""


class DuplicateEventError(ProvenanceError):
    """An event_id already present in the ledger was appended again.

    Append-only means an existing event is never overwritten, so this is an error rather
    than an update. The default ``event_id`` is a content hash of the event body, so the
    common way to hit this is genuinely appending the same event twice.
    """

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(
            f"Event {event_id!r} is already in the ledger. The ledger is append-only: "
            "existing events are never updated or replaced. Supply a distinct event_id "
            "if this is a genuinely different event."
        )


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def normalize_version(value: Any, *, label: str = "version") -> str:
    """Coerce a version to the schema's ``versionString`` form (``MAJOR.MINOR[.PATCH]``).

    The package pins ``PARSER_VERSION = "1"`` and ``PROTOCOL_VERSION = "1"``, which are
    one-component strings; the schema requires at least two components. A bare integer is
    therefore widened (``"1"`` -> ``"1.0"``) rather than written out invalid. Anything
    still not matching the pattern raises, because an event that cannot validate must not
    reach the store.
    """
    text = str(value).strip()
    if not text:
        raise ProvenanceError(f"{label} must be a non-empty version string.")
    if text.isdigit():
        text = f"{text}.0"
    if not _VERSION_RE.match(text):
        raise ProvenanceError(
            f"{label} {value!r} is not a valid version string "
            "(expected MAJOR.MINOR[.PATCH], e.g. '1.0' or '0.1.0')."
        )
    return text


def normalize_hash(value: Any, *, label: str = "source_response_hash") -> str:
    """Validate a SHA-256 content hash, hex encoded, optionally ``sha256:``-prefixed.

    The value is returned unchanged (prefix and all): the ledger stores what the caller
    supplied, and :mod:`researcher_core.lineage` strips the prefix when it joins.
    """
    text = str(value).strip()
    if not _HASH_RE.match(text):
        raise ProvenanceError(
            f"{label} {value!r} is not a SHA-256 content hash "
            "(64 lowercase hex characters, optionally prefixed with 'sha256:')."
        )
    return text


def normalize_ts(value: str | datetime) -> str:
    """Validate a CALLER-SUPPLIED RFC 3339 timestamp and return it verbatim.

    This function never reads the clock, and neither does anything else in this module.
    That is the whole point: a self-generated timestamp would make two replays of the same
    run differ, and D15 forbids that. A :class:`datetime` is accepted for convenience but
    must be timezone-aware.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ProvenanceError(
                "ts must be timezone-aware; a naive datetime has no defined instant."
            )
        text = value.isoformat()
        return text[:-6] + "Z" if text.endswith("+00:00") else text

    text = str(value).strip()
    if not text:
        raise ProvenanceError(
            "ts is required and is caller-supplied; the ledger never generates it "
            "(a self-generated timestamp would break replay determinism, D15)."
        )
    probe = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(probe)
    except ValueError as exc:
        raise ProvenanceError(
            f"ts {value!r} is not an RFC 3339 timestamp (e.g. '2026-07-14T12:00:00Z')."
        ) from exc
    if parsed.tzinfo is None:
        raise ProvenanceError(
            f"ts {value!r} carries no UTC offset; RFC 3339 requires one "
            "(e.g. '2026-07-14T12:00:00Z')."
        )
    return text
