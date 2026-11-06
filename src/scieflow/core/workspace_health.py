"""The workspace index for the browser and the JSON API (T-12 t5, plan 5.7).

Read-only and in memory. It never writes INDEX.md (that stays `scieflow workspace
index`), never recurses, never follows an alias, and reads each run's two control
files through control_read. A control that is not ok is a finding, and the fields
it feeds are reported as unknown, never as a default.
"""

from __future__ import annotations

import importlib.util
import math
import os
import time
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from scieflow.core import control_read as cr
from scieflow.core import workspace as wsmod
from scieflow.core.project import ProjectError

if TYPE_CHECKING:
    from scieflow.core.project import Project

INDEX_LIMIT = 10_000
MAX_TEXT = 200   # the longest text a control field may carry into a row (bounds the reply)
UNKNOWN = "unknown"
NOTE_EXTERNAL = "points outside workspace/; not followed"
_STAMPED = ("status.yml", "log.md", "notebook.md")
_EVENTS = "events.jsonl"


def _bounded(value) -> bool:
    """A value the index may copy into a row: None, a bool or number, a short string."""
    if isinstance(value, str):
        return len(value) <= MAX_TEXT
    if value is None or isinstance(value, (bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _status_shaped(status: dict) -> bool:
    """Whether every status field the row copies is a bounded scalar.

    An alias can make a ~1 KB file expand to tens of MB once it is copied, so a
    container in any of these fields is an invalid shape, not a value to show.
    """
    stopped = status.get("stopped")
    if isinstance(stopped, dict):
        stopped = stopped.get("reason")
    phases = status.get("phases")
    phases_ok = phases is None or (isinstance(phases, dict) and all(
        _bounded(key) and _bounded(value) for key, value in phases.items()))
    return phases_ok and _bounded(stopped) and all(
        _bounded(status.get(key)) for key in ("id", "workflow", "phase", "iteration"))


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
        if not _status_shaped(status_dict):
            findings.append({"file": "status.yml", "status": "invalid-shape"})
        else:
            try:
                row = asdict(wsmod.describe_from(name, status_dict, config_dict, updated, updated_at))
            except (AttributeError, KeyError, TypeError, ValueError):
                # Valid YAML of the wrong shape (e.g. `phases: 5`): a finding, never a crash.
                findings.append({"file": "status.yml", "status": "invalid-shape"})
    if row is None:
        row = _unknown_run(name, updated, updated_at)
    config_lineage = config.data.get("lineage") if isinstance(config.data, dict) else None
    lineage_ok = _bounded(config_lineage)
    if not lineage_ok:
        findings.append({"file": "config.yml", "status": "invalid-shape"})
    row["lineage"] = _lineage(config) if lineage_ok else UNKNOWN
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


# -- run health (T-12 t6, plan 5.8) ---------------------------------------------------
# The cheap checks read a fixed set of controls. The scan walks the run's tree once, under one
# budget shared by all its walks, and reports what it could not finish as partial, never as a
# total. Nothing is repaired, cleaned, sent anywhere or written.
HEALTH_MAX_ENTRIES = 10_000
HEALTH_MAX_SECONDS = 2.0
SCAN_DEPTH = 4                      # the depth doctor walks to (workspace.doctor)
NOTE_RESULTS = "results may change while a run is active"
NOTE_NOT_YET = "not checked yet: press Check now to count it"
NOTE_NOT_REACHED = "not reached: the scan stopped before this check"
_ARCHIVE_PATH = ("scripts", "sflib", "archive.py")


class RunNotFound(LookupError):
    """No run of that name; the service layer turns it into a ServiceError (404)."""


def archive_module(root: Path):
    """The `scripts/sflib/archive.py` of the project at `root` (None when absent).

    Loaded by its path under the project root, never through the working directory, so a
    run's junk classification is the same whatever directory the process started in.
    """
    path = Path(root).joinpath(*_ARCHIVE_PATH)
    if path.is_symlink() or not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("scieflow_run_health_archive", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Budget:
    """One budget for every walk of a scan: each entry a walk yields is one step.

    `take` checks the clock before each step; once a limit is reached it stops, and every
    later `take` refuses, so no walk starts another operation.
    """

    def __init__(self, max_entries: int, max_seconds: float, clock):
        self.max_entries = max_entries
        self.max_seconds = max_seconds
        self.clock = clock
        self.started = clock()
        self.count = 0
        self.stop_reason: str | None = None

    def take(self) -> bool:
        if self.stop_reason is None:
            if self.count >= self.max_entries:
                self.stop_reason = "entries"
            elif self.clock() - self.started >= self.max_seconds:
                self.stop_reason = "time"
            else:
                self.count += 1
                return True
        return False


def _open_rel(root_fd: int, parts: tuple) -> int:
    """A descriptor for root/parts, every component opened with O_NOFOLLOW (a link is refused)."""
    fd = os.dup(root_fd)
    try:
        for name in parts:
            nxt = cr.open_subdir(fd, name)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _entries(fd: int, budget: _Budget, refusals: list) -> Iterator[os.DirEntry]:
    """One directory's entries, streamed, one budget step each. An unreadable one is a refusal."""
    try:
        iterator = os.scandir(fd)
    except OSError as exc:
        refusals.append(cr.status_for_error(exc))
        return
    with iterator:
        for entry in iterator:
            if not budget.take():
                return
            yield entry


def _size_under(run_fd: int, parts: tuple, budget: _Budget, refusals: list) -> int:
    """Bytes under a rebuildable directory, counted as doctor's `_size` counts them.

    Non-directories are counted by their own size (lstat); a symbolic link to a directory is
    neither followed nor counted, as os.walk does not follow it. Iterative, so depth is not
    limited by the interpreter's recursion limit.
    """
    total = 0
    stack = [parts]
    while stack and budget.stop_reason is None:
        here = stack.pop()
        try:
            fd = _open_rel(run_fd, here)
        except OSError as exc:
            refusals.append(cr.status_for_error(exc))
            continue
        try:
            for entry in _entries(fd, budget, refusals):
                if entry.is_dir():
                    if not entry.is_symlink():
                        stack.append((*here, entry.name))
                    continue
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
        finally:
            os.close(fd)
    return total


def _walk(run_fd: int, run_path: str, archive, budget: _Budget, refusals: list) -> tuple[dict, int]:
    """Doctor's walk: rebuildable directories (with their sizes) and absolute self-links.

    Every directory down to SCAN_DEPTH is listed; the rebuildable children of the deepest listed
    directory are classified too, exactly as doctor's walk classifies them.
    """
    junk: dict[str, int] = {}
    self_links = 0
    run_dir = Path(run_path)
    stack: list[tuple] = [()]
    while stack and budget.stop_reason is None:
        here = stack.pop()
        try:
            fd = _open_rel(run_fd, here)
        except OSError as exc:
            refusals.append(cr.status_for_error(exc))
            continue
        try:
            for entry in _entries(fd, budget, refusals):
                if entry.is_symlink():
                    target = cr.read_link(fd, entry.name)
                    if target is not None and wsmod.is_self_link(target, run_path):
                        self_links += 1
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                child = (*here, entry.name)
                if archive is not None and archive._skip_dir(run_dir.joinpath(*child), run_dir):
                    junk["/".join(child)] = _size_under(run_fd, child, budget, refusals)
                elif len(here) < SCAN_DEPTH:
                    stack.append(child)
        finally:
            os.close(fd)
    return junk, self_links


def _count_scripts(run_fd: int, budget: _Budget, refusals: list) -> tuple[int, str, dict | None]:
    """(count, status, finding) for the `*.py` entries of logs/ (doctor's `glob("*.py")`)."""
    kind = cr.stat_entry(run_fd, "logs")
    if kind == "missing":
        return 0, "complete", None
    if kind != "dir":
        return 0, "refused", {"file": "logs", "status": "symlink" if kind == "symlink" else "not-regular"}
    try:
        fd = _open_rel(run_fd, ("logs",))
    except OSError as exc:
        return 0, "refused", {"file": "logs", "status": cr.status_for_error(exc)}
    count = 0
    try:
        for entry in _entries(fd, budget, refusals):
            if entry.name.endswith(".py"):
                count += 1
    finally:
        os.close(fd)
    return count, "complete", None


def _open(project: Project, slug: str):
    """(name, ws_fd, run_fd) for a real run; the alias information instead for a link.

    The slug is canonicalised first; the workspace and the run are opened without following
    a link, so an alias or an external link is described and never walked.
    """
    try:
        name = project.run_dir(slug).name
    except ProjectError as exc:
        raise RunNotFound(str(exc)) from exc
    missing = f"no run workspace/{slug}"
    try:
        ws_fd = cr.open_dir(project.root, ("workspace",))
    except OSError as exc:
        raise RunNotFound(missing) from exc
    try:
        kind = cr.stat_entry(ws_fd, name)
        if kind == "symlink":
            info = _alias_info(name, cr.read_link(ws_fd, name))
            os.close(ws_fd)
            return info
        if kind != "dir":
            raise RunNotFound(missing)
        run_fd = cr.open_subdir(ws_fd, name)
    except RunNotFound:
        os.close(ws_fd)
        raise
    except OSError as exc:
        os.close(ws_fd)
        raise RunNotFound(missing) from exc
    return name, ws_fd, run_fd


def _alias_info(name: str, target: str | None) -> dict:
    how, value = _classify(target)
    if how == "external":
        return {"slug": name, "external_alias": value, "note": NOTE_EXTERNAL}
    return {"slug": name, "alias_of": value}


def _check(status: str, **fields) -> dict:
    return {"status": status, **fields}


def _describe(name: str, run_fd: int):
    """(kind, state, config read, findings) from status.yml; an unreadable status is 'unknown'."""
    status = cr.read_yaml(run_fd, "status.yml")
    config = cr.read_yaml(run_fd, "config.yml")
    config_data = config.data if config.status == "ok" else {}
    if status.status == "missing":
        run = wsmod.describe_from(name, {}, config_data, None, None)
        return run.kind, run.state, config, []
    if status.status != "ok":
        return "unknown", "unknown", config, [{"file": "status.yml", "status": status.status}]
    if not _status_shaped(status.data):
        return "unknown", "unknown", config, [{"file": "status.yml", "status": "invalid-shape"}]
    try:
        run = wsmod.describe_from(name, status.data, config_data, None, None)
    except (AttributeError, KeyError, TypeError, ValueError):
        return "unknown", "unknown", config, [{"file": "status.yml", "status": "invalid-shape"}]
    return run.kind, run.state, config, []


def _cheap_checks(ws_fd: int, name: str, run_fd: int, kind: str, config, status_findings: list):
    """The checks every page load runs: missing controls, legacy keys, the per-file DVC pointer."""
    findings: list = []
    checks: dict = {}
    if kind == "unknown":
        checks["missing"] = _check("refused", finding=status_findings[0])
    else:
        missing = []
        for file in wsmod.expected_files(kind):
            found = cr.stat_entry(run_fd, file)
            if found == "missing":
                missing.append(file)
            elif found != "file":
                finding = {"file": file, "status": "symlink" if found == "symlink" else "not-regular"}
                findings.append(finding)
        checks["missing"] = _check("complete", value=missing)
    if config.status == "ok":
        checks["legacy_config_keys"] = _check("complete", value=wsmod.legacy_keys(config.data))
    elif config.status == "missing":
        checks["legacy_config_keys"] = _check("complete", value=[])
    else:
        finding = {"file": "config.yml", "status": config.status}
        findings.append(finding)
        checks["legacy_config_keys"] = _check("refused", finding=finding)
    checks["per_file_dvc_pointer"] = _check(
        "complete", value=cr.stat_entry(ws_fd, f"{name}.dvc") != "missing")
    return checks, findings


def _result(name: str, kind: str, state: str, checks: dict, findings: list, mode: str,
            **extra) -> dict:
    return {"observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "slug": name, "kind": kind, "state": state, "mode": mode, "checks": checks,
            "findings": findings, "note": NOTE_RESULTS, **extra}


def health(project: Project, slug: str) -> dict:
    """The run's cheap health (`GET /runs/<slug>/health`): fixed control reads, no walk, no write."""
    opened = _open(project, slug)
    if isinstance(opened, dict):
        return opened
    name, ws_fd, run_fd = opened
    try:
        kind, state, config, status_findings = _describe(name, run_fd)
        checks, findings = _cheap_checks(ws_fd, name, run_fd, kind, config, status_findings)
        findings = status_findings + findings
        for walked in ("junk_dirs", "absolute_self_links", "scripts_in_logs"):
            checks[walked] = _check("not-checked", reason=NOTE_NOT_YET)
        return _result(name, kind, state, checks, findings, mode="cheap")
    finally:
        os.close(run_fd)
        os.close(ws_fd)


def scan(project: Project, slug: str, *, max_entries: int = HEALTH_MAX_ENTRIES,
         max_seconds: float = HEALTH_MAX_SECONDS, clock=time.monotonic) -> dict:
    """The run's full health (`POST /runs/<slug>/health/check`): one bounded walk of its tree.

    At most `max_entries` entries and about `max_seconds` across all walks together. A check the
    budget or a refusal cut short is `partial` with a lower bound (`at_least`); a check it never
    reached is `not-checked`. Nothing is followed, repaired, written or sent anywhere.
    """
    opened = _open(project, slug)
    if isinstance(opened, dict):
        return opened
    name, ws_fd, run_fd = opened
    try:
        budget = _Budget(max_entries, max_seconds, clock)
        refusals: list[str] = []
        kind, state, config, status_findings = _describe(name, run_fd)
        checks, findings = _cheap_checks(ws_fd, name, run_fd, kind, config, status_findings)
        findings = status_findings + findings
        run_path = str(project.root / "workspace" / name)
        archive = archive_module(project.root)
        if archive is None:
            checks["junk_dirs"] = _check("refused", finding={"file": "/".join(_ARCHIVE_PATH),
                                                             "status": "missing"})
        junk, self_links = _walk(run_fd, run_path, archive, budget, refusals)
        walk_whole = budget.stop_reason is None and not refusals
        ordered = dict(sorted(junk.items(), key=lambda kv: -kv[1]))
        if archive is not None:
            checks["junk_dirs"] = (_check("complete", value=ordered) if walk_whole
                                   else _check("partial", at_least=ordered))
        checks["absolute_self_links"] = (_check("complete", value=self_links) if walk_whole
                                         else _check("partial", at_least=self_links))
        if budget.stop_reason is not None:
            checks["scripts_in_logs"] = _check("not-checked", reason=NOTE_NOT_REACHED)
        else:
            before = len(refusals)
            count, status, finding = _count_scripts(run_fd, budget, refusals)
            if status == "refused":
                checks["scripts_in_logs"] = _check("refused", finding=finding)
                findings.append(finding)
            elif budget.stop_reason is None and len(refusals) == before:
                checks["scripts_in_logs"] = _check("complete", value=count)
            else:
                checks["scripts_in_logs"] = _check("partial", at_least=count)
        stop = budget.stop_reason or (f"refused:{refusals[0]}" if refusals else "none")
        whole = all(check["status"] == "complete" for check in checks.values())
        return _result(name, kind, state, checks, findings, mode="scan", complete=whole,
                       entries_observed=budget.count, max_entries=max_entries,
                       max_seconds=max_seconds, stop_reason=stop)
    finally:
        os.close(run_fd)
        os.close(ws_fd)
