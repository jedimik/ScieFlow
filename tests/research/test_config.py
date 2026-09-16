from pathlib import Path

import pytest
from scieflow.research import config

REPO = Path(__file__).resolve().parents[2]


def make_root(tmp_path: Path, workspace_cfg: str | None = None) -> tuple[Path, Path]:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "defaults.yml").write_text(
        "research:\n  max_papers: 40\n  zotero:\n    library: user\n"
    )
    (tmp_path / "config" / "agents.yml").write_text(
        """
agents:
  claude: {cmd: "claude -p {prompt}", enabled: true, tier: primary}
  codex: {cmd: "codex exec {prompt}", enabled: true, tier: primary}
  agy: {cmd: "agy --print {prompt}", enabled: true, tier: support}
  stub: {cmd: "python stub.py {prompt}", enabled: false, tier: primary}
"""
    )
    ws = tmp_path / "workspace" / "2026-07-test"
    ws.mkdir(parents=True)
    if workspace_cfg is not None:
        (ws / "config.yml").write_text(workspace_cfg)
    return tmp_path, ws


def test_repo_root_finds_real_repo():
    assert config.repo_root(REPO / "src") == REPO


def test_repo_root_raises_outside_repo(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.repo_root(tmp_path)


def test_load_agents(tmp_path):
    root, _ = make_root(tmp_path)
    agents = config.load_agents(root)
    assert set(agents) == {"claude", "codex", "agy", "stub"}
    assert agents["claude"]["enabled"] is True


def test_load_workspace_defaults_to_enabled_agents(tmp_path):
    root, ws = make_root(tmp_path)
    merged = config.load_workspace(ws, root)
    assert merged["run_agents"] == ["claude", "codex", "agy"]
    assert merged["defaults"]["max_papers"] == 40


def test_workspace_overrides_win(tmp_path):
    root, ws = make_root(
        tmp_path,
        "agents: [claude]\nmax_papers: 10\nzotero:\n  library: group:123\n  collection: ScieFlow/test\n",
    )
    merged = config.load_workspace(ws, root)
    assert merged["run_agents"] == ["claude"]
    assert merged["defaults"]["max_papers"] == 10
    assert config.zotero_target(merged) == {"library": "group:123", "collection": "ScieFlow/test"}


def test_agent_overrides_loaded(tmp_path):
    root, ws = make_root(
        tmp_path,
        "agents: [claude]\nagent_overrides:\n  claude:\n    model: claude-opus-4-8\n    reasoning: high\n",
    )
    overrides = config.agent_overrides(ws)
    assert overrides["claude"] == {"model": "claude-opus-4-8", "reasoning": "high"}


def test_agent_overrides_empty_without_config(tmp_path):
    root, ws = make_root(tmp_path)
    assert config.agent_overrides(ws) == {}


def test_zotero_target_defaults(tmp_path):
    root, ws = make_root(tmp_path)
    merged = config.load_workspace(ws, root)
    assert config.zotero_target(merged)["library"] == "user"


def test_tier_agents_filters_run_set(tmp_path):
    root, ws = make_root(tmp_path)
    merged = config.load_workspace(ws, root)
    assert config.tier_agents(merged, "primary") == ["claude", "codex"]
    assert config.tier_agents(merged, "support") == ["agy"]


def test_tier_agents_cannot_promote_support(tmp_path):
    # A workspace narrowing the run set to agy still yields no primary agent.
    root, ws = make_root(tmp_path, workspace_cfg="agents: [agy]\n")
    merged = config.load_workspace(ws, root)
    assert config.tier_agents(merged, "primary") == []
    assert config.tier_agents(merged, "support") == ["agy"]


def test_real_registry_declares_tiers():
    agents = config.load_agents(REPO)
    assert agents["claude"]["tier"] == "primary"
    assert agents["codex"]["tier"] == "primary"
    assert agents["codex"]["model"] == "gpt-5.6-sol"
    assert "--model {model}" in agents["codex"]["cmd"]
    assert agents["agy"]["tier"] == "support"
    assert agents["agy"]["capabilities"] == ["web-search", "large-context"]
