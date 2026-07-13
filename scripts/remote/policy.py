"""Load config/remotes.yml and enforce its deny-by-default allowlists.

The single sanctioned gate for remote operations: every remote.py
subcommand authorizes here before any SSH happens. PolicyError means the
user's config forbids the operation — report it, never work around it.
"""

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_KEYS = ["host", "user", "auth", "scheduler", "allowed_dirs",
                 "allowed_ops", "limits"]
REQUIRED_LIMITS = ["max_walltime", "max_cpus", "max_mem_gb", "max_gpus",
                   "queues", "max_concurrent_jobs", "max_fix_attempts"]

# Everything interpolated into a remote shell command must stay inside
# these charsets — the remote side of ssh/rsync always goes through a
# shell, so metacharacters here would bypass the allowlists entirely.
_SAFE_TOKEN_RE = re.compile(r"^[\w.\-]+$")
_SAFE_PATH_RE = re.compile(r"^[\w./\-]+$")


class PolicyError(Exception):
    """Operation refused by config/remotes.yml."""


@dataclass
class Remote:
    name: str
    host: str
    user: str
    auth: str
    scheduler: str
    allowed_dirs: list
    allowed_ops: list
    limits: dict


def load_remote(root: Path, name: str) -> Remote:
    path = root / "config" / "remotes.yml"
    if not path.exists():
        raise PolicyError(
            f"{path} not found — copy config/remotes.example.yml to "
            "config/remotes.yml and fill it in"
        )
    data = yaml.safe_load(path.read_text()) or {}
    entry = (data.get("remotes") or {}).get(name)
    if entry is None:
        raise PolicyError(f"remote '{name}' not defined in {path}")
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise PolicyError(f"remote '{name}' missing keys: {', '.join(missing)}")
    missing = [k for k in REQUIRED_LIMITS if k not in entry["limits"]]
    if missing:
        raise PolicyError(f"remote '{name}' limits missing: {', '.join(missing)}")
    return Remote(name=name, **{k: entry[k] for k in REQUIRED_KEYS})


def check_op(remote: Remote, op: str) -> None:
    if op not in remote.allowed_ops:
        raise PolicyError(
            f"operation '{op}' not in allowed_ops for remote "
            f"'{remote.name}' (allowed: {', '.join(remote.allowed_ops)})"
        )


def check_token(value: str, what: str) -> str:
    if not _SAFE_TOKEN_RE.match(value):
        raise PolicyError(f"unsafe {what} (allowed: letters, digits, "
                          f"'.', '-', '_'): '{value}'")
    return value


def check_script(script: str) -> str:
    # Relative to the submit <dir> (qsub runs `cd <dir> && qsub ... <script>`),
    # so an absolute path, a leading '-' (option injection), or any '..'
    # segment would escape the approved directory.
    if (not _SAFE_PATH_RE.match(script) or script.startswith(("-", "/"))
            or ".." in script.split("/")):
        raise PolicyError(f"unsafe script path: '{script}'")
    return script


def check_dir(remote: Remote, path: str) -> str:
    if not _SAFE_PATH_RE.match(path):
        raise PolicyError(f"remote path contains unsafe characters: '{path}'")
    norm = posixpath.normpath(path)
    if not norm.startswith("/"):
        raise PolicyError(f"remote path must be absolute: '{path}'")
    for allowed in remote.allowed_dirs:
        base = posixpath.normpath(allowed)
        if norm == base or norm.startswith(base + "/"):
            return norm
    raise PolicyError(
        f"'{norm}' is outside allowed_dirs for remote '{remote.name}'"
    )


def check_queue(remote: Remote, queue: str) -> None:
    queues = remote.limits["queues"]
    if queue not in queues:
        raise PolicyError(
            f"queue '{queue}' not allowed for remote '{remote.name}' "
            f"(allowed: {', '.join(queues)})"
        )


def walltime_seconds(walltime: str) -> int:
    parts = str(walltime).split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise PolicyError(f"bad walltime '{walltime}' (expected HH:MM:SS)")
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def clamp_resources(remote: Remote, walltime: str, cpus: int, mem_gb: int,
                    gpus: int) -> tuple[dict, list[str]]:
    lim = remote.limits
    warnings = []
    if walltime_seconds(walltime) > walltime_seconds(lim["max_walltime"]):
        warnings.append(f"walltime {walltime} clamped to {lim['max_walltime']}")
        walltime = lim["max_walltime"]
    caps = [("cpus", cpus, lim["max_cpus"]), ("mem_gb", mem_gb, lim["max_mem_gb"]),
            ("gpus", gpus, lim["max_gpus"])]
    clamped = {}
    for label, value, cap in caps:
        if value > cap:
            warnings.append(f"{label} {value} clamped to {cap}")
            value = cap
        clamped[label] = value
    return {"walltime": walltime, **clamped}, warnings
