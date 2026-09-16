"""Research-module run configuration on top of the shared core registry.

Workflow defaults live under `research:` in config/defaults.yml; a run's
workspace/<slug>/config.yml overrides them at top level and may narrow the
participating agents with `agents: [...]`.
"""

from pathlib import Path

import yaml

from scieflow.core.config import load_agents, repo_root  # noqa: F401  (re-exported)


def research_defaults(root: Path) -> dict:
    path = root / "config" / "defaults.yml"
    if not path.exists():
        return {}
    return dict((yaml.safe_load(path.read_text()) or {}).get("research") or {})


def load_workspace(ws: Path, root: Path) -> dict:
    """Merged run config: registry agents + research defaults + config.yml."""
    agents = load_agents(root)
    merged = {
        "agents": agents,
        "defaults": research_defaults(root),
        "run_agents": [n for n, a in agents.items() if a.get("enabled")],
    }
    ws_file = ws / "config.yml"
    if ws_file.exists():
        override = yaml.safe_load(ws_file.read_text()) or {}
        if "agents" in override:
            merged["run_agents"] = list(override.pop("agents"))
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged["defaults"].get(key), dict):
                merged["defaults"][key] = {**merged["defaults"][key], **value}
            else:
                merged["defaults"][key] = value
    return merged


def tier_agents(merged: dict, tier: str) -> list[str]:
    """Run-set agent names whose registry entry declares this tier.

    Registry tiers are authoritative: a workspace config.yml can narrow the
    run set but can never re-tier an agent (root AGENTS.md rule 10).
    """
    return [
        name
        for name in merged["run_agents"]
        if merged["agents"].get(name, {}).get("tier") == tier
    ]


def agent_overrides(ws: Path) -> dict:
    """Per-agent override blocks from a workspace config.yml `agent_overrides:`."""
    ws_file = ws / "config.yml"
    if not ws_file.exists():
        return {}
    data = yaml.safe_load(ws_file.read_text()) or {}
    return data.get("agent_overrides") or {}


def zotero_target(merged: dict) -> dict:
    z = dict(merged["defaults"].get("zotero", {}))
    return {"library": z.get("library", "user"), "collection": z.get("collection")}
