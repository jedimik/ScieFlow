"""Model families and the reviewer-pool roles (scite review plan T0.1, T0.2)."""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import agent_config as ac
from scieflow.core import config, sessions
from scieflow.core.cli import agent

ROOT = Path(__file__).resolve().parents[2]


def configure(*args):
    return CliRunner().invoke(agent, ["configure", *args])


def ws_config(repo: Path, slug: str = "run-1") -> dict:
    path = repo / "workspace" / slug / "config.yml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def add_agent(repo: Path, name: str, **fields) -> None:
    path = repo / "config" / "agents.yml"
    data = yaml.safe_load(path.read_text())
    data["agents"][name] = {"cmd": f"{name} {{prompt}}", "tier": "primary",
                            "enabled": True, **fields}
    path.write_text(yaml.safe_dump(data, sort_keys=False))


# --- T0.1 model_family -------------------------------------------------------

def test_every_enabled_registry_agent_resolves_a_model_family():
    agents = config.load_agents(ROOT)
    enabled = {n: c for n, c in agents.items() if c.get("enabled", True)}
    assert enabled
    missing = [n for n, c in enabled.items() if not ac.model_family(c)]
    assert missing == []


@pytest.mark.parametrize("name, family", [
    ("claude", "claude"), ("claude-paper", "claude"),
    ("codex", "openai"), ("codex-paper", "openai"), ("codex-review", "openai"),
    ("agy", "gemini"), ("stub", "stub"),
])
def test_shipped_agents_map_to_their_model_family(name, family):
    assert ac.model_family(config.load_agents(ROOT)[name]) == family


def test_model_family_falls_back_to_family_and_else_none():
    assert ac.model_family({"model_family": "openai", "family": "codex"}) == "openai"
    assert ac.model_family({"family": "claude"}) == "claude"
    assert ac.model_family({}) is None
    assert ac.model_family({"model_family": "  "}) is None


def test_can_converse_is_unchanged_for_every_registry_agent():
    # Snapshot taken before `model_family:` was added: the dialect key
    # `family:` must stay the only thing can_converse reads.
    before = {"claude": True, "codex": True, "codex-paper": False,
              "claude-paper": False, "codex-review": False, "agy": True,
              "stub": False}
    agents = config.load_agents(ROOT)
    assert {n: sessions.can_converse(c) for n, c in agents.items()} == before


# --- T0.2 roles ----------------------------------------------------------------

def test_draft_reviewers_is_a_primary_only_fan_out_role():
    spec = ac.ROLES["research.draft-reviewers"]
    assert spec.many and not spec.support_ok
    assert ac.ROLES["research.reviewer"].many
    assert ac.LEGACY_ROLE_KEYS["reviewer"] == "research.reviewer"


def test_shipped_default_draft_reviewers_are_the_draft_authors():
    eff = ac.resolve(ROOT)
    assert eff.problems == []
    assert eff.value("research.draft-reviewers") == eff.value("research.draft-authors")


def test_draft_reviewers_follow_the_authors_unless_the_run_sets_them(repo, write_ws):
    eff = ac.resolve(repo)   # the fixture's defaults do not name the role at all
    assert eff.problems == []
    assert eff.value("research.draft-reviewers") == ["claude", "codex"]

    write_ws(repo, {"assignments": {"research.draft-authors": ["codex"]}})
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["codex"]

    write_ws(repo, {"draft_agents": ["claude"]})   # legacy key
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["claude"]

    write_ws(repo, {"assignments": {"research.draft-authors": ["codex"],
                                    "research.draft-reviewers": ["claude", "codex"]}})
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.draft-reviewers") == ["claude", "codex"]
    assert eff.assignments["research.draft-reviewers"].source == ac.WORKSPACE


def test_following_reviewers_inherit_the_authors_support_exception(repo, write_ws):
    write_ws(repo, {"assignments": {"research.draft-authors": ["agy", "codex"]},
                    "support_as_primary": ["research.draft-authors"]})
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.draft-reviewers") == ["agy", "codex"]
    assert eff.problems == []
    # an explicit reviewer set needs its own exception
    write_ws(repo, {"assignments": {"research.draft-authors": ["agy", "codex"],
                                    "research.draft-reviewers": ["agy", "codex"]},
                    "support_as_primary": ["research.draft-authors"]})
    assert any("--promote research.draft-reviewers" in p
               for p in ac.resolve(repo, "run-1").problems)


def test_legacy_agents_list_does_not_detach_reviewers_from_authors(repo, write_ws):
    path = repo / "config" / "defaults.yml"
    data = yaml.safe_load(path.read_text())
    data["assignments"]["research.draft-reviewers"] = ["claude", "codex"]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    write_ws(repo, {"agents": ["claude", "codex"], "draft_agents": ["codex"]})
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["codex"]
    write_ws(repo, {"agents": ["codex"]})   # narrowed authors, no explicit reviewers
    eff = ac.resolve(repo, "run-1")
    assert eff.value("research.draft-reviewers") == eff.value("research.draft-authors") == ["codex"]


def test_single_string_authors_are_followed(repo, write_ws):
    write_ws(repo, {"draft_agents": "codex"})
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["codex"]


def test_changing_the_default_authors_moves_the_default_reviewers(repo):
    result = configure("--assign", "research.draft-authors=codex", "--yes")
    assert result.exit_code == 0, result.output
    assert ac.resolve(repo).value("research.draft-reviewers") == ["codex"]


