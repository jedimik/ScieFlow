"""Shared dataclasses: one shape for every store, so one checklist, one
manifest and one remap pass cover all four tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TOOLS: tuple[str, ...] = ("claude", "codex", "agy", "gemini")


@dataclass(frozen=True)
class ChatRef:
    """One conversation, whichever tool it came from."""

    tool: str
    chat_id: str
    project_path: str | None
    title: str
    started: datetime | None
    updated: datetime | None
    size_bytes: int
    message_count: int
    files: tuple[Path, ...] = ()
    extras: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identifier used in plan files and manifests."""
        return f"{self.tool}:{self.chat_id}"

    @property
    def project_name(self) -> str:
        if not self.project_path:
            return "(no project)"
        return Path(self.project_path).name or self.project_path


@dataclass(frozen=True)
class CopySpec:
    """A file to place in the bundle.

    Exactly one of `source` (copy from disk) or `data` (bytes produced in
    memory, e.g. rows extracted from SQLite) is set.
    """

    member: str
    source: Path | None = None
    data: bytes | None = None

    def __post_init__(self) -> None:
        if (self.source is None) == (self.data is None):
            raise ValueError(f"{self.member}: set exactly one of source/data")


@dataclass
class Artifact:
    """A skill, plugin or MCP server a selected chat referenced."""

    kind: str  # skill | plugin | mcp
    name: str
    source: Path | None = None
    version: str | None = None
    marketplace: str | None = None
    used_by: list[str] = field(default_factory=list)
    confidence: str = "high"  # high | low (protobuf byte-scan hits)


@dataclass
class Selection:
    """What the user picked, and what it implies."""

    chats: list[ChatRef] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        return sum(c.size_bytes for c in self.chats)
