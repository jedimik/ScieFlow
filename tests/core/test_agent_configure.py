from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import agent_config as ac
from scieflow.core import agent_configure as acf
from scieflow.core.cli import agent

ANNOTATED_DEFAULTS_TAIL = """
# Which agent performs each role.
assignments:
  loop.experiment: claude   # experiments
  loop.literature: claude
  loop.paper-draft: claude
  research.search: [claude, codex, agy]
  research.cross-review: [claude, codex]
  research.gap-analysis: [claude, codex]
  research.debate: [claude, codex]
  research.journal-profile: [agy, claude]
  research.reviewer: codex
  research.submitter: claude
  research.outline: claude
  research.draft-authors: [claude, codex]
  research.consistency: codex
"""


def configure(*args):
    return CliRunner().invoke(agent, ["configure", *args])


def ws_config(repo: Path, slug: str = "run-1") -> dict:
    path = repo / "workspace" / slug / "config.yml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def test_workspace_assign_and_set_store_only_differences(repo):
    defaults_before = (repo / "config" / "defaults.yml").read_text()
    registry_before = (repo / "config" / "agents.yml").read_text()

    result = configure("--workspace", "run-1", "--assign", "loop.experiment=codex",
                       "--set", "codex.reasoning=high", "--yes")

    assert result.exit_code == 0, result.output
    assert ws_config(repo) == {"assignments": {"loop.experiment": "codex"},
                               "agent_overrides": {"codex": {"reasoning": "high"}}}
    assert (repo / "config" / "defaults.yml").read_text() == defaults_before
    assert (repo / "config" / "agents.yml").read_text() == registry_before
    eff = ac.resolve(repo, "run-1")
    assert eff.assignments["loop.experiment"].source == "workspace"
    assert eff.assignments["loop.literature"].source == "default"


def test_setting_a_workspace_value_back_to_default_removes_the_override(repo):
    configure("--workspace", "run-1", "--assign", "loop.experiment=codex",
              "--set", "codex.reasoning=high", "--yes")
    result = configure("--workspace", "run-1", "--assign", "loop.experiment=claude",
                       "--set", "codex.reasoning=medium", "--yes")
    assert result.exit_code == 0, result.output
    assert ws_config(repo) == {}


def test_workspace_keeps_unrelated_keys_and_their_layout(repo):
    path = repo / "workspace" / "run-1" / "config.yml"
    original = "approval: per-campaign\n# budget\nmax_iterations: 3\nagents:\n- claude\n- codex\n"
    path.write_text(original)
    result = configure("--workspace", "run-1", "--set", "claude.timeout_min=90", "--yes")
    assert result.exit_code == 0, result.output
    assert path.read_text().startswith(original)
    assert ws_config(repo)["agent_overrides"] == {"claude": {"timeout_min": 90}}


def test_unset_removes_one_workspace_override(repo, write_ws):
    write_ws(repo, {"assignments": {"loop.experiment": "codex"},
                    "agent_overrides": {"codex": {"reasoning": "high", "timeout_min": 20}}})
    result = configure("--workspace", "run-1", "--unset", "loop.experiment",
                       "--unset", "codex.reasoning", "--yes")
    assert result.exit_code == 0, result.output
    assert ws_config(repo) == {"agent_overrides": {"codex": {"timeout_min": 20}}}


def test_legacy_role_key_forces_an_explicit_assignment(repo, write_ws):
    write_ws(repo, {"reviewer": "claude", "submitter": "codex"})
    result = configure("--workspace", "run-1", "--assign", "research.reviewer=codex",
                       "--assign", "research.submitter=claude", "--yes")
    assert result.exit_code == 0, result.output
    data = ws_config(repo)
    assert data["assignments"] == {"research.reviewer": "codex", "research.submitter": "claude"}
    assert ac.resolve(repo, "run-1").value("research.reviewer") == "codex"


@pytest.mark.parametrize("args, fragment", [
    (["--assign", "research.reviewer=agy"], "primary-only"),
    (["--assign", "research.search=agy"], "paired with a primary"),
    (["--assign", "loop.bogus=claude"], "unknown role"),
    (["--set", "codex.enabled=false"], "enabled is global"),
    (["--set", "ghost.model=x"], "unknown agent"),
    (["--set", "codex.timeout_min=soon"], "must be a number"),
    (["--set", "codex.timeout_min=0"], "positive number"),
    (["--assign", "loop.experiment=claude,codex"], "exactly one agent"),
])
def test_invalid_workspace_changes_are_refused_and_nothing_written(repo, args, fragment):
    result = configure("--workspace", "run-1", *args, "--yes")
    assert result.exit_code != 0
    assert fragment in result.output
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


def test_non_interactive_without_yes_writes_nothing(repo):
    result = configure("--workspace", "run-1", "--assign", "loop.experiment=codex")
    assert result.exit_code != 0
    assert "--yes" in result.output
    assert "+  loop.experiment: codex" in result.output
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


def test_missing_status_yml_is_noted(repo):
    result = configure("--workspace", "run-1", "--set", "claude.model=opus", "--yes")
    assert result.exit_code == 0, result.output
    assert "no status.yml yet" in result.output


