import pytest

from scieflow.core import agent_config as ac
from scieflow.core import agent_configure as acf

REGISTRY = {"agents": {
    "codex": {"cmd": "codex exec --model {model} -c e={reasoning} {prompt}", "model": "astra",
              "reasoning": "high", "tier": "primary", "enabled": True,
              "menu": {"models": ["astra", "sol"],
                       "reasoning": {"levels": ["low", "high", "xhigh"]}}},
    "claude": {"cmd": "claude -p --model {model} {prompt}", "model": "fable",
               "tier": "primary", "enabled": True, "menu": {"models": ["fable", "opus"]}},
    "agy": {"cmd": "agy --model {model} {prompt}", "model": "gem", "tier": "support",
            "enabled": True},
}}


def defaults(draft):
    roles = {r: ("claude" if not spec.many else ["claude"]) for r, spec in ac.ROLES.items()}
    roles["research.submitter"] = "codex"
    roles["research.draft-authors"] = draft
    return {"assignments": roles}


def test_rich_entries_resolve_to_names_plus_role_overrides():
    eff = ac.resolve_data(REGISTRY, defaults([
        {"agent": "codex", "model": "sol", "reasoning": "xhigh"},
        {"agent": "claude", "reasoning": "extended-thinking"},
    ]))
    assert eff.value("research.draft-authors") == ["codex", "claude"]
    assert eff.role_overrides["research.draft-authors"]["codex"].value == {
        "model": "sol", "reasoning": "xhigh"}
    assert eff.problems == []
    assert "role_overrides" in eff.to_json()


def test_plain_strings_still_work():
    eff = ac.resolve_data(REGISTRY, defaults(["codex", "claude"]))
    assert eff.value("research.draft-authors") == ["codex", "claude"]
    assert eff.role_overrides == {}


def test_unknown_fields_and_off_menu_models_are_reported():
    eff = ac.resolve_data(REGISTRY, defaults([{"agent": "codex", "temperature": 2},
                                              {"agent": "claude", "model": "gpt-9"}]))
    assert any("temperature" in p for p in eff.problems)
    assert any("gpt-9" in w for w in eff.warnings)


def test_support_agent_drafting_needs_the_explicit_promotion():
    base = defaults([{"agent": "agy", "model": "gem"}, "codex"])
    assert any("primary-only" in p for p in ac.resolve_data(REGISTRY, base).problems)
    promoted = {**base, "support_as_primary": ["research.draft-authors"]}
    assert ac.resolve_data(REGISTRY, promoted).problems == []


def test_apply_role_override_handles_reasoning_both_ways():
    codex = REGISTRY["agents"]["codex"]
    assert ac.apply_role_override(codex, {"model": "sol", "reasoning": "xhigh"})["reasoning"] == "xhigh"
    claude = ac.apply_role_override(REGISTRY["agents"]["claude"], {"reasoning": "extended-thinking"})
    assert claude["cmd"].startswith(ac.EXTENDED_THINKING)
    back = ac.apply_role_override(claude, {"reasoning": "default"})
    assert not back["cmd"].startswith(ac.EXTENDED_THINKING)


@pytest.mark.parametrize("token, expected", [
    ("codex", "codex"),
    ("codex@sol", {"agent": "codex", "model": "sol"}),
    ("codex@sol/xhigh", {"agent": "codex", "model": "sol", "reasoning": "xhigh"}),
    ("claude@/extended-thinking", {"agent": "claude", "reasoning": "extended-thinking"}),
])
def test_parse_assign_rich_tokens(token, expected):
    op = acf.parse_assign(f"research.draft-authors={token},codex")
    assert op.value[0] == expected


def test_agent_run_role_applies_override_and_refuses_unassigned(tmp_path):
    import subprocess
    import sys

    import yaml

    root = tmp_path
    (root / "config").mkdir()
    cmd = f"{sys.executable} -c \"import sys; print(sys.argv[1:])\" --model {{model}} --effort {{reasoning}}"
    reg = {"agents": {"codex": {"cmd": cmd, "model": "astra", "reasoning": "high",
                                "tier": "primary", "enabled": True, "timeout_min": 1},
                      "claude": {"cmd": cmd.replace(" --effort {reasoning}", ""), "model": "fable",
                                 "tier": "primary", "enabled": True, "timeout_min": 1}}}
    (root / "config" / "agents.yml").write_text(yaml.safe_dump(reg))
    d = defaults([{"agent": "codex", "model": "sol", "reasoning": "xhigh"}])
    d["assignments"]["research.submitter"] = "codex"
    (root / "config" / "defaults.yml").write_text(yaml.safe_dump(d))
    (root / "p.md").write_text("hi")
    # These prompts belong to no run, which the sandbox refuses by design.
    run = [sys.executable, "-m", "scieflow.core.agent_run", "--no-sandbox"]
    ok = subprocess.run([*run, "--role", "research.draft-authors", "codex", "p.md", "t.md"],
                        cwd=root, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "'sol'" in (root / "t.md").read_text() and "'xhigh'" in (root / "t.md").read_text()
    refused = subprocess.run([*run, "--role", "research.draft-authors", "claude", "p.md", "t.md"],
                             cwd=root, capture_output=True, text=True)
    assert refused.returncode != 0 and "not assigned" in refused.stderr
