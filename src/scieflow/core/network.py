"""The network mode of a run (M-C1 Part A 5.4, 5.7).

A run's literature traffic has one of four modes, decided before any transport is chosen:

    live     requests go out; the run's data level (M-B) still decides what may leave
    record   as live, and every answer is also written to the run's snapshots
    replay   answers come only from the run's snapshots; a miss is a typed error, never a request
    offline  as replay, then the Gateway's cache; a miss is a typed error

The mode comes from workspace/<run>/network.yml when the file is present and valid. Otherwise it
comes from the run's data level: an L2 run reaches only the services its data policy lists (M-B),
and any other service replays, so its misses are the M-B refusal. An invalid network.yml fails
closed to offline. A command flag (--network) may tighten a mode or move between the two egress
modes; it can never loosen a no-egress mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from scieflow.core.data import levels
from scieflow.core.data import policy as data_policy
from scieflow.integrations.scite.policy import PolicyError

FILE = "network.yml"
VERSION = 1
MODES = ("live", "record", "replay", "offline")
EGRESS = frozenset({"live", "record"})
SET_BY = ("human", "agent")
_KEYS = frozenset({"version", "mode", "set_by", "set_at", "reason"})


class NetworkRefused(PolicyError):
    """A boundary (exit 3): a command flag would loosen a no-egress mode."""


class NetworkUsage(ValueError):
    """A network option used without the run it needs (exit 1)."""


class NetworkMiss(PolicyError):
    """No snapshot or cache entry answers a no-egress request. Nothing was sent (exit 6)."""

    def __init__(self, provider: str, op: str, key: str, mode: str, run: str):
        self.provider, self.op, self.mode, self.run = provider, op, mode, run
        self.key = key[:12]
        super().__init__(f"offline-miss: {provider} {op} (key {self.key}): run {run} is {mode}; "
                         "nothing was sent")


@dataclass(frozen=True)
class Resolved:
    mode: str
    origin: str     # file | invalid | default | l2-default | flag
    on_miss: str    # "miss": NetworkMiss; "m-b": the M-B refusal of the data policy; "": no miss


def _check(raw) -> tuple[str, str]:
    """("file", mode) for a well-formed network.yml, else ("invalid", reason)."""
    if not isinstance(raw, dict):
        return "invalid", f"{FILE} must be a mapping"
    if set(raw) != _KEYS:
        return "invalid", f"{FILE} must have exactly the keys {', '.join(sorted(_KEYS))}"
    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != VERSION:
        return "invalid", f"{FILE}: version must be {VERSION}"
    if raw["mode"] not in MODES:
        return "invalid", f"{FILE}: mode must be one of {', '.join(MODES)}"
    if raw["set_by"] not in SET_BY:
        return "invalid", f"{FILE}: set_by must be one of {', '.join(SET_BY)}"
    if not isinstance(raw["set_at"], str) or not isinstance(raw["reason"], str):
        return "invalid", f"{FILE}: set_at and reason must be text"
    return "file", raw["mode"]


def _file_state(ws: Path) -> tuple[str, str]:
    """("absent", "") | ("file", mode) | ("invalid", reason). A symbolic link is invalid."""
    path = ws / FILE
    if not os.path.lexists(path):
        return "absent", ""
    if path.is_symlink():
        return "invalid", f"{FILE} is a symbolic link"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "invalid", f"{FILE} is not readable"
    except yaml.YAMLError:
        return "invalid", f"{FILE} is not valid YAML"
    return _check(raw)


def _l2_lists(ws: Path, service: str) -> bool:
    """Whether the run's data policy lists `service` in l2_providers (an invalid policy lists none)."""
    try:
        pol = data_policy.load(ws.resolve().parent.parent)
    except data_policy.PolicyInvalid:
        return False
    return service in pol.l2_providers


def resolve(ws, service: str, *, flag: str | None = None) -> Resolved:
    """The mode for one request of `service` made for run `ws` (None: no run).

    The file or the run's level decides the base mode; the command flag then tightens it or moves
    between the egress modes. A flag cannot loosen a no-egress base (NetworkRefused)."""
    if flag is not None and flag not in MODES:
        raise NetworkUsage(f"--network takes one of {', '.join(MODES)}")
    if ws is None:
        if flag in (None, "live"):
            return Resolved("live", "flag" if flag else "default", "")
        raise NetworkUsage(f"--network {flag} needs --workspace: the mode is a run's setting")
    ws = Path(ws)
    state, value = _file_state(ws)
    if state == "file":
        base = Resolved(value, "file", "miss")
    elif state == "invalid":
        base = Resolved("offline", "invalid", "miss")
    elif levels.run_level(ws) == "L2":
        if _l2_lists(ws, service):
            base = Resolved("live", "l2-default", "")
        else:
            base = Resolved("replay", "l2-default", "m-b")
    else:
        base = Resolved("live", "default", "")
    if flag is None:
        return base
    if base.mode not in EGRESS and flag in EGRESS:
        raise NetworkRefused(f"network: run {ws.name} is {base.mode}; "
                             "a command option cannot loosen it")
    return Resolved(flag, "flag", "miss")