def test_default_changes_preserve_comments(repo):
    defaults_path = repo / "config" / "defaults.yml"
    defaults_path.write_text("# loop defaults\narchive: false  # zips\n" + ANNOTATED_DEFAULTS_TAIL)
    agents_path = repo / "config" / "agents.yml"
    agents_path.write_text("# registry header\n" + agents_path.read_text())
    before_defaults, before_agents = defaults_path.read_text(), agents_path.read_text()

    result = configure("--assign", "loop.experiment=codex", "--set", "codex.model=sol-2", "--yes")

    assert result.exit_code == 0, result.output
    after_defaults = defaults_path.read_text()
    changed = [(a, b) for a, b in zip(before_defaults.splitlines(), after_defaults.splitlines()) if a != b]
    assert len(changed) == 1
    assert changed[0][1].split("#")[0].strip() == "loop.experiment: codex"
    assert changed[0][1].rstrip().endswith("# experiments")
    after_agents = agents_path.read_text()
    assert after_agents.startswith("# registry header\n")
    assert yaml.safe_load(after_agents)["agents"]["codex"]["model"] == "sol-2"
    assert len(after_agents.splitlines()) == len(before_agents.splitlines())


def test_default_fan_out_assignment_is_written_as_flow_list(repo):
    result = configure("--assign", "research.debate=codex,claude", "--yes")
    assert result.exit_code == 0, result.output
    assert yaml.safe_load((repo / "config" / "defaults.yml").read_text())[
        "assignments"]["research.debate"] == ["codex", "claude"]


def test_defaults_refuse_unsetting_a_role_and_disabling_an_assigned_agent(repo):
    result = configure("--unset", "loop.experiment", "--yes")
    assert result.exit_code != 0 and "must assign every role" in result.output
    result = configure("--set", "codex.enabled=false", "--yes")
    assert result.exit_code != 0 and "is disabled" in result.output


def test_repeating_the_current_value_writes_nothing(repo):
    result = configure("--assign", "loop.experiment=claude", "--yes")
    assert result.exit_code == 0, result.output
    assert "nothing to write" in result.output


def test_wizard_assigns_a_role_in_a_workspace(repo):
    answers = "\n".join([
        "roles",               # what to change
        "loop.experiment",     # role
        "codex",               # agent
        "agent",               # next: an agent setting
        "codex",               # agent
        "reasoning",           # setting
        "high",                # value
        "done",
        "y",                   # write?
    ]) + "\n"
    result = CliRunner().invoke(agent, ["configure", "--workspace", "run-1"], input=answers)
    assert result.exit_code == 0, result.output
    assert "queued: assign loop.experiment = codex" in result.output
    assert ws_config(repo) == {"assignments": {"loop.experiment": "codex"},
                               "agent_overrides": {"codex": {"reasoning": "high"}}}


def test_wizard_rejects_invalid_choice_and_can_decline(repo):
    answers = "\n".join(["roles", "research.reviewer", "agy", "done", "n"]) + "\n"
    result = CliRunner().invoke(agent, ["configure", "--workspace", "run-1"], input=answers)
    assert result.exit_code == 0, result.output
    assert "not applied" in result.output and "primary-only" in result.output
    assert "nothing to change" in result.output
    assert not (repo / "workspace" / "run-1" / "config.yml").exists()


def test_wizard_picks_target_and_declines_writing(repo):
    answers = "\n".join(["default", "roles", "loop.literature", "codex", "done", "n"]) + "\n"
    before = (repo / "config" / "defaults.yml").read_text()
    result = CliRunner().invoke(agent, ["configure"], input=answers)
    assert result.exit_code == 0, result.output
    assert "not written" in result.output
    assert (repo / "config" / "defaults.yml").read_text() == before


def test_plan_workspace_unknown_slug(repo):
    with pytest.raises(acf.ConfigureError, match="no workspace"):
        acf.plan_workspace(repo, "missing", [acf.Op("assign", "loop.experiment", "codex")])


def test_pyyaml_wrapped_file_keeps_untouched_top_level_blocks_verbatim(repo):
    long_scope = "private manuscript, prepared result package, processing descriptions, drafts and reviews"
    data = {
        "approval": "per-campaign",
        "agents": ["codex", "claude"],
        "agent_overrides": {"codex": {"timeout_min": 20}},
        "external_sharing": {"codex": {"approved": True, "scope": long_scope}},
    }
    path = repo / "workspace" / "run-1" / "config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    original = path.read_text()
    assert "\n      " in original  # PyYAML wrapped the long scope at 80 columns

    result = configure("--workspace", "run-1", "--set", "codex.timeout_min=25", "--yes")

    assert result.exit_code == 0, result.output
    after = path.read_text()
    sharing_block = original[original.index("external_sharing:"):]
    assert sharing_block in after
    assert after.startswith("approval: per-campaign\nagents:\n- codex\n- claude\n")
    assert yaml.safe_load(after) == {**data, "agent_overrides": {"codex": {"timeout_min": 25}}}
