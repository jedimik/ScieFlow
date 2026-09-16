from datetime import date
from pathlib import Path

import yaml

ROUTING_PATH = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "experiments" / "model-routing.yaml"


def load_routing():
    return yaml.safe_load(ROUTING_PATH.read_text())


def test_as_of_is_a_date():
    routing = load_routing()
    assert date.fromisoformat(str(routing["as_of"]))
    assert routing["refresh_rule"].strip()


def test_tiers_have_models_with_prices():
    routing = load_routing()
    assert set(routing["tiers"]) == {"high", "standard", "cheap"}
    for tier_name, tier in routing["tiers"].items():
        assert tier["purpose"].strip(), tier_name
        assert tier["models"], tier_name
        for cli, entry in tier["models"].items():
            assert entry["model"], (tier_name, cli)
            assert entry["usd_per_mtok_in"] > 0, (tier_name, cli)
            assert entry["usd_per_mtok_out"] > 0, (tier_name, cli)


def test_every_phase_maps_to_a_defined_tier():
    routing = load_routing()
    assert routing["phases"], "phases must not be empty"
    for phase, tier in routing["phases"].items():
        assert tier in routing["tiers"], f"{phase} -> unknown tier {tier}"


def test_delegation_covers_every_cli_in_tiers():
    routing = load_routing()
    clis = {cli for tier in routing["tiers"].values() for cli in tier["models"]}
    assert clis <= set(routing["delegation"]), (
        f"missing delegation recipe for: {clis - set(routing['delegation'])}"
    )
    for cli, recipe in routing["delegation"].items():
        assert recipe.strip(), cli
