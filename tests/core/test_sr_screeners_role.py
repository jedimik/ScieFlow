"""M-H t4 (plan 5.6, Part B t4 item 1): the research.sr-screeners role.

Two distinct primary agents, fan-out, never a support-tier agent. Synthetic configuration only."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from scieflow.core import agent_config as ac
from scieflow.core.cli import agent

REPO = Path(__file__).resolve().parents[2]
ROLE = "research.sr-screeners"


def test_role_is_a_fan_out_role_that_is_primary_only():
    spec = ac.ROLES[ROLE]
    assert spec.many is True
    assert spec.support_ok is False


def test_the_shipped_default_names_codex_and_claude():
    eff = ac.resolve(REPO)
    assert eff.problems == []
    assert eff.value(ROLE) == ["codex", "claude"]
    assert eff.assignments[ROLE].source == "default"


def test_a_support_agent_is_refused_for_the_role():
    registry = yaml.safe_load((REPO / "config" / "agents.yml").read_text())
    defaults = yaml.safe_load((REPO / "config" / "defaults.yml").read_text())
    eff = ac.resolve_data(registry, defaults, {"assignments": {ROLE: ["codex", "agy"]}})
    assert any(ROLE in p and "agy" in p for p in eff.problems), eff.problems


def test_configure_accepts_two_primary_agents_and_refuses_a_support_agent(repo):
    ok = CliRunner().invoke(agent, ["configure", "--workspace", "run-1",
                                    "--assign", f"{ROLE}=codex,claude", "--yes"])
    assert ok.exit_code == 0, ok.output
    assert ac.resolve(repo, "run-1").value(ROLE) == ["codex", "claude"]

    bad = CliRunner().invoke(agent, ["configure", "--workspace", "run-1",
                                     "--assign", f"{ROLE}=codex,agy", "--yes"])
    assert bad.exit_code != 0
    assert "support" in bad.output
