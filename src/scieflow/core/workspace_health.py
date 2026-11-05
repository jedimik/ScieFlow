"""The workspace index for the browser and the JSON API (T-12 t5, plan 5.7).

Read-only and in memory. It never writes INDEX.md (that stays `scieflow workspace
index`), never recurses, never follows an alias, and reads each run's two control
files through control_read. A control that is not ok is a finding, and the fields
it feeds are reported as unknown, never as a default.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from scieflow.core import control_read as cr
from scieflow.core import workspace as wsmod

if TYPE_CHECKING:
    from scieflow.core.project import Project

INDEX_LIMIT = 10_000
UNKNOWN = "unknown"
NOTE_EXTERNAL = "points outside workspace/; not followed"
_STAMPED = ("status.yml", "log.md", "notebook.md")
_EVENTS = "events.jsonl"


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec="seconds")


def _activity(stamps: dict[str, int | None]) -> tuple[str | None, str | None]:
    """`workspace._updated` and `_updated_at`, from stamps read without following links."""
    file_stamps = [v for n in _STAMPED if (v := stamps.get(n)) is not None]
    updated = (datetime.fromtimestamp(max(file_stamps) / 1e9, tz=timezone.utc).date().isoformat()
               if file_stamps else None)
    all_stamps = [v for n in (*_STAMPED, _EVENTS) if (v := stamps.get(n)) is not None]
    return updated, (_iso(max(all_stamps)) if all_stamps else None)


def _lineage(config: cr.ControlRead):
    if config.status == "ok":
        value = config.data.get("lineage") if isinstance(config.data, dict) else None
        return value if isinstance(value, str) else None
    return None if config.status == "missing" else UNKNOWN


def _unknown_run(slug: str, updated, updated_at) -> dict:
    run = wsmod.Run(slug=slug, kind=UNKNOWN, state=UNKNOWN, updated=updated,
                    id=UNKNOWN, phase=UNKNOWN, phase_state=UNKNOWN, iteration=None,
                    stopped_reason=UNKNOWN, updated_at=updated_at)
    return {**asdict(run), "iteration": UNKNOWN}


def _run_row(ws_fd: int, name: str) -> dict:
    try:
        run_fd = cr.open_subdir(ws_fd, name)
    except OSError as exc:
        return {**_unknown_run(name, UNKNOWN, UNKNOWN), "lineage": UNKNOWN, "aliases": [],
                "findings": [{"file": ".", "status": cr.status_for_error(exc)}]}
    try:
        status = cr.read_yaml(run_fd, "status.yml")
        config = cr.read_yaml(run_fd, "config.yml")
        stamps = {n: cr.mtime_of(run_fd, n) for n in (*_STAMPED, _EVENTS)}
    finally:
        os.close(run_fd)
    updated, updated_at = _activity(stamps)
    findings = [{"file": file, "status": read.status}
                for file, read in (("status.yml", status), ("config.yml", config))
                if read.status != "ok"]
    row = None
    if status.status == "ok":
        status_dict = status.data if isinstance(status.data, dict) else {}
        config_dict = config.data if config.status == "ok" and isinstance(config.data, dict) else {}
        try:
            row = asdict(wsmod.describe_from(name, status_dict, config_dict, updated, updated_at))
        except (AttributeError, KeyError, TypeError, ValueError):
            # Valid YAML of the wrong shape (e.g. `phases: 5`): a finding, never a crash.
            findings.append({"file": "status.yml", "status": "invalid-shape"})
    if row is None:
        row = _unknown_run(name, updated, updated_at)
    row["lineage"] = _lineage(config)
    row["aliases"] = []
    row["findings"] = findings
    return row


def _classify(target: str | None) -> tuple[str, str | None]:
    """A symlink's target as ("moved", target) | ("alias", sibling) | ("external", target)."""
    if target is None:
        return "external", None
    if target.startswith("_misc/"):
        return "moved", target
    sibling = wsmod.sibling_name(target)
    if sibling is not None:
        return "alias", sibling
    return "external", target


def index(project: Project, *, limit: int = INDEX_LIMIT) -> dict:
    """The workspace as it is now: runs, alias grouping, moved and external entries, findings."""
    out: dict = {
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complete": True,
        "inspected": 0,
        "limit": limit,
        "omitted_note": None,
        "runs": [],
        "external_aliases": [],
        "moved": [],
        "lineage": {},
        "lineage_partial": False,
        "unattached_aliases": [],
    }
    try:
        ws_fd = cr.open_dir(project.root, ("workspace",))
    except FileNotFoundError:
        return out
    except OSError as exc:
        out["complete"] = False
        out["omitted_note"] = f"workspace/ could not be opened ({cr.status_for_error(exc)}); not listed"
        return out
    try:
        names, truncated = cr.scan_dir(ws_fd, limit)
        out["inspected"] = len(names)
        rows: list[dict] = []
        aliases: list[tuple[str, str]] = []
        for name in names:
            if name.startswith((".", "_")) or name in wsmod.MODULE_DIRS:
                continue
            kind = cr.stat_entry(ws_fd, name)
            if kind == "dir":
                rows.append(_run_row(ws_fd, name))
            elif kind == "symlink":
                target = cr.read_link(ws_fd, name)
                how, value = _classify(target)
                if how == "moved":
                    out["moved"].append({"name": name, "target": value})
                elif how == "alias":
                    aliases.append((name, str(value)))
                else:
                    out["external_aliases"].append({"name": name, "target": value,
                                                    "note": NOTE_EXTERNAL})
    finally:
        os.close(ws_fd)
    by_slug = {row["slug"]: row for row in rows}
    for name, sibling in aliases:
        if sibling in by_slug:
            by_slug[sibling]["aliases"].append(name)
        else:
            out["unattached_aliases"].append({"name": name, "target": sibling})
    for row in rows:
        row["aliases"].sort()
    out["runs"] = sorted(rows, key=lambda row: row["slug"])
    out["external_aliases"].sort(key=lambda entry: entry["name"])
    out["moved"].sort(key=lambda entry: entry["name"])
    out["unattached_aliases"].sort(key=lambda entry: entry["name"])
    families: dict[str, list[str]] = {}
    for row in out["runs"]:
        if row["lineage"] == UNKNOWN:
            out["lineage_partial"] = True
        elif row["lineage"]:
            families.setdefault(row["lineage"], []).append(row["slug"])
    out["lineage"] = {name: slugs for name, slugs in sorted(families.items())}
    if truncated:
        out["complete"] = False
        out["omitted_note"] = (f"stopped after {limit} entries; the rest of workspace/ "
                               "was not listed")
    if not out["complete"]:
        out["lineage_partial"] = True
        out["omitted_note"] = out["omitted_note"] or "the index is incomplete"
    return out
