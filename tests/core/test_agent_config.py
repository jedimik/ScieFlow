import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import agent_config as ac

def test_real_repo_defaults_are_complete_and_valid():
    eff = ac.resolve(Path(__file__).resolve().parents[2])
    assert eff.problems == []
    assert set(eff.assignments) == set(ac.ROLES)


def test_defaults_resolve_with_default_source(repo):
    eff = ac.resolve(repo)
    assert eff.problems == []
    assert eff.value("loop.experiment") == "claude"
    assert eff.assignments["loop.experiment"].source == "default"
    assert eff.agents["codex"]["reasoning"].value == "medium"


def test_workspace_overrides_win_and_rest_inherits(repo, write_ws):
    write_ws(repo, {"assignments": {"loop.experiment": "codex"},
                    "agent_overrides": {"codex": {"reasoning": "high"}}})
    eff = ac.resolve(repo, "run-1")
    assert eff.problems == []
    assert (eff.value("loop.experiment"), eff.assignments["loop.experiment"].source) == ("codex", "workspace")
    assert eff.assignments["loop.literature"].source == "default"
    assert eff.agents["codex"]["reasoning"].value == "high"
    assert eff.agents["codex"]["reasoning"].source == "workspace"
    assert eff.agents["codex"]["model"].source == "default"


def test_legacy_research_keys_are_read(repo, write_ws):
    write_ws(repo, {"agents": ["claude", "codex"], "reviewer": "claude", "submitter": "codex"})
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.search") == ["claude", "codex"]
    assert eff.value("research.reviewer") == "claude"
    assert eff.assignments["research.reviewer"].source == ac.LEGACY
    assert eff.problems == []


def test_new_assignments_beat_legacy_keys(repo, write_ws):
    write_ws(repo, {"reviewer": "claude", "submitter": "codex",
                    "assignments": {"research.reviewer": "codex", "research.submitter": "claude"}})
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.reviewer") == "codex"


@pytest.mark.parametrize("assignments, fragment", [
    ({"research.reviewer": "agy"}, "primary-only"),
    ({"research.search": ["agy"]}, "paired with a primary"),
    ({"loop.experiment": "off"}, "disabled"),
    ({"loop.experiment": "ghost"}, "unknown agent"),
    ({"loop.experiment": ["claude"]}, "exactly one agent"),
    ({"research.debate": []}, "non-empty list"),
    ({"research.debate": ["claude", "claude"]}, "listed twice"),
    ({"research.submitter": "codex"}, "must be different"),
    ({"loop.nonsense": "claude"}, "unknown role"),
])
def test_validation_refusals(repo, write_ws, assignments, fragment):
    write_ws(repo, {"assignments": assignments})
    problems = ac.resolve(repo, "run-1").problems
    assert any(fragment in p for p in problems), problems


def test_reasoning_label_and_unlisted_level_only_warn(repo, write_ws):
    write_ws(repo, {"agent_overrides": {"claude": {"reasoning": "max"},
                                        "codex": {"reasoning": "ultra"}}})
    eff = ac.resolve(repo, "run-1")
    assert eff.problems == []
    assert any("only a label" in w for w in eff.warnings)
    assert any("'ultra' is not in the menu" in w for w in eff.warnings)


def test_legacy_run_set_never_empties_an_unused_role(repo, write_ws):
    write_ws(repo, {"agents": ["codex"]})
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.search") == ["codex"]
    assert eff.value("research.journal-profile") == ["agy", "claude"]
    assert eff.assignments["research.journal-profile"].source == "default"


@pytest.mark.parametrize("overrides, fragment", [
    ({"codex": {"timeout_min": 0}}, "positive number"),
    ({"codex": {"model": ""}}, "non-empty string"),
    ({"ghost": {"model": "x"}}, "unknown agent"),
])
def test_agent_setting_refusals(repo, write_ws, overrides, fragment):
    write_ws(repo, {"agent_overrides": overrides})
    problems = ac.resolve(repo, "run-1").problems
    assert any(fragment in p for p in problems), problems


def test_missing_default_role_is_reported(repo, default_assignments):
    doc = {"assignments": {k: v for k, v in default_assignments.items() if k != "loop.literature"}}
    (repo / "config" / "defaults.yml").write_text(yaml.safe_dump(doc))
    assert "role loop.literature: no agent assigned" in ac.resolve(repo).problems


def test_show_cli_json_and_exit_code(repo, write_ws):
    from scieflow.core.cli import agent

    result = CliRunner().invoke(agent, ["show", "--workspace", "run-1", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["assignments"]["loop.experiment"] == {"value": "claude", "source": "default"}
    assert data["agents"]["agy"]["tier"]["value"] == "support"

    write_ws(repo, {"assignments": {"research.reviewer": "agy"}})
    result = CliRunner().invoke(agent, ["show", "--workspace", "run-1"])
    assert result.exit_code == 1
    assert "primary-only" in result.output


def test_show_cli_unknown_workspace(repo):
    from scieflow.core.cli import agent

    result = CliRunner().invoke(agent, ["show", "--workspace", "nope"])
    assert result.exit_code != 0
    assert "no workspace" in result.output
