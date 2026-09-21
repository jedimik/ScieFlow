"""Old run configs resolve to the agents they ran with."""

from scieflow.core.agent_config import LEGACY, WORKSPACE, resolve_data

REGISTRY = {
    "agents": {
        name: {"cmd": "x {prompt}", "model": "m", "tier": tier, "enabled": True}
        for name, tier in (("claude", "primary"), ("codex", "primary"), ("agy", "support"))
    }
}
DEFAULTS = {
    "assignments": {
        "loop.experiment": "codex",
        "loop.literature": "codex",
        "loop.paper-draft": "codex",
        "research.draft-authors": ["codex"],
        "research.journal-profile": ["agy", "codex"],
    }
}


def test_old_loop_agent_key_sets_all_loop_roles():
    eff = resolve_data(REGISTRY, DEFAULTS, {"agent": "claude"})
    for role in ("loop.experiment", "loop.literature", "loop.paper-draft"):
        assert eff.assignments[role].value == "claude"
        assert eff.assignments[role].source == LEGACY
    assert not [p for p in eff.problems if "loop." in p]


def test_explicit_assignment_beats_legacy_key():
    eff = resolve_data(
        REGISTRY, DEFAULTS, {"agent": "claude", "assignments": {"loop.experiment": "codex"}}
    )
    assert eff.assignments["loop.experiment"].value == "codex"
    assert eff.assignments["loop.experiment"].source == WORKSPACE
    assert eff.assignments["loop.literature"].value == "claude"


def test_draft_agents_and_journal_profile_agents():
    eff = resolve_data(
        REGISTRY, DEFAULTS,
        {"draft_agents": ["claude", "codex"], "journal_profile_agents": ["agy", "codex"]},
    )
    assert eff.assignments["research.draft-authors"].value == ["claude", "codex"]
    assert eff.assignments["research.journal-profile"].source == LEGACY


def test_journal_profiler_plus_crosscheck():
    eff = resolve_data(
        REGISTRY, DEFAULTS, {"journal_profiler": "agy", "primary_profile_crosscheck": "codex"}
    )
    assert eff.assignments["research.journal-profile"].value == ["agy", "codex"]


def test_unmapped_legacy_key_is_reported_not_dropped():
    eff = resolve_data(REGISTRY, DEFAULTS, {"support_idea_agent": "agy"})
    assert any("support_idea_agent" in w for w in eff.warnings)
