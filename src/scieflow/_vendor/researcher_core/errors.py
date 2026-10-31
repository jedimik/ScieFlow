"""Connector error types: a verbatim excerpt of researcher v1.0.0 connectors/base.py,
lines 206-271 (MIT; see VENDORED.md). Nothing else from base.py is vendored here."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ConnectorError(Exception):
    """Base class for every connector-layer failure."""


class SourceErrorKind(str, Enum):
    """Why a source failed to give a clean answer."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    NETWORK = "network"
    BAD_RESPONSE = "bad_response"
    CONFIG = "config"


class SourceError(ConnectorError):
    """A source did not give a clean answer.

    Timeout, rate limit, 5xx, network failure, unparseable payload, or missing required
    configuration. This is emphatically NOT "no match found": a clean negative is an empty
    list or ``None``. Consumers translate a :class:`SourceError` into the ``source_error``
    per-source outcome, which per D9 forces an ``inconclusive`` reference-level verdict and
    can never produce a refusal-grade ``unresolvable``.
    """

    def __init__(
        self,
        source: str,
        message: str,
        *,
        kind: SourceErrorKind = SourceErrorKind.NETWORK,
        status_code: int | None = None,
        endpoint: str = "",
    ) -> None:
        self.source = source
        self.kind = kind
        self.status_code = status_code
        self.endpoint = endpoint
        self.message = message
        detail = f"[{source}:{kind.value}]"
        if status_code is not None:
            detail += f" HTTP {status_code}"
        super().__init__(f"{detail} {message}")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind.value,
            "status_code": self.status_code,
            "endpoint": self.endpoint,
            "message": self.message,
        }


class UnsupportedOperation(ConnectorError):
    """The connector does not implement this operation at all (arXiv has no citation graph).

    Distinct from both a clean negative and a source error: nothing was asked of the API.
    Callers should skip the source for that operation rather than record an outcome. Use
    :meth:`BaseConnector.supports` to check before calling.
    """

    def __init__(self, source: str, operation: str) -> None:
        self.source = source
        self.operation = operation
        super().__init__(f"Connector {source!r} does not support {operation!r}.")
