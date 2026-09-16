from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def test_new_defaults_present():
    cfg = yaml.safe_load((REPO / "config" / "defaults.yml").read_text())
    d = cfg["research"]
    assert d["max_debate_rounds"] == 2
    assert d["max_gaps"] == 10
    assert d["max_hypotheses"] == 5
    assert d["auto_approve_outline"] is False


def test_agents_md_has_provenance_rule_and_workflows():
    text = (REPO / "src" / "scieflow" / "research" / "AGENTS.md").read_text()
    assert "manifest.yml" in text
    assert "[data:" in text
    assert "skills/gap-discovery/SKILL.md" in text
    assert "skills/paper-draft/SKILL.md" in text
