"""Validators for event and job records (M-I t2; plan section 5.2).

Writers call the checks before a record reaches disk (`events.emit` and
`jobs.save`). Readers use the read checks to report on what is already there
(`workspace doctor`), and never raise on content. Messages name the rule and
the field or key path, never the value; the error types carry `rule` and
`where`.

`events` and `jobs` import this module at load time, so both are imported
lazily inside the functions that need their constants.
"""

from __future__ import annotations

import dataclasses
import json
import math
import numbers
import os
import re
from datetime import datetime
from pathlib import Path, PurePath

ENVELOPE_KEYS = frozenset({"id", "ts", "run", "slug", "type", "actor", "data"})
READ_REQUIRED_KEYS = ("id", "ts", "type", "actor", "data")
EVENT_ID_PATTERN = "[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}"
NOTE_NAME_PATTERN = "[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}"
JOB_ID_PATTERN = "[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
JOB_KIND_PATTERN = "[a-z][a-z0-9_-]{0,31}"
MAX_READ_BYTES = 50 * 1024 * 1024
NOTE_PREFIX = "note."

_JOB_TYPES = ("job.queued", "job.started", "job.finished", "job.failed", "job.timeout",
              "job.cancelled", "job.lost")
_PHASE_TYPES = ("phase.pending", "phase.started", "phase.done", "phase.failed")

# Event type -> data keys every emitter on the base passes (M-I-D7; t0 inventory (b)).
REQUIRED_DATA: dict[str, tuple[str, ...]] = {
    **{t: ("job", "kind", "label") for t in _JOB_TYPES},
    "job.refused": ("reason",),
    "gate.opened": ("gate", "kind", "question"),
    "gate.answered": ("gate", "kind", "answer"),
    "gate.withdrawn": ("gate", "kind"),
    **{t: ("phase",) for t in _PHASE_TYPES},
    "iteration.advanced": ("iteration",),
    "checkpoint": ("reason",),
    "charter.set": ("version",),
    "curation.changed": ("version",),
    "curation.round": ("version",),
    "data.warning": ("rule", "path"),
    "data.level_resolved": ("level", "boundary"),
    "network.set": ("mode",),
    "literature.snapshot": ("action",),
    "literature.monitor": ("action",),
}


class RecordInvalid(ValueError):
    """A record refused by one rule. Carries the rule id and the field or key path."""

    kind = "record"

    def __init__(self, rule: str, where: str):
        self.rule = rule
        self.where = where
        super().__init__(f"{self.kind} record refused: rule {rule} at {where}")


class EventInvalid(RecordInvalid):
    kind = "event"


class JobInvalid(RecordInvalid):
    kind = "job"


# -- events ----------------------------------------------------------------

def check_event(event: dict) -> dict:
    """Return the normalised event that is written and returned; raise EventInvalid."""
    from scieflow.core import events

    if not isinstance(event, dict) or set(event) != ENVELOPE_KEYS:
        raise EventInvalid("envelope-keys", "envelope")
    if not _is_text(event["id"], EVENT_ID_PATTERN):
        raise EventInvalid("id", "id")
    if not _is_timestamp(event["ts"]):
        raise EventInvalid("ts", "ts")
    for key in ("run", "slug"):
        if not (isinstance(event[key], str) and event[key]):
            raise EventInvalid(key, key)
    _check_type(event["type"], events)
    if not (isinstance(event["actor"], str) and event["actor"] in events.ACTORS):
        raise EventInvalid("actor", "actor")
    data = event["data"]
    if not isinstance(data, dict):
        raise EventInvalid("data-object", "data")
    normal = _normal_data(data, "data")
    for key in REQUIRED_DATA.get(event["type"], ()):
        if key not in normal:
            raise EventInvalid("required-data", f"data.{key}")
    return dict(event, data=normal)


def _check_type(type_, events) -> None:
    if not isinstance(type_, str):
        raise EventInvalid("type", "type")
    if type_ in events.TYPES:
        return
    if type_.startswith(NOTE_PREFIX):
        if re.fullmatch(NOTE_NAME_PATTERN, type_[len(NOTE_PREFIX):]):
            return
        raise EventInvalid("note-name", "type")
    raise EventInvalid("type", "type")


