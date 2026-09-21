"""Adapter protocol plus the exclusion rules every store shares.

The one place that resolves HOME: `home()` honours $SCIEFLOW_CHATS_HOME so
tests can point the whole module at a `tmp_path` fake home. Store roots
themselves come from `config/chats.yml` and bound what may be read.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sqlite3
import subprocess
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from ..model import ChatRef, CopySpec

#: Never bundled, not overridable by any flag. Paths are relative to a store root.
CREDENTIAL_FILES: dict[str, tuple[str, ...]] = {
    "claude": (".credentials.json",),
    "codex": ("auth.json",),
    "agy": ("antigravity-oauth-token",),
    "gemini": ("oauth_creds.json", "google_accounts.json"),
}

#: Dropped by default: caches, logs and re-downloadable payloads.
DEFAULT_EXCLUDES: dict[str, tuple[str, ...]] = {
    "claude": ("debug/*", "statsig/*", "cache/*", "tmp/*", "daemon/*", "downloads/*"),
    "codex": ("packages/*", "logs_2.sqlite*", "cache/*", "vendor_imports/*"),
    "agy": (
        "bin/*",
        "log/*",
        "cache/*",
        "crashes/*",
        "updater/*",
        "presence/*",
        "brain/*",  # 122M of scratch space; --with-brain keeps it
    ),
    "gemini": ("config/*", "antigravity-cli/*"),
}


class StoreError(Exception):
    """A store could not be read or restored."""


def home() -> Path:
    """The HOME this module operates on ($SCIEFLOW_CHATS_HOME wins, for tests)."""
    override = os.environ.get("SCIEFLOW_CHATS_HOME")
    return Path(override) if override else Path.home()


def is_credential(tool: str, path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return path.name in {p for pats in CREDENTIAL_FILES.values() for p in pats}
    return rel in CREDENTIAL_FILES.get(tool, ())


def is_excluded(tool: str, path: Path, root: Path, extra: tuple[str, ...] = ()) -> bool:
    """True for credential files, default exclusions, and user `exclude_extra`."""
    if is_credential(tool, path, root):
        return True
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    patterns = DEFAULT_EXCLUDES.get(tool, ()) + tuple(extra)
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def ts(value: float | int | None) -> datetime | None:
    """Epoch seconds (or milliseconds) to an aware UTC datetime."""
    if not value:
        return None
    seconds = value / 1000 if value > 1e11 else value
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def snapshot_sqlite(source: Path) -> Path:
    """A consistent read-only copy of a live SQLite file.

    Codex and Antigravity keep `-wal`/`-shm` companions open while their CLI
    runs; `sqlite3.Connection.backup` reads a coherent snapshot without
    checkpointing or writing the original.
    """
    if not source.exists():
        raise StoreError(f"sqlite file not found: {source}")
    fd, tmp = tempfile.mkstemp(prefix="scieflow-chats-", suffix=".sqlite")
    os.close(fd)
    dest = Path(tmp)
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
    except sqlite3.Error as e:
        dest.unlink(missing_ok=True)
        raise StoreError(f"could not snapshot {source}: {e}") from e
    return dest


def running_tools(tools: tuple[str, ...] = ("claude", "codex", "agy")) -> list[str]:
    """Which target CLIs currently have a process, so we can refuse to write."""
    if shutil.which("pgrep") is None:
        return []
    live = []
    for tool in tools:
        try:
            done = subprocess.run(
                ["pgrep", "-x", tool], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and done.stdout.strip():
            live.append(tool)
    return live


class StoreAdapter(ABC):
    """One tool's chat store. Five calls, identical across all four."""

    tool: str = ""

    def __init__(
        self,
        root: Path,
        exclude_extra: tuple[str, ...] = (),
        options: dict | None = None,
    ):
        self.root = root
        self.exclude_extra = tuple(exclude_extra)
        self.options = dict(options or {})

    # -- read side -----------------------------------------------------
    @abstractmethod
    def discover(self) -> list[ChatRef]:
        """Every chat in this store, cheaply (no full transcript parsing)."""

    @abstractmethod
    def collect(self, refs: list[ChatRef]) -> list[CopySpec]:
        """Bundle members for the selected chats."""

    @abstractmethod
    def sidecars(self, refs: list[ChatRef]) -> list[CopySpec]:
        """Filtered settings/config/registry slices — never whole configs."""

    @abstractmethod
    def transcript_texts(self, ref: ChatRef):
        """Yield the raw text of a chat, for skill detection and secret scanning."""

    # -- write side ----------------------------------------------------
    @abstractmethod
    def plan_restore(self, bundle_dir: Path, dest_root: Path, mapping: dict[str, str]):
        """`FileChange`s describing what a restore would do. Writes nothing."""

    def skip(self, path: Path) -> bool:
        return is_excluded(self.tool, path, self.root, self.exclude_extra)


ADAPTERS: dict[str, type[StoreAdapter]] = {}


def register(cls: type[StoreAdapter]) -> type[StoreAdapter]:
    ADAPTERS[cls.tool] = cls
    return cls


def get_adapter(
    tool: str,
    root: Path,
    exclude_extra: tuple[str, ...] = (),
    options: dict | None = None,
) -> StoreAdapter:
    from . import antigravity, claude, codex, gemini  # noqa: F401  (registers)

    if tool not in ADAPTERS:
        raise StoreError(f"no adapter for store {tool!r}")
    return ADAPTERS[tool](root, exclude_extra, options)
