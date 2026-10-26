"""A run's data level (M-B Part A 5.1): workspace/<slug>/data.yml.

The file is the user's declaration (D9): ScieFlow cannot know what a file holds.
It is read fail-closed (M-B-D5): a file that does not parse or does not match the
schema makes the whole run L2. Writes go through modify(), under the run's lock,
and only the `scieflow data` commands call it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from scieflow.core import store

DATA_FILE = "data.yml"
ORDER = ("L0", "L1", "L1.5", "L2")
DECLARABLE = ("L0", "L1", "L2")
KINDS = ("tabular", "imaging", "eeg", "other")
DECLARED_BY = ("human", "agent")
_TOP_KEYS = ("version", "default_level", "levels", "pending_releases", "released", "imports")
_ENTRY_KEYS = ("path", "level", "kind", "reason", "declared_by", "declared_at")
_LIST_KEYS = ("pending_releases", "released", "imports")


class DataFileError(ValueError):
    """A data.yml problem. The message names a key or a path, never a value."""


@dataclass(frozen=True)
class Invalid:
    reason: str


@dataclass
class DataFile:
    present: bool
    default_level: str = "L0"
    levels: list = field(default_factory=list)
    pending_releases: list = field(default_factory=list)
    released: list = field(default_factory=list)
    imports: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": 1, "default_level": self.default_level, "levels": self.levels,
                "pending_releases": self.pending_releases, "released": self.released,
                "imports": self.imports}


@dataclass(frozen=True)
class LevelInfo:
    level: str
    reason: str | None = None


def rank(level: str) -> int:
    return ORDER.index(level)


def path_problem(value) -> str | None:
    """Why a run-relative glob is unacceptable, or None when it is fine."""
    if not isinstance(value, str) or not value.strip():
        return "is empty"
    if value.startswith(("/", "~")):
        return "must be relative to the run"
    if ".." in PurePosixPath(value).parts:
        return "must not contain '..'"
    return None


def _check_entry(entry, index: int) -> None:
    where = f"data.yml: levels[{index}]"
    if not isinstance(entry, dict):
        raise DataFileError(f"{where}: must be a mapping")
    for key in entry:
        if key not in _ENTRY_KEYS:
            raise DataFileError(f"{where}: unknown key {key!r}")
    problem = path_problem(entry.get("path"))
    if problem:
        raise DataFileError(f"{where}.path: {problem}")
    if entry.get("level") not in DECLARABLE:
        raise DataFileError(f"{where}.level: must be L0, L1 or L2")
    if entry.get("kind", "other") not in KINDS:
        raise DataFileError(f"{where}.kind: must be one of {', '.join(KINDS)}")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise DataFileError(f"{where}.reason: is required")
    if entry.get("declared_by") not in DECLARED_BY:
        raise DataFileError(f"{where}.declared_by: must be human or agent")


def _check(raw) -> DataFile:
    if not isinstance(raw, dict):
        raise DataFileError("data.yml: top level must be a mapping")
    for key in raw:
        if key not in _TOP_KEYS:
            raise DataFileError(f"data.yml: unknown key {key!r}")
    if isinstance(raw.get("version"), bool) or raw.get("version") != 1:
        raise DataFileError("data.yml: version must be 1")
    default = raw.get("default_level", "L0")
    if default not in DECLARABLE:
        raise DataFileError("data.yml: default_level must be L0, L1 or L2")
    entries = raw.get("levels", [])
    if not isinstance(entries, list):
        raise DataFileError("data.yml: levels must be a list")
    for index, entry in enumerate(entries):
        _check_entry(entry, index)
    for key in _LIST_KEYS:
        if not isinstance(raw.get(key, []), list):
            raise DataFileError(f"data.yml: {key} must be a list")
    return DataFile(present=True, default_level=default, levels=list(entries),
                    pending_releases=list(raw.get("pending_releases", [])),
                    released=list(raw.get("released", [])),
                    imports=list(raw.get("imports", [])))


def read(ws) -> DataFile | Invalid:
    """The declaration file, or Invalid(reason). A missing file is an empty DataFile."""
    path = Path(ws) / DATA_FILE
    if not path.exists():
        return DataFile(present=False)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return Invalid("data.yml: not readable YAML")
    if raw is None:
        return Invalid("data.yml: the file is empty")
    try:
        return _check(raw)
    except DataFileError as exc:
        return Invalid(str(exc))


def level_info(ws) -> LevelInfo:
    """The run's level: the maximum of default, declarations and L1.5 for imports."""
    got = read(ws)
    if isinstance(got, Invalid):
        return LevelInfo("L2", got.reason)
    candidates = [got.default_level, *(entry["level"] for entry in got.levels)]
    if got.imports:
        candidates.append("L1.5")
    return LevelInfo(max(candidates, key=rank))


def run_level(ws) -> str:
    return level_info(ws).level


def modify(ws, change: Callable[[DataFile], None]) -> DataFile:
    """Apply `change` to the declaration under the run's lock, then write it atomically.

    An invalid file is never overwritten: a human fixes it by hand.
    """
    path = Path(ws) / DATA_FILE
    with store.locked(path):
        got = read(ws)
        if isinstance(got, Invalid):
            raise DataFileError(f"{got.reason}; ScieFlow does not overwrite an invalid data.yml")
        change(got)
        store._atomic_write(path, yaml.safe_dump(got.to_dict(), sort_keys=False,
                                                 allow_unicode=True))
        return got


def run_dirs(root) -> list[Path]:
    """Every real directory under workspace/ (a symlinked entry is skipped)."""
    base = Path(root) / "workspace"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink())


def l2_runs(root, except_run: str | None = None) -> list[str]:
    return [ws.name for ws in run_dirs(root)
            if ws.name != except_run and run_level(ws) == "L2"]


def project_l2_paths(root, policy) -> list[Path]:
    """Existing project-level folders the policy declares L2 (outside any run)."""
    from scieflow.core.data.policy import project_path

    out = []
    for entry in policy.project_levels:
        if entry.level == "L2":
            path = project_path(root, entry.path)
            if path.exists():
                out.append(path)
    return out


def project_has_l2(root, policy, except_run: str | None = None) -> bool:
    return bool(l2_runs(root, except_run) or project_l2_paths(root, policy))
