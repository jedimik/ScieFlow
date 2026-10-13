"""scite readiness report (`scieflow workspace doctor`, `scieflow research scite status`).

Read-only. Reports:

- `configured` / `tier` / `reason` — from `config/scite.yml` (`policy.load`); an
  invalid file is reported, not raised.
- `sdk_installed` — the optional `scite` dependency group (the `mcp` SDK).
- `token_in_env` — the token variable (`token_env`, and always `SCITE_TOKEN`) is set
  in *this* process's environment. A warning: every dispatch ScieFlow starts from
  this shell inherits it, so a sub-agent could read it. Prefer `token_file`.
- `harness_registrations` — dispatch-harness configs that register a scite MCP
  server: `~/.claude.json` (top level and per project), `~/.codex/config.toml`,
  Hermes `config.yaml` (`$HERMES_HOME` or `~/.hermes`, plus its profiles), and
  the project-scoped `<root>/.mcp.json` and `<root>/.codex/config.toml` (these
  reach every dispatch started in the project). Only
  the harness, the file and the server *name* are reported; values (URLs,
  headers, tokens) are never copied into the report. A scite registration is
  for interactive coordinator chats only — dispatches must not reach scite.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

import yaml

from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite import policy as _policy

_MARK = "scite"


def _is_scite(name, cfg) -> bool:
    """A server entry is scite's when its name or any string value mentions scite."""
    if _MARK in str(name).lower():
        return True
    stack = [cfg]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str) and _MARK in cur.lower():
            return True
        if isinstance(cur, Mapping):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def _servers(table) -> list[str]:
    if not isinstance(table, Mapping):
        return []
    return [str(k) for k, v in table.items() if _is_scite(k, v)]


def _claude(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return []
    names = _servers(data.get("mcpServers"))
    projects = data.get("projects")
    for proj in (projects.values() if isinstance(projects, Mapping) else []):
        if isinstance(proj, Mapping):
            names += [n for n in _servers(proj.get("mcpServers")) if n not in names]
    return names


def _codex(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _servers(data.get("mcp_servers"))


def _hermes(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _servers(data.get("mcp_servers")) if isinstance(data, Mapping) else []


def registrations(home: Path, environ: Mapping[str, str], root: Path | None = None) -> list[dict]:
    home = Path(home)
    hermes_home = Path(environ.get("HERMES_HOME") or home / ".hermes")
    candidates = [("claude", home / ".claude.json", _claude),
                  ("codex", home / ".codex" / "config.toml", _codex),
                  ("hermes", hermes_home / "config.yaml", _hermes)]
    if root is not None:
        # project-scoped registrations reach every dispatch started in this project
        candidates += [("claude", Path(root) / ".mcp.json", _claude),
                       ("codex", Path(root) / ".codex" / "config.toml", _codex)]
    profiles = hermes_home / "profiles"
    if profiles.is_dir():
        candidates += [("hermes", p / "config.yaml", _hermes)
                       for p in sorted(profiles.iterdir()) if p.is_dir()]
    out = []
    for harness, path, read in candidates:
        if not path.is_file():
            continue
        try:
            names = read(path)
        except (OSError, ValueError, UnicodeDecodeError, yaml.YAMLError, tomllib.TOMLDecodeError):
            continue
        for name in names:
            out.append({"harness": harness, "file": str(path), "server": name})
    return out


def report(root: Path, environ: Mapping[str, str] | None = None,
           home: Path | None = None) -> dict:
    env = os.environ if environ is None else environ
    home = Path(home) if home is not None else Path(env.get("HOME") or Path.home())
    try:
        pol = _policy.load(Path(root), env)
        reason = pol.reason
    except _policy.PolicyError as exc:
        pol, reason = None, str(exc)
    configured = bool(pol and pol.enabled)
    token_env = (pol.token_env if pol and pol.token_env else "SCITE_TOKEN")
    names = list(dict.fromkeys([token_env, "SCITE_TOKEN"]))
    exposed = [n for n in names if (env.get(n) or "").strip()]
    token_in_env = bool(exposed)
    regs = registrations(home, env, Path(root))
    warnings = []
    for name in exposed:
        warnings.append(f"${name} is set in the ScieFlow process environment: every "
                        "dispatch inherits it. Prefer token_file in config/scite.yml and unset "
                        "the variable.")
    for r in regs:
        warnings.append(f"{r['harness']} config {r['file']} registers a scite MCP server "
                        f"'{r['server']}': coordinator chats only — never use it in dispatches.")
    return {
        "configured": configured,
        "tier": pol.tier if configured and pol else None,
        "reason": reason,
        "sdk_installed": smcp.sdk_available(),
        "install_hint": smcp.INSTALL_HINT,
        "token_env": token_env,
        "token_in_env": token_in_env,
        "harness_registrations": regs,
        "warnings": warnings,
    }
