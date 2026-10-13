"""Per-run review settings: the `review:` block of workspace/<slug>/config.yml."""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core.project import Project
from scieflow.core.review import settings
from scieflow.core.review.cli import review

ROOT = Path(__file__).resolve().parents[3]

BUILTIN = {
    "scite": "ask",
    "reviews_per_draft": 2,
    "allow_same_family_review": False,
    "allow_self_review": False,
    "review_decisions": "human",
    "pool": None,
    "matrix_override": {},
}


def run_config(repo: Path, slug: str = "run-1") -> dict:
    path = repo / "workspace" / slug / "config.yml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def invoke(*args):
    return CliRunner().invoke(review, ["configure", *args])


# --- defaults ------------------------------------------------------------------


def test_defaults_without_any_review_block(repo):
    assert settings.load(Project(repo), "run-1") == BUILTIN


def test_shipped_defaults_carry_the_review_block():
    shipped = yaml.safe_load((ROOT / "config" / "defaults.yml").read_text())
    assert shipped["review"] == {
        "scite": "ask",
        "reviews_per_draft": 2,
        "allow_same_family_review": False,
        "allow_self_review": False,
        "review_decisions": "human",
    }
    assert settings.validate(shipped["review"]) == shipped["review"]


def test_project_defaults_then_run_block_merge_key_by_key(repo, write_ws):
    defaults_path = repo / "config" / "defaults.yml"
    defaults = yaml.safe_load(defaults_path.read_text())
    defaults["review"] = {"scite": "off", "reviews_per_draft": 3}
    defaults_path.write_text(yaml.safe_dump(defaults))
    write_ws(repo, {"review": {"reviews_per_draft": 1}})

    got = settings.load(Project(repo), "run-1")

    assert got == {**BUILTIN, "scite": "off", "reviews_per_draft": 1}


def test_load_refuses_a_hand_broken_block(repo, write_ws):
    write_ws(repo, {"review": {"scite": "sure"}})
    with pytest.raises(settings.SettingsError, match="off, ask, approved"):
        settings.load(Project(repo), "run-1")


def test_hand_written_bare_off_reads_as_off(repo):
    # PyYAML (YAML 1.1) parses a bare `off` as false.
    (repo / "workspace" / "run-1" / "config.yml").write_text("review:\n  scite: off\n")
    assert settings.load(Project(repo), "run-1")["scite"] == "off"


def test_written_off_is_a_string_for_yaml11_readers(repo):
    settings.write(Project(repo), "run-1", {"scite": "off"}, actor="human")
    assert yaml.safe_load((repo / "workspace" / "run-1" / "config.yml").read_text()) == {
        "review": {"scite": "off"}}


def test_load_unknown_workspace(repo):
    with pytest.raises(settings.SettingsError, match="no workspace"):
        settings.load(Project(repo), "nope")


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize("changes, allowed", [
    ({"scite": "maybe"}, "off, ask, approved"),
    ({"scite": True}, "off, ask, approved"),
    ({"review_decisions": "robot"}, "human, coordinator"),
    ({"allow_self_review": "yes please"}, "true, false"),
    ({"allow_same_family_review": 1}, "true, false"),
    ({"reviews_per_draft": 0}, "integer >= 1"),
    ({"reviews_per_draft": "two"}, "integer >= 1"),
    ({"reviews_per_draft": True}, "integer >= 1"),
    ({"pool": [{"agent": "codex", "covers": "some"}]}, "all, auto"),
    ({"pool": [{"agent": "codex", "extra": 1}]}, "agent, covers"),
    ({"pool": "codex"}, "list"),
    ({"pool": [{"agent": "a/b"}]}, "not a draft name"),
    ({"pool": [{"agent": "codex"}, {"agent": "codex"}]}, "more than once"),
    ({"matrix_override": ["codex"]}, "mapping"),
    ({"matrix_override": {"codex": "claude"}}, "list"),
    ({"matrix_override": {"codex": ["../x"]}}, "not a draft name"),
    ({"colour": "blue"}, "scite, reviews_per_draft"),
])
def test_invalid_values_are_refused_naming_the_allowed_set(changes, allowed):
    with pytest.raises(settings.SettingsError) as exc:
        settings.validate(changes)
    assert allowed in str(exc.value)


def test_valid_values_pass_through():
    block = {"scite": "approved", "reviews_per_draft": 3, "allow_self_review": True,
             "review_decisions": "coordinator",
             "pool": [{"agent": "codex", "covers": "all"}, {"agent": "claude"}],
             "matrix_override": {"codex": ["claude"]}}
    assert settings.validate(block) == block


# --- actor rules -----------------------------------------------------------------


