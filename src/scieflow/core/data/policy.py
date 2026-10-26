"""The project's data policy, config/data-policy.yml (M-B Part A 5.2, 5.3).

User-owned and fail-closed: no ScieFlow code path writes this file (M-B-D11).
An agent is safe only when the policy lists it, pins the sha256 of its final
command templates, and gives an endpoint from safe_endpoints (M-B-D3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import urlsplit

import yaml

POLICY_FILE = "config/data-policy.yml"
ENFORCEMENT = ("warn", "refuse")
TEMPLATE_KEYS = ("cmd", "stdin_cmd", "session_cmd", "resume_cmd")
SCITE_PROVIDERS = ("scite", "notebooklm")
# Mirrors research.lib.providers.CATALOGUE (tests/core/data/test_policy.py keeps them equal). Static
# so that loading the policy never imports the optional `research` extra (`requests`), M-B review F1.
KNOWN_PROVIDERS = ("pubtator", "ctgov", "openalex", "europepmc", "search-openalex", "search-arxiv",
                   "search-europepmc", "search-crossref", *SCITE_PROVIDERS)
LEVELS = ("L0", "L1", "L2")
KINDS = ("tabular", "imaging", "eeg", "other")
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
SCAN_DEFAULTS = {"max_files": 2000, "max_bytes_per_file": 65536, "max_seconds": 5}
MIN_CELL_DEFAULT = 5
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS = ("version", "enforcement", "safe_endpoints", "safe_agents", "l2_remotes",
             "l2_providers", "project_levels", "min_cell", "scan", "ingest")


class PolicyInvalid(ValueError):
    """The policy file is malformed. The message names a key, never a value."""


@dataclass(frozen=True)
class SafeAgent:
    endpoint: str
    templates_sha256: str
    reason: str


@dataclass(frozen=True)
class ProjectLevel:
    path: str
    level: str
    kind: str
    reason: str


@dataclass(frozen=True)
class Policy:
    present: bool
    enforcement: str
    safe_endpoints: tuple
    safe_agents: Mapping
    l2_remotes: tuple
    l2_providers: tuple
    project_levels: tuple
    min_cell: int
    scan_max_files: int
    scan_max_bytes_per_file: int
    scan_max_seconds: float
    extra_drop: tuple


@dataclass(frozen=True)
class Boundary:
    kind: str       # "safe" or "cloud"
    endpoint: str   # "" for cloud
    reason: str     # "pinned" for safe; the refusal rule otherwise


def _defaults(present: bool) -> Policy:
    return Policy(present=present, enforcement="warn", safe_endpoints=(), safe_agents={},
                  l2_remotes=(), l2_providers=(), project_levels=(),
                  min_cell=MIN_CELL_DEFAULT,
                  scan_max_files=SCAN_DEFAULTS["max_files"],
                  scan_max_bytes_per_file=SCAN_DEFAULTS["max_bytes_per_file"],
                  scan_max_seconds=SCAN_DEFAULTS["max_seconds"], extra_drop=())


def _fail(where: str, why: str) -> NoReturn:
    raise PolicyInvalid(f"{POLICY_FILE}: {where}: {why}")


def _mapping(value, where: str, keys: tuple, required: tuple = ()) -> dict:
    if not isinstance(value, dict):
        _fail(where, "must be a mapping")
    for key in value:
        if key not in keys:
            _fail(where, f"unknown key {key!r}")
    for key in required:
        if key not in value:
            _fail(where, f"missing key {key!r}")
    return value


def _list(raw: dict, key: str) -> list:
    value = raw.get(key) or []
    if not isinstance(value, list):
        _fail(key, "must be a list")
    return value


def _reason(entry: dict, where: str) -> str:
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        _fail(f"{where}.reason", "is required (a non-empty string)")
    return reason


def _endpoint_ok(url: str) -> bool:
    # Whitespace, control characters and credentials are refused (M-B review F4): the endpoint is
    # substituted into an argv template, and a credential in it would show up in `levels`.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if "@" in parts.netloc or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS


def _positive(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0)


def load(root) -> Policy:
    """The project policy. An absent file gives the safe defaults (nothing is safe).

    A symlink or an unreadable entry is PolicyInvalid, never the defaults: a dangling link
    must not silently drop `enforcement: refuse` (fail-closed, M-B review F2).
    """
    path = Path(root) / POLICY_FILE
    try:
        linked = path.is_symlink()
        present = path.exists()
    except OSError:
        raise PolicyInvalid(f"{POLICY_FILE}: cannot be read") from None
    if linked:
        raise PolicyInvalid(f"{POLICY_FILE}: is a symlink; use a regular file")
    if not present:
        return _defaults(present=False)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise PolicyInvalid(f"{POLICY_FILE}: not readable YAML") from None
    if raw is None:
        return _defaults(present=True)
    _mapping(raw, "top level", _TOP_KEYS)
    if type(raw.get("version")) is not int or raw["version"] != 1:
        _fail("version", "must be 1")
    enforcement = raw.get("enforcement", "warn")
    if enforcement not in ENFORCEMENT:
        _fail("enforcement", "must be warn or refuse")

    endpoints = []
    for i, item in enumerate(_list(raw, "safe_endpoints")):
        where = f"safe_endpoints[{i}]"
        item = _mapping(item, where, ("url", "reason"), ("url",))
        if not isinstance(item["url"], str) or not _endpoint_ok(item["url"]):
            _fail(f"{where}.url", "must be https (http only for 127.0.0.1, ::1 or localhost), "
                                  "with no spaces, control characters or credentials")
        _reason(item, where)
        endpoints.append(item["url"])

    agents = {}
    block = raw.get("safe_agents") or {}
    if not isinstance(block, dict):
        _fail("safe_agents", "must be a mapping of agent name to entry")
    for name, item in block.items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name):
            _fail("safe_agents", "agent names must be plain tokens")
        where = f"safe_agents.{name}"
        item = _mapping(item, where, ("endpoint", "templates_sha256", "reason"),
                        ("endpoint", "templates_sha256"))
        if item["endpoint"] not in endpoints:
            _fail(f"{where}.endpoint", "must be one of safe_endpoints")
        sha = item["templates_sha256"]
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            _fail(f"{where}.templates_sha256",
                  "must be 64 lowercase hex digits (from scieflow data pin <agent>)")
        agents[name] = SafeAgent(endpoint=item["endpoint"], templates_sha256=sha,
                                 reason=_reason(item, where))

    remotes = []
    for i, item in enumerate(_list(raw, "l2_remotes")):
        where = f"l2_remotes[{i}]"
        item = _mapping(item, where, ("remote", "reason"), ("remote",))
        if not isinstance(item["remote"], str) or not _TOKEN.fullmatch(item["remote"]):
            _fail(f"{where}.remote", "must be a plain remote name from config/remotes.yml")
        _reason(item, where)
        remotes.append(item["remote"])

    known = set(KNOWN_PROVIDERS)
    providers = []
    for i, item in enumerate(_list(raw, "l2_providers")):
        where = f"l2_providers[{i}]"
        item = _mapping(item, where, ("name", "reason"), ("name",))
        if item["name"] not in known:
            _fail(f"{where}.name", "is not a known literature service")
        _reason(item, where)
        providers.append(item["name"])

    levels = []
    for i, item in enumerate(_list(raw, "project_levels")):
        where = f"project_levels[{i}]"
        item = _mapping(item, where, ("path", "level", "kind", "reason"), ("path", "level"))
        path = item["path"]
        if not isinstance(path, str) or not path.strip() or ".." in PurePosixPath(path).parts:
            _fail(f"{where}.path", "must be a non-empty path without '..'")
        if item["level"] not in LEVELS:
            _fail(f"{where}.level", "must be L0, L1 or L2")
        kind = item.get("kind", "other")
        if kind not in KINDS:
            _fail(f"{where}.kind", "must be one of " + ", ".join(KINDS))
        levels.append(ProjectLevel(path=path, level=item["level"], kind=kind,
                                   reason=_reason(item, where)))

    min_cell = raw.get("min_cell", MIN_CELL_DEFAULT)
    if isinstance(min_cell, bool) or not isinstance(min_cell, int) or min_cell < 1:
        _fail("min_cell", "must be an integer of at least 1")

    scan = {**SCAN_DEFAULTS, **_mapping(raw.get("scan") or {}, "scan", tuple(SCAN_DEFAULTS))}
    for key in ("max_files", "max_bytes_per_file", "max_seconds"):
        if not _positive(scan[key]):
            _fail(f"scan.{key}", "must be a positive number")

    ingest = _mapping(raw.get("ingest") or {}, "ingest", ("extra_drop",))
    extra = ingest.get("extra_drop", [])
    if not isinstance(extra, list) or not all(
            isinstance(x, str) and _TOKEN.fullmatch(x) for x in extra):
        _fail("ingest.extra_drop", "must be a list of column names")

    return Policy(present=True, enforcement=enforcement, safe_endpoints=tuple(endpoints),
                  safe_agents=agents, l2_remotes=tuple(remotes), l2_providers=tuple(providers),
                  project_levels=tuple(levels), min_cell=min_cell,
                  scan_max_files=scan["max_files"],
                  scan_max_bytes_per_file=scan["max_bytes_per_file"],
                  scan_max_seconds=scan["max_seconds"], extra_drop=tuple(extra))


def project_path(root, text: str) -> Path:
    """A project_levels path: repo-relative, absolute, or ~/."""
    path = Path(os.path.expanduser(text))
    return path if path.is_absolute() else Path(root) / path


def writable_grants(root) -> list[Path]:
    """The writable: grants of config/sandbox.yml, validated by the sandbox's own loader."""
    from scieflow.core import sandbox
    from scieflow.core.project import Project

    root = Path(root)
    return sandbox._allowlist_of(sandbox._load_allowlist(Project(root)), root,
                                 root / sandbox.ALLOWLIST_FILE)


