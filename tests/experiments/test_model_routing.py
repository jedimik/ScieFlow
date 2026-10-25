import re
from pathlib import Path

import yaml

EXPERIMENTS = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "experiments"
ROUTING_PATH = EXPERIMENTS / "model-routing.yaml"
SKILL_PATH = EXPERIMENTS / "skills" / "model-routing" / "SKILL.md"
MODEL_ID = re.compile(r"claude-|gpt-|gemini-")


def load_routing():
    return yaml.safe_load(ROUTING_PATH.read_text())


def test_no_prices_no_as_of_no_refresh_rule():
    routing = load_routing()
    assert "as_of" not in routing
    assert "refresh_rule" not in routing
    assert "usd_per_mtok" not in ROUTING_PATH.read_text()


def test_routing_names_no_concrete_model_id():
    assert not MODEL_ID.search(ROUTING_PATH.read_text())


def test_tiers_carry_purpose_only():
    routing = load_routing()
    assert set(routing["tiers"]) == {"high", "standard", "cheap"}
    for tier_name, tier in routing["tiers"].items():
        assert tier["purpose"].strip(), tier_name
        assert set(tier) == {"purpose"}, tier_name


def test_every_phase_maps_to_a_defined_tier():
    routing = load_routing()
    assert routing["phases"], "phases must not be empty"
    for phase, tier in routing["phases"].items():
        assert tier in routing["tiers"], f"{phase} -> unknown tier {tier}"


def test_delegation_is_one_role_bound_agent_run():
    routing = load_routing()
    assert "scieflow agent run --role" in routing["delegation"]


def test_skill_has_no_self_commit_instruction():
    assert not re.search(r"commit", SKILL_PATH.read_text(), re.IGNORECASE)
