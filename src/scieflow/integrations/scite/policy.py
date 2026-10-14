"""Load config/scite.yml and enforce it — deny by default.

The file is the user's control surface over scite: whether it is used at all,
which MCP tools core may call, and how many calls a round / run / day may
spend. It is user-owned and gitignored; `config/scite.example.yml` is the
committed template.

- No `config/scite.yml` → scite is off for every run, with one reason.
- Unknown keys, wrong types or unknown tools → `PolicyError` (never guessed).
- The token is checked by *presence only*: an environment variable name or a
  token file's existence. This module never reads, stores, logs or prints the
  token value; the MCP transport reads it at call time. Error messages never
  echo a bad string value that could be a pasted token (`_show`). The
  `token_env` *name* is shown by status output, so it must be a name.

A `PolicyError` is a boundary: report it, never retry around it. CLIs map it
to exit code 3 (`REFUSED_POLICY`), like the claim-check module.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG = Path("config") / "scite.yml"
EXIT_REFUSED_POLICY = 3

# scite MCP retrieval tools core may ever be allowed to call. Collection
# writers, reports and prompts are not in the catalogue, so no config can
# enable them.
KNOWN_TOOLS = ("search_literature", "citation_graph", "read_fulltext", "bibliography")
DEFAULT_TOOLS = ("search_literature", "citation_graph")
TTL_CLASSES = ("metadata", "search")

_DEFAULTS = {
    "token_env": "SCITE_TOKEN",
    "max_mcp_calls_per_round": 5,
    "max_mcp_calls_per_run": 20,
    "max_mcp_calls_per_day": None,
    "max_followups_per_round": 2,
    "contested_seeds": 10,
    "rest_rate_per_s": 5,
    "rest_max_dois_per_request": 500,
}
_POSITIVE_INTS = ("max_mcp_calls_per_round", "max_mcp_calls_per_run", "max_mcp_calls_per_day",
                  "contested_seeds", "rest_max_dois_per_request")
_NON_NEGATIVE_INTS = ("max_followups_per_round",)
_KEYS = frozenset({"token_env", "token_file", "allowed_tools", "rest_rate_per_s",
                   "cache_ttl_days", *_POSITIVE_INTS, *_NON_NEGATIVE_INTS})
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}\Z")
_SHOWABLE = re.compile(r"^[a-z_][a-z0-9_.]{0,31}\Z")


class PolicyError(Exception):
    """Refused by config/scite.yml (or its absence). A boundary, not a failure."""

    code = "REFUSED_POLICY"


def exit_code(exc: BaseException) -> int:
    """CLI exit code for a refusal (3, like nblm) — 1 for anything else."""
    return EXIT_REFUSED_POLICY if isinstance(exc, PolicyError) else 1


@dataclass(frozen=True)
class Policy:
    enabled: bool
    tier: str                      # off | public | mcp
    reason: str | None = None      # why scite (or its MCP tier) is unavailable
    token_env: str | None = "SCITE_TOKEN"
    token_file: str | None = None
    token_present: bool = False
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS
    max_mcp_calls_per_round: int = 5
    max_mcp_calls_per_run: int = 20
    max_mcp_calls_per_day: int | None = None
    max_followups_per_round: int = 2
    contested_seeds: int = 10
    rest_rate_per_s: float = 5
    rest_max_dois_per_request: int = 500
    cache_ttl_days: dict = field(default_factory=lambda: {"metadata": 30, "search": 7})
    path: str | None = None

    @classmethod
    def disabled(cls, reason: str) -> "Policy":
        return cls(enabled=False, tier="off", reason=reason)


def _show(value) -> str:
    """Echo a bad value only when it cannot be a pasted secret."""
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if isinstance(value, str) and _SHOWABLE.match(value):
        return repr(value)
    return f"<{type(value).__name__} hidden>"


def _int(key: str, value, minimum: int):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PolicyError(f"{key} must be an integer >= {minimum}, got {_show(value)}")
    return value


def _validate(data) -> dict:
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise PolicyError(f"{CONFIG} must be a mapping of settings")
    unknown = sorted(_show(k) for k in data if k not in _KEYS)
    if unknown:
        raise PolicyError(f"{CONFIG}: unknown key(s) {', '.join(unknown)} "
                          f"(allowed: {', '.join(sorted(_KEYS))})")
    out = dict(_DEFAULTS)
    for key in _POSITIVE_INTS:
        if key in data:
            out[key] = None if (key == "max_mcp_calls_per_day" and data[key] is None) \
                else _int(key, data[key], 1)
    for key in _NON_NEGATIVE_INTS:
        if key in data:
            out[key] = _int(key, data[key], 0)
    if "rest_rate_per_s" in data:
        v = data["rest_rate_per_s"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise PolicyError(f"rest_rate_per_s must be a number > 0, got {_show(v)}")
        out["rest_rate_per_s"] = v

    tools = data.get("allowed_tools", list(DEFAULT_TOOLS))
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise PolicyError("allowed_tools must be a list of tool names")
    bad = [t for t in tools if t not in KNOWN_TOOLS]
    if bad:
        raise PolicyError(f"allowed_tools: {', '.join(_show(t) for t in bad)} not allowed "
                          f"(scite retrieval tools: {', '.join(KNOWN_TOOLS)})")
    out["allowed_tools"] = tuple(dict.fromkeys(tools))

    ttl = data.get("cache_ttl_days", {})
    if not isinstance(ttl, Mapping):
        raise PolicyError("cache_ttl_days must be a mapping (metadata, search)")
    unknown = sorted(_show(k) for k in ttl if k not in TTL_CLASSES)
    if unknown:
        raise PolicyError(f"cache_ttl_days: unknown key(s) {', '.join(unknown)} "
                          f"(allowed: {', '.join(TTL_CLASSES)})")
    out["cache_ttl_days"] = {"metadata": 30, "search": 7}
    for k, v in ttl.items():
        out["cache_ttl_days"][k] = _int(f"cache_ttl_days.{k}", v, 0)

    if "token_env" in data and "token_file" in data:
        raise PolicyError("set token_env or token_file, not both")
    if "token_file" in data:
        tf = data["token_file"]
        if not isinstance(tf, str) or not Path(tf).expanduser().is_absolute():
            # never echoed: a pasted token is the likeliest mistake here
            raise PolicyError("token_file must be an absolute path to a file holding the token")
        out["token_file"], out["token_env"] = tf, None
    elif "token_env" in data:
        te = data["token_env"]
        if not isinstance(te, str) or not _ENV_NAME.match(te):
            raise PolicyError("token_env must be the NAME of an environment variable "
                              "(A-Z, 0-9, _; at most 64 characters), not the token")
        out["token_env"] = te
    return out


def _token_present(cfg: dict, environ: Mapping[str, str]) -> bool:
    """Presence only: the env var is set and non-blank, or the file exists."""
    if cfg.get("token_file"):
        return Path(cfg["token_file"]).expanduser().is_file()
    return bool((environ.get(cfg["token_env"]) or "").strip())


def load(root: Path, environ: Mapping[str, str] | None = None) -> Policy:
    """The policy for the project at `root`. Absent file → `Policy.disabled`."""
    path = Path(root) / CONFIG
    if not path.exists():
        return Policy.disabled(f"scite is off: {CONFIG} not found "
                               f"(copy config/scite.example.yml to enable it)")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"{CONFIG} is not readable ({type(exc).__name__})") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # the parser's message quotes the offending line — report the position only
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise PolicyError(f"{CONFIG} is not valid YAML{where}") from None
    cfg = _validate(data)
    present = _token_present(cfg, os.environ if environ is None else environ)
    where = (f"token_file {cfg['token_file']}" if cfg.get("token_file")
             else f"${cfg['token_env']}")
    return Policy(
        enabled=True,
        tier="mcp" if present else "public",
        reason=None if present else f"scite MCP unavailable: no token in {where} "
                                    f"(public REST only)",
        token_present=present,
        path=str(path),
        **cfg,
    )


def check_tool(policy: Policy, tool: str) -> None:
    """Raise unless core may call this scite MCP tool now."""
    if not policy.enabled:
        raise PolicyError(policy.reason or "scite is off")
    if tool not in policy.allowed_tools:
        raise PolicyError(f"scite tool '{tool}' not in allowed_tools "
                          f"(allowed: {', '.join(policy.allowed_tools) or 'none'})")
    if not policy.token_present:
        raise PolicyError(policy.reason or "scite MCP token missing")


def check_rest(policy: Policy) -> None:
    """Raise unless the public REST tier may be used (needs no token)."""
    if not policy.enabled:
        raise PolicyError(policy.reason or "scite is off")


def describe(policy: Policy) -> dict:
    """Safe summary for status output: names and flags, never the token value."""
    token = ({"source": "file", "name": policy.token_file, "present": policy.token_present}
             if policy.token_file else
             {"source": "env", "name": policy.token_env, "present": policy.token_present})
    return {
        "enabled": policy.enabled,
        "tier": policy.tier,
        "reason": policy.reason,
        "allowed_tools": list(policy.allowed_tools) if policy.enabled else [],
        "budgets": {"round": policy.max_mcp_calls_per_round,
                    "run": policy.max_mcp_calls_per_run,
                    "day": policy.max_mcp_calls_per_day},
        "token": token,
    }