def _normal_data(value, where: str):
    """A plain-JSON copy of a data value after the coercions; raises EventInvalid otherwise."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, os.PathLike):
        path = os.fspath(value)
        if isinstance(path, str):
            return path
        raise EventInvalid("data-value", where)
    if isinstance(value, (list, tuple)):
        return [_normal_data(item, f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EventInvalid("data-object", where)
            out[key] = _normal_data(item, f"{where}.{key}")
        return out
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise EventInvalid("data-finite", where)
        return number
    raise EventInvalid("data-value", where)


def _is_timestamp(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _is_text(value, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# -- jobs ------------------------------------------------------------------

def check_job(record: dict) -> None:
    """Raise JobInvalid unless `record` (asdict of a Job) may be written."""
    _check_job(record, final=True)


def _check_job(record, *, final: bool) -> None:
    from scieflow.core import jobs

    if not isinstance(record, dict):
        raise JobInvalid("object", "job")
    if set(record) != {f.name for f in dataclasses.fields(jobs.Job)}:
        raise JobInvalid("job-keys", "job")
    if not _is_text(record["id"], JOB_ID_PATTERN):
        raise JobInvalid("job-id", "id")
    if not _is_text(record["kind"], JOB_KIND_PATTERN):
        raise JobInvalid("job-kind", "kind")
    if not (isinstance(record["state"], str) and record["state"] in jobs.STATES):
        raise JobInvalid("job-state", "state")
    argv = record["argv"]
    if not (isinstance(argv, list) and all(isinstance(item, str) for item in argv)):
        raise JobInvalid("job-argv", "argv")
    for key in ("cwd", "label", "queued", "log", "err"):
        if not isinstance(record[key], str):
            raise JobInvalid("job-strings", key)
    for key in ("run_dir", "started", "finished"):
        if record[key] is not None and not isinstance(record[key], str):
            raise JobInvalid("job-strings", key)
    for key in ("pid", "exit_code"):
        if record[key] is not None and not _is_int(record[key]):
            raise JobInvalid("job-numbers", key)
    timeout = record["timeout_s"]
    if timeout is not None and not (_is_number(timeout) and math.isfinite(timeout) and timeout >= 0):
        raise JobInvalid("job-numbers", "timeout_s")
    if not isinstance(record["sandboxed"], bool):
        raise JobInvalid("job-flags", "sandboxed")
    if record["turn_audit"] is not None and not isinstance(record["turn_audit"], dict):
        raise JobInvalid("job-flags", "turn_audit")
    if final and record["state"] in jobs.FINAL and not record["finished"]:
        raise JobInvalid("job-final", "finished")


# -- read checks (never raise on content) ----------------------------------

def check_event_lines(path) -> list[dict]:
    """Problems in an events.jsonl: [{"line": n, "rule": ...}]. Blank lines are skipped."""
    from scieflow.core import events

    path = Path(path)
    if not path.is_file():
        return []
    if path.stat().st_size > MAX_READ_BYTES:
        return [{"line": None, "rule": "too-large"}]
    problems = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                problems.append({"line": number, "rule": "json"})
                continue
            rule = _read_rule(obj, events)
            if rule is not None:
                problems.append({"line": number, "rule": rule})
    return problems


def _read_rule(obj, events) -> str | None:
    if not isinstance(obj, dict):
        return "object"
    if not all(key in obj for key in READ_REQUIRED_KEYS):
        return "keys"
    type_ = obj["type"]
    known = isinstance(type_, str) and (
        type_ in events.TYPES
        or (type_.startswith(NOTE_PREFIX) and re.fullmatch(NOTE_NAME_PATTERN, type_[len(NOTE_PREFIX):]))
    )
    if not known:
        return "type"
    if not (isinstance(obj["actor"], str) and obj["actor"] in events.ACTORS):
        return "actor"
    if not isinstance(obj["data"], dict):
        return "data"
    return None


def check_job_files(directory) -> list[dict]:
    """Problems in a jobs/ directory: [{"file": name, "rule": ...}]. The job-final rule is not read-checked."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    problems = []
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                problems.append({"file": path.name, "rule": "too-large"})
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            problems.append({"file": path.name, "rule": "unreadable"})
            continue
        except ValueError:
            problems.append({"file": path.name, "rule": "json"})
            continue
        try:
            _check_job(record, final=False)
        except JobInvalid as exc:
            problems.append({"file": path.name, "rule": exc.rule})
    return problems