@pytest.mark.parametrize("changes", [
    {"scite": "approved"},
    {"scite": "ask"},
    {"allow_same_family_review": True},
    {"allow_self_review": True},
    {"allow_self_review": False},
    {"review_decisions": "coordinator"},
    {"review_decisions": "human"},
    {"pool": [{"agent": "codex", "covers": "all"}]},
    {"matrix_override": {"codex": ["claude"]}},
])
def test_agent_actor_refused_for_human_only_settings(repo, changes):
    with pytest.raises(settings.SettingsError, match="only the user"):
        settings.write(Project(repo), "run-1", changes, actor="agent")
    assert run_config(repo) == {}


def test_agent_may_turn_scite_off(repo):
    got = settings.write(Project(repo), "run-1", {"scite": "off"}, actor="agent")
    assert got["scite"] == "off"
    assert run_config(repo) == {"review": {"scite": "off"}}


def test_agent_refusal_in_a_batch_writes_nothing(repo):
    with pytest.raises(settings.SettingsError):
        settings.write(Project(repo), "run-1",
                       {"scite": "off", "allow_self_review": True}, actor="agent")
    assert run_config(repo) == {}


def test_unknown_actor_refused(repo):
    with pytest.raises(settings.SettingsError, match="human, agent"):
        settings.write(Project(repo), "run-1", {"scite": "off"}, actor="root")


def test_human_may_set_everything(repo):
    changes = {"scite": "approved", "reviews_per_draft": 3,
               "allow_same_family_review": True, "allow_self_review": True,
               "review_decisions": "coordinator",
               "pool": [{"agent": "codex", "covers": "all"}, {"agent": "claude", "covers": "auto"}],
               "matrix_override": {"codex": ["claude"]}}
    got = settings.write(Project(repo), "run-1", changes, actor="human")
    assert got == changes
    assert run_config(repo)["review"] == changes


# --- pool against the registry ----------------------------------------------------


def test_pool_agent_must_exist(repo):
    with pytest.raises(settings.SettingsError, match="unknown agent 'ghost'"):
        settings.write(Project(repo), "run-1", {"pool": [{"agent": "ghost"}]}, actor="human")


def test_pool_agent_must_be_enabled(repo):
    with pytest.raises(settings.SettingsError, match="disabled"):
        settings.write(Project(repo), "run-1", {"pool": [{"agent": "off"}]}, actor="human")


def test_pool_agent_needs_a_model_family(repo):
    registry_path = repo / "config" / "agents.yml"
    registry = yaml.safe_load(registry_path.read_text())
    registry["agents"]["nofam"] = {"cmd": "x {prompt}", "tier": "primary", "enabled": True}
    registry_path.write_text(yaml.safe_dump(registry))
    with pytest.raises(settings.SettingsError, match="model_family"):
        settings.write(Project(repo), "run-1", {"pool": [{"agent": "nofam"}]}, actor="human")


def test_matrix_override_agents_must_exist(repo):
    with pytest.raises(settings.SettingsError, match="unknown agent 'ghost'"):
        settings.write(Project(repo), "run-1", {"matrix_override": {"codex": ["ghost"]}},
                       actor="human")


def _add_agent(repo, name, cfg):
    registry_path = repo / "config" / "agents.yml"
    registry = yaml.safe_load(registry_path.read_text())
    registry["agents"][name] = cfg
    registry_path.write_text(yaml.safe_dump(registry))


def test_matrix_override_reviewer_needs_a_model_family(repo):
    _add_agent(repo, "nofam", {"cmd": "x {prompt}", "tier": "primary", "enabled": True})
    with pytest.raises(settings.SettingsError, match="model_family"):
        settings.write(Project(repo), "run-1", {"matrix_override": {"nofam": ["claude"]}},
                       actor="human")


def test_matrix_override_reviewer_must_be_enabled(repo):
    _add_agent(repo, "gone", {"cmd": "x {prompt}", "model_family": "x", "enabled": False})
    with pytest.raises(settings.SettingsError, match="disabled"):
        settings.write(Project(repo), "run-1", {"matrix_override": {"gone": ["claude"]}},
                       actor="human")


def test_a_stale_pool_does_not_block_turning_scite_off(repo):
    project = Project(repo)
    settings.write(project, "run-1", {"pool": [{"agent": "codex"}]}, actor="human")
    registry_path = repo / "config" / "agents.yml"
    registry = yaml.safe_load(registry_path.read_text())
    registry["agents"]["codex"]["enabled"] = False
    registry_path.write_text(yaml.safe_dump(registry))

    got = settings.write(project, "run-1", {"scite": "off"}, actor="agent")

    assert got["scite"] == "off"


def test_human_write_after_a_hand_written_bare_off(repo):
    (repo / "workspace" / "run-1" / "config.yml").write_text("review:\n  scite: off\n")
    got = settings.write(Project(repo), "run-1", {"reviews_per_draft": 3}, actor="human")
    assert got["scite"] == "off" and got["reviews_per_draft"] == 3
    assert yaml.safe_load((repo / "workspace" / "run-1" / "config.yml").read_text()) == {
        "review": {"scite": "off", "reviews_per_draft": 3}}