@pytest.mark.parametrize("target", [[], ["--workspace", "run-1"]])
def test_family_check_covers_reviewers_that_follow_the_authors(repo, target):
    add_agent(repo, "nofam")
    result = configure(*target, "--assign", "research.draft-authors=nofam,codex", "--yes")
    assert result.exit_code != 0
    assert "model_family" in result.output and "nofam" in result.output
    # with an explicit reviewer pool the authors need no family
    result = configure(*target, "--assign", "research.draft-authors=nofam,codex",
                       "--assign", "research.draft-reviewers=codex,claude", "--yes")
    assert result.exit_code == 0, result.output


def test_unset_after_assign_of_draft_reviewers_wins(repo):
    result = configure("--workspace", "run-1",
                       "--assign", "research.draft-authors=codex",
                       "--assign", "research.draft-reviewers=claude,codex",
                       "--unset", "research.draft-reviewers", "--yes")
    assert result.exit_code == 0, result.output
    assert "research.draft-reviewers" not in ws_config(repo)["assignments"]
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["codex"]


def test_explicit_default_draft_reviewers_are_kept_when_authors_are_default(repo):
    path = repo / "config" / "defaults.yml"
    data = yaml.safe_load(path.read_text())
    data["assignments"]["research.draft-reviewers"] = ["codex"]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    assert ac.resolve(repo).value("research.draft-reviewers") == ["codex"]


def test_configure_writes_draft_reviewers_as_a_list(repo):
    add_agent(repo, "codex-review", model_family="openai")
    add_agent(repo, "claude-paper", model_family="claude")
    result = configure("--workspace", "run-1", "--assign",
                       "research.draft-reviewers=codex-review,claude-paper", "--yes")
    assert result.exit_code == 0, result.output
    assert ws_config(repo)["assignments"] == {
        "research.draft-reviewers": ["codex-review", "claude-paper"]}
    eff = ac.resolve(repo, "run-1")
    assert eff.problems == []
    assert eff.value("research.draft-reviewers") == ["codex-review", "claude-paper"]


def test_configure_keeps_a_run_reviewer_set_equal_to_the_defaults(repo):
    # The run changes the authors; reviewers explicitly equal to the default
    # list must stay written, or they would silently follow the new authors.
    result = configure("--workspace", "run-1",
                       "--assign", "research.draft-authors=codex",
                       "--assign", "research.draft-reviewers=claude,codex", "--yes")
    assert result.exit_code == 0, result.output
    assert ac.resolve(repo, "run-1").value("research.draft-reviewers") == ["claude", "codex"]


def test_support_agent_is_refused_as_draft_reviewer_unless_promoted(repo):
    result = configure("--workspace", "run-1", "--assign",
                       "research.draft-reviewers=agy,codex", "--yes")
    assert result.exit_code != 0
    assert "--promote research.draft-reviewers" in result.output
    result = configure("--workspace", "run-1", "--assign",
                       "research.draft-reviewers=agy,codex",
                       "--promote", "research.draft-reviewers", "--yes")
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("role", ["research.draft-reviewers", "research.reviewer"])
def test_agent_without_a_model_family_is_refused_for_review_roles(repo, role):
    add_agent(repo, "nofam")
    result = configure("--workspace", "run-1", "--assign", f"{role}=nofam", "--yes")
    assert result.exit_code != 0
    assert "model_family" in result.output and "nofam" in result.output
    assert ws_config(repo) == {}
    # the same agent is fine in a role that does not compare families
    result = configure("--workspace", "run-1", "--assign", "research.outline=nofam", "--yes")
    assert result.exit_code == 0, result.output


def test_agent_without_a_model_family_is_refused_in_the_defaults_too(repo):
    add_agent(repo, "nofam")
    result = configure("--assign", "research.draft-reviewers=nofam", "--yes")
    assert result.exit_code != 0 and "model_family" in result.output


def test_research_reviewer_accepts_a_list(repo, write_ws):
    add_agent(repo, "codex-review", model_family="openai")
    result = configure("--workspace", "run-1", "--assign",
                       "research.reviewer=codex,codex-review", "--yes")
    assert result.exit_code == 0, result.output
    assert ws_config(repo)["assignments"] == {"research.reviewer": ["codex", "codex-review"]}
    assert ac.resolve(repo, "run-1").value("research.reviewer") == ["codex", "codex-review"]


def test_reviewer_list_containing_the_submitter_is_a_problem(repo, write_ws):
    write_ws(repo, {"assignments": {"research.reviewer": ["codex", "claude"]}})
    assert any("must be different" in p for p in ac.resolve(repo, "run-1").problems)


@pytest.mark.parametrize("ws", [
    {"assignments": {"research.reviewer": "codex"}},
    {"reviewer": "codex"},
    {"agents": ["claude", "codex"], "reviewer": "codex", "submitter": "claude"},
])
def test_legacy_string_reviewer_configs_still_resolve(repo, write_ws, ws):
    write_ws(repo, ws)
    eff = ac.resolve(repo, "run-1")
    assert eff.problems == []
    assert eff.value("research.reviewer") == "codex"


def test_legacy_string_reviewer_still_dispatches(repo, write_ws):
    from scieflow.core import agent_run
    from scieflow.core.project import Project

    write_ws(repo, {"reviewer": "codex"})
    (repo / "workspace" / "run-1" / "status.yml").write_text("slug: run-1\n")
    prompt = repo / "workspace" / "run-1" / "prompt.md"
    prompt.write_text("hi")
    prepared = agent_run.prepare(Project(repo), "codex", prompt,
                                 role="research.reviewer", sandbox_enabled=False)
    assert prepared is not None
    with pytest.raises(agent_run.DispatchError) as exc:
        agent_run.prepare(Project(repo), "claude", prompt,
                          role="research.reviewer", sandbox_enabled=False)
    assert "not assigned to research.reviewer" in str(exc.value)
