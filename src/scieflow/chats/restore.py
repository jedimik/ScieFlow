"""Restoring a bundle onto this machine.

Everything is planned first and written second: `plan()` returns the full list
of `FileChange`s without touching disk, and `apply()` is the only function
here that writes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from .config import ChatsConfig
from .model import TOOLS
from .remap import FileChange, build_mapping
from .stores import get_adapter
from .stores.base import home

#: Metadata files snapshotted to `.bak` before a merge or an upsert.
SNAPSHOT_ACTIONS = {"merge", "sqlite-upsert"}


class RestoreError(Exception):
    """A restore could not be planned or applied."""


def default_mapping(manifest: dict, overrides: list[tuple[str, str]]) -> dict[str, str]:
    """Source home -> this home, plus whatever the user passed with --map."""
    pairs: list[tuple[str, str]] = []
    source_home = (manifest.get("source") or {}).get("home")
    if source_home:
        pairs.append((str(source_home), str(home())))
    pairs.extend(overrides)
    return build_mapping(pairs)


def parse_map(value: str) -> tuple[str, str]:
    old, sep, new = value.partition("=")
    if not sep or not old or not new:
        raise ValueError(f"--map expects OLD=NEW, got {value!r}")
    return old, new


def plan(
    bundle_dir: Path,
    manifest: dict,
    config: ChatsConfig,
    mapping: dict[str, str],
    tools: tuple[str, ...] = (),
) -> list[FileChange]:
    wanted = tuple(tools) or tuple(manifest.get("tools") or TOOLS)
    changes: list[FileChange] = []
    for tool in wanted:
        store = config.stores.get(tool)
        if store is None:
            continue
        adapter = get_adapter(tool, store.root, tuple(config.exclude_extra))
        changes.extend(adapter.plan_restore(bundle_dir, store.root, mapping))
    changes.extend(_plan_skills(bundle_dir, config))
    return changes


def _plan_skills(bundle_dir: Path, config: ChatsConfig) -> list[FileChange]:
    """Skills land next to the target's own skills; existing ones are left alone."""
    source = bundle_dir / "skills"
    store = config.stores.get("claude")
    if not source.is_dir() or store is None:
        return []
    changes = []
    for skill_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        target = store.root / "skills" / skill_dir.name.replace("__", ":")
        changes.append(
            FileChange(
                target,
                "skip-exists" if target.exists() else "create",
                "skill directory",
                source=skill_dir,
            )
        )
    return changes


def summarise(changes: list[FileChange]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.action] = counts.get(change.action, 0) + 1
    return counts


def apply(changes: list[FileChange], overwrite: bool = False) -> list[str]:
    """Write the planned changes. Returns one note per skipped or odd case."""
    notes: list[str] = []
    for change in changes:
        if change.action == "skip-exists" and not overwrite:
            continue
        if change.action == "sqlite-upsert":
            notes.extend(_upsert(change, overwrite))
            continue
        _snapshot(change)
        change.path.parent.mkdir(parents=True, exist_ok=True)
        if change.data is not None:
            _atomic_write(change.path, change.data)
        elif change.source is not None:
            if change.source.is_dir():
                shutil.copytree(change.source, change.path, dirs_exist_ok=overwrite)
            else:
                shutil.copy2(change.source, change.path)
    return notes


def _snapshot(change: FileChange) -> None:
    if change.action in SNAPSHOT_ACTIONS and change.path.exists():
        backup = change.path.with_name(change.path.name + ".bak")
        if not backup.exists():
            shutil.copy2(change.path, backup)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(data)
    tmp.replace(path)


def _upsert(change: FileChange, overwrite: bool) -> list[str]:
    """Insert the bundled rows into an existing database, never creating one.

    The CLIs own their schemas; if the database is missing, the right fix is to
    start that CLI once and re-run the restore.
    """
    if not change.path.exists():
        return [
            f"{change.path.name} not found — start that CLI once to create it, "
            "then re-run restore"
        ]
    _snapshot(change)
    verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"
    notes: list[str] = []
    conn = sqlite3.connect(change.path)
    try:
        for table, rows in change.rows:
            if not rows:
                continue
            try:
                existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error as e:
                notes.append(f"{table}: {e}")
                continue
            if not existing:
                notes.append(f"{table}: no such table in {change.path.name}, skipped")
                continue
            for row in rows:
                cols = [c for c in row if c in existing]
                if not cols:
                    continue
                marks = ",".join("?" * len(cols))
                names = ",".join(f'"{c}"' for c in cols)
                try:
                    conn.execute(
                        f"{verb} INTO {table} ({names}) VALUES ({marks})",
                        [row[c] for c in cols],
                    )
                except sqlite3.Error as e:
                    notes.append(f"{table}: {e}")
                    break
        conn.commit()
    finally:
        conn.close()
    return notes
