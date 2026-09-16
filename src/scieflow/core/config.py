"""ScieFlow configuration: agent registry, defaults, per-run overrides."""

from pathlib import Path

import yaml


def _find_root(start: Path) -> Path | None:
    p = start.resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "config" / "agents.yml").exists():
            return candidate
    return None


def repo_root(start: Path | None = None) -> Path:
    """The ScieFlow repo root: nearest ancestor holding config/agents.yml.

    With an explicit `start`, only that path is searched. Otherwise the current
    working directory wins (so a command run inside a repo uses that repo's
    config), falling back to the installed package's own checkout.
    """
    if start is not None:
        found = _find_root(start)
        if found is None:
            raise FileNotFoundError(f"config/agents.yml not found above {start}")
        return found
    for origin in (Path.cwd(), Path(__file__)):
        found = _find_root(origin)
        if found is not None:
            return found
    raise FileNotFoundError(
        f"config/agents.yml not found above {Path.cwd()} or {Path(__file__)}"
    )


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