def test_clearing_the_pool_returns_to_the_default(repo):
    project = Project(repo)
    settings.write(project, "run-1", {"pool": [{"agent": "codex"}]}, actor="human")
    got = settings.write(project, "run-1", {"pool": None}, actor="human")
    assert got["pool"] is None
    assert run_config(repo) == {}


# --- writes touch only review: ----------------------------------------------------


def test_write_touches_only_the_review_block(repo):
    path = repo / "workspace" / "run-1" / "config.yml"
    original = (
        "# run overrides\n"
        "max_iterations: 3   # keep it short\n"
        "assignments:\n"
        "  loop.experiment: codex\n"
        "review:\n"
        "  reviews_per_draft: 3\n"
        "claim_check: never\n"
    )
    path.write_text(original)

    settings.write(Project(repo), "run-1", {"scite": "off"}, actor="human")

    text = path.read_text()
    assert text.startswith("# run overrides\nmax_iterations: 3   # keep it short\n"
                           "assignments:\n  loop.experiment: codex\n")
    assert text.endswith("claim_check: never\n")
    data = yaml.safe_load(text)
    assert list(data) == ["max_iterations", "assignments", "review", "claim_check"]
    assert data["review"] == {"reviews_per_draft": 3, "scite": "off"}
    before = yaml.safe_load(original)
    assert {k: v for k, v in data.items() if k != "review"} == {
        k: v for k, v in before.items() if k != "review"}


def test_write_without_change_leaves_the_file_alone(repo, write_ws):
    write_ws(repo, {"review": {"scite": "off"}})
    path = repo / "workspace" / "run-1" / "config.yml"
    before = path.read_text()
    settings.write(Project(repo), "run-1", {"scite": "off"}, actor="human")
    assert path.read_text() == before


def test_write_refuses_a_bad_slug(repo):
    with pytest.raises(settings.SettingsError):
        settings.write(Project(repo), "../outside", {"scite": "off"}, actor="human")


def test_write_never_touches_defaults(repo):
    defaults_before = (repo / "config" / "defaults.yml").read_text()
    settings.write(Project(repo), "run-1", {"scite": "approved"}, actor="human")
    assert (repo / "config" / "defaults.yml").read_text() == defaults_before


# --- CLI -----------------------------------------------------------------------------


def test_cli_sets_values_with_yes(repo):
    result = invoke("run-1", "--set", "scite=approved", "--set", "reviews_per_draft=3",
                    "--set", "allow_self_review=true", "--yes")
    assert result.exit_code == 0, result.output
    assert run_config(repo)["review"] == {"scite": "approved", "reviews_per_draft": 3,
                                          "allow_self_review": True}


def test_cli_pool_shorthand_and_yaml(repo):
    result = invoke("run-1", "--set", "pool=codex:all,claude", "--yes")
    assert result.exit_code == 0, result.output
    assert run_config(repo)["review"]["pool"] == [{"agent": "codex", "covers": "all"},
                                                  {"agent": "claude"}]
    result = invoke("run-1", "--set", "pool=[{agent: claude, covers: auto}]",
                    "--set", "matrix_override={codex: [claude]}", "--yes")
    assert result.exit_code == 0, result.output
    review_block = run_config(repo)["review"]
    assert review_block["pool"] == [{"agent": "claude", "covers": "auto"}]
    assert review_block["matrix_override"] == {"codex": ["claude"]}


def test_cli_as_agent_refused_for_approved(repo):
    result = invoke("run-1", "--set", "scite=approved", "--as-agent", "--yes")
    assert result.exit_code != 0
    assert "only the user" in result.output
    assert run_config(repo) == {}


def test_cli_as_agent_may_turn_scite_off(repo):
    result = invoke("run-1", "--set", "scite=off", "--as-agent", "--yes")
    assert result.exit_code == 0, result.output
    assert run_config(repo) == {"review": {"scite": "off"}}


def test_cli_invalid_value_lists_allowed(repo):
    result = invoke("run-1", "--set", "review_decisions=robot", "--yes")
    assert result.exit_code != 0
    assert "human, coordinator" in result.output


def test_cli_malformed_set(repo):
    result = invoke("run-1", "--set", "scite", "--yes")
    assert result.exit_code != 0
    assert "KEY=VALUE" in result.output


def test_cli_without_yes_non_interactive_writes_nothing(repo):
    result = invoke("run-1", "--set", "scite=off")
    assert result.exit_code != 0
    assert "--yes" in result.output
    assert run_config(repo) == {}


def test_cli_without_set_shows_effective_settings(repo):
    result = invoke("run-1")
    assert result.exit_code == 0, result.output
    assert "scite" in result.output and "ask" in result.output
    assert run_config(repo) == {}


def test_review_group_registered_on_main():
    from scieflow.cli import main

    result = CliRunner().invoke(main, ["review", "configure", "--help"])
    assert result.exit_code == 0, result.output
    assert "--as-agent" in result.output
