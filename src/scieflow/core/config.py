"""Load ScieFlow configuration: agent registry, loop defaults, per-run overrides."""

from pathlib import Path

import yaml


def repo_root(start: Path | None = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "config" / "agents.yml").exists():
            return candidate
    raise FileNotFoundError(f"config/agents.yml not found above {p}")


def load_agents(root: Path) -> dict:
    return yaml.safe_load((root / "config" / "agents.yml").read_text())["agents"]


def load_defaults(root: Path) -> dict:
    return yaml.safe_load((root / "config" / "defaults.yml").read_text())


def load_run_config(ws: Path, root: Path) -> dict:
    """Loop defaults overlaid with the workspace's config.yml (flat merge)."""
    merged = dict(load_defaults(root))
    ws_file = ws / "config.yml"
    if ws_file.exists():
        merged.update(yaml.safe_load(ws_file.read_text()) or {})
    return merged


def tier_agents(agents: dict, tier: str) -> list[str]:
    """Enabled agent names declaring this tier (AGENTS.md rule 10)."""
    return [
        name
        for name, entry in agents.items()
        if entry.get("enabled") and entry.get("tier") == tier
    ]