def _templates(cfg: Mapping) -> dict:
    return {key: str(cfg[key]) for key in TEMPLATE_KEYS if key in cfg and cfg[key] is not None}


def pin_sha(cfg: Mapping) -> str:
    """sha256 over the command templates only (canonical JSON), the pin in the policy."""
    payload = {key: cfg[key] for key in TEMPLATE_KEYS if key in cfg}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _launcher_writable(templates: dict, root: Path, writable_areas: list) -> bool:
    """True when a command token is an absolute path under workspace/ or a writable grant."""
    areas = [root / "workspace", *writable_areas]
    real_areas = [os.path.realpath(area) for area in areas]
    subst = {"root": str(root), "python": sys.executable}
    for text in templates.values():
        filled = re.sub(r"\{(\w+)\}", lambda m: subst.get(m.group(1), ""), text)
        try:
            tokens = shlex.split(filled)
        except ValueError:
            # Unparseable: fail closed on a plain substring test of the same areas.
            if any(str(area) in filled for area in areas) or any(
                    area in filled for area in real_areas):
                return True
            continue
        for token in tokens:
            if not token.startswith("/"):
                continue
            real = os.path.realpath(token)
            if any(real == area or real.startswith(area + os.sep) for area in real_areas):
                return True
    return False


def boundary(policy: Policy, agent: str, final_cfg: Mapping, *, root, writable_areas) -> Boundary:
    """`safe` or `cloud` for one agent, computed on its final configuration (5.3)."""
    entry = policy.safe_agents.get(agent)
    if entry is None:
        return Boundary("cloud", "", "not-listed")
    templates = _templates(final_cfg)
    if "cmd" not in templates or any("{endpoint}" not in text for text in templates.values()):
        return Boundary("cloud", "", "no-endpoint-placeholder")
    if pin_sha(final_cfg) != entry.templates_sha256:
        return Boundary("cloud", "", "pin-mismatch")
    if _launcher_writable(templates, Path(root), list(writable_areas)):
        return Boundary("cloud", "", "launcher-writable")
    return Boundary("safe", entry.endpoint, "pinned")
