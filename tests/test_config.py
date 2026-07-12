from pathlib import Path

import pytest
import yaml

from sflib import config

ROOT = Path(__file__).resolve().parents[1]


def test_repo_root_finds_scieflow_root():
    assert config.repo_root(ROOT / "scripts") == ROOT


def test_repo_root_raises_outside_repo(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.repo_root(tmp_path)


def test_agents_registry_is_fable_only():
    agents = config.load_agents(ROOT)
    assert agents["claude"]["model"] == "claude-fable-5"
    assert agents["claude"]["enabled"] is True
    assert agents["stub"]["enabled"] is False
    assert set(agents) == {"claude", "codex", "agy", "stub"}


def test_load_run_config_merges_workspace_overrides(tmp_path):
    ws = tmp_path / "run"
    ws.mkdir()
    (ws / "config.yml").write_text(yaml.safe_dump({"max_iterations": 2}))
    cfg = config.load_run_config(ws, ROOT)
    assert cfg["max_iterations"] == 2
    assert cfg["approval"] == "per-campaign"  # untouched default


def test_real_registry_tiers():
    root = config.repo_root()
    agents = config.load_agents(root)
    assert agents["claude"]["tier"] == "primary"
    assert agents["codex"]["tier"] == "primary"
    assert agents["codex"]["model"] == "gpt-5.6-sol"
    assert agents["agy"]["tier"] == "support"


def test_tier_agents_filters_enabled_only():
    agents = {
        "claude": {"tier": "primary", "enabled": True},
        "codex": {"tier": "primary", "enabled": False},
        "agy": {"tier": "support", "enabled": True},
    }
    assert config.tier_agents(agents, "primary") == ["claude"]
    assert config.tier_agents(agents, "support") == ["agy"]
