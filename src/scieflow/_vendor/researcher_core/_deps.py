"""Stand-ins for the upstream modules the vendored researcher code imports but ScieFlow does not vendor.

ScieFlow-authored, not pinned (M-C2-D1). verify.py and monitor.py import the upstream connector
stack, the snapshot store, the status axis and the provenance ledger; the import patches recorded in
UPSTREAM.json point those imports here. Every stand-in refuses with NotImplementedError and names
the ScieFlow replacement. Nothing here reaches the network or a file. It imports only the standard
library and this package's excerpts (errors, provenance_ts).
"""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar

from .errors import ConnectorError, SourceError, SourceErrorKind, UnsupportedOperation
from .provenance_ts import ProvenanceError, normalize_ts

__all__ = [
    "BaseConnector",
    "ConnectorError",
    "PARSER_VERSION",
    "PROTOCOL_VERSION",
    "ProvenanceError",
    "ProvenanceEvent",
    "RunContext",
    "SnapshotSession",
    "SourceError",
    "SourceErrorKind",
    "StatusEntry",
    "UnsupportedOperation",
    "__version__",
    "check_status_async",
    "create_connector",
    "normalize_ts",
]

__version__ = "1.0.0"
PARSER_VERSION = "1"
PROTOCOL_VERSION = "1"

_CONNECTORS = "ScieFlow's identity adapters (src/scieflow/research/lib/identity.py, M-C2 t4) replace the upstream connectors"
_SNAPSHOTS = "ScieFlow's snapshot store (src/scieflow/research/lib/snapshots.py) replaces the upstream snapshot store"
_STATUS = "ScieFlow's scite evidence bundle (src/scieflow/core/review/evidence.py) replaces the upstream status axis"
_PROVENANCE = "ScieFlow's run ledger (src/scieflow/integrations/scite/ledger.py) replaces the upstream provenance ledger"


class BaseConnector(ABC):
    """Interface only: the operations verify.py may call. Every operation is unsupported by default."""

    name: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def supports(cls, operation: str) -> bool:
        return operation in cls.capabilities

    async def search(self, query: str, *, limit: int = 25, since: str | None = None) -> Any:
        raise UnsupportedOperation(self.name, "search")

    async def get_by_id(self, identifier: str) -> Any:
        raise UnsupportedOperation(self.name, "get_by_id")

    async def resolve_doi(self, doi: str) -> Any:
        raise UnsupportedOperation(self.name, "resolve_doi")

    async def get_oa_pdf(self, doi: str) -> Any:
        raise UnsupportedOperation(self.name, "get_oa_pdf")

    async def aclose(self) -> None:
        return None


def create_connector(name: str, **kwargs: Any) -> BaseConnector:
    raise NotImplementedError(f"create_connector({name!r}) is not available in ScieFlow. {_CONNECTORS}.")


class SnapshotSession:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(f"SnapshotSession is not available in ScieFlow. {_SNAPSHOTS}.")


class StatusEntry:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(f"StatusEntry is not available in ScieFlow. {_STATUS}.")


async def check_status_async(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(f"check_status_async is not available in ScieFlow. {_STATUS}.")


class ProvenanceEvent:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(f"ProvenanceEvent is not available in ScieFlow. {_PROVENANCE}.")


class RunContext:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(f"RunContext is not available in ScieFlow. {_PROVENANCE}.")
