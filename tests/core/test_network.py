"""M-C1 t2 (Part A 5.4): a run's network mode and how it is resolved. Synthetic data; no network."""

from __future__ import annotations

import pytest
import yaml

from scieflow.core import network
from scieflow.integrations.scite.policy import PolicyError


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "proj"
    ws = root / "workspace" / "demo"
    (root / "config").mkdir(parents=True)
    ws.mkdir(parents=True)
    return ws


def write_net(ws, **over):
    doc = {"version": 1, "mode": "replay", "set_by": "human",
           "set_at": "2026-10-27T15:00:00Z", "reason": "test"}
    doc.update(over)
    (ws / "network.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")


def declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")


def write_policy(ws, text):
    (ws.parents[1] / "config" / "data-policy.yml").write_text(text, encoding="utf-8")


def resolved(ws, service="search-openalex", flag=None):
    got = network.resolve(ws, service, flag=flag)
    return got.mode, got.origin, got.on_miss


# --- the file ----------------------------------------------------------------------------------

def test_absent_file_outside_l2_is_live(run):
    assert resolved(run) == ("live", "default", "")


@pytest.mark.parametrize("mode", ["live", "record", "replay", "offline"])
def test_a_valid_file_gives_its_mode(run, mode):
    write_net(run, mode=mode)
    assert resolved(run) == (mode, "file", "miss")


@pytest.mark.parametrize("body", [
    "version: 1\nmode: replay\nset_by: human\nset_at: x\nreason: r\nextra: 1\n",   # unknown key
    "version: 1\nmode: sometimes\nset_by: human\nset_at: x\nreason: r\n",          # unknown mode
    "version: 2\nmode: replay\nset_by: human\nset_at: x\nreason: r\n",             # wrong version
    "version: 1\nmode: replay\nset_by: robot\nset_at: x\nreason: r\n",             # unknown actor
    "version: 1\nmode: replay\nset_by: human\nset_at: 12\nreason: r\n",            # wrong type
    "version: 1\nmode: [replay\n",                                                  # YAML error
    "- replay\n",                                                                   # not a mapping
])
def test_an_invalid_file_fails_closed(run, body):
    (run / "network.yml").write_text(body, encoding="utf-8")
    assert resolved(run) == ("offline", "invalid", "miss")


def test_a_symbolic_link_file_is_invalid(run, tmp_path):
    target = tmp_path / "elsewhere.yml"
    target.write_text("version: 1\nmode: live\nset_by: human\nset_at: x\nreason: r\n")
    (run / "network.yml").symlink_to(target)
    assert resolved(run) == ("offline", "invalid", "miss")


# --- L2 runs without a file --------------------------------------------------------------------

def test_an_l2_run_with_a_listed_service_is_live(run):
    declare_l2(run)
    write_policy(run, yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "search-openalex", "reason": "test: reviewed"}]}))
    assert resolved(run) == ("live", "l2-default", "")


def test_an_l2_run_with_an_unlisted_service_replays_and_defers_misses_to_m_b(run):
    declare_l2(run)
    assert resolved(run) == ("replay", "l2-default", "m-b")


def test_an_l2_run_with_an_invalid_policy_replays(run):
    declare_l2(run)
    write_policy(run, "version: 1\nenforcement: maybe\n")
    assert resolved(run) == ("replay", "l2-default", "m-b")


def test_a_file_wins_over_the_l2_default(run):
    declare_l2(run)
    write_net(run, mode="live")
    assert resolved(run) == ("live", "file", "miss")


# --- command flags -----------------------------------------------------------------------------

def test_a_flag_may_tighten_a_live_run(run):
    assert resolved(run, flag="offline") == ("offline", "flag", "miss")


def test_a_flag_may_switch_live_to_record(run):
    assert resolved(run, flag="record")[0] == "record"


def test_a_flag_may_tighten_a_live_file(run):
    write_net(run, mode="live")
    assert resolved(run, flag="replay") == ("replay", "flag", "miss")


def test_a_flag_may_move_between_no_egress_modes(run):
    write_net(run, mode="replay")
    assert resolved(run, flag="offline") == ("offline", "flag", "miss")


@pytest.mark.parametrize("setup", ["replay_file", "invalid_file", "l2_default_replay"])
def test_a_flag_cannot_loosen_a_no_egress_run(run, setup):
    if setup == "replay_file":
        write_net(run, mode="replay")
    elif setup == "invalid_file":
        (run / "network.yml").write_text("mode: [\n", encoding="utf-8")
    else:
        declare_l2(run)
    with pytest.raises(network.NetworkRefused, match="cannot loosen"):
        network.resolve(run, "search-openalex", flag="live")


def test_a_refusal_is_a_policy_refusal_with_exit_3(run):
    write_net(run, mode="replay")
    with pytest.raises(PolicyError) as exc:
        network.resolve(run, "search-openalex", flag="record")
    assert isinstance(exc.value, network.NetworkRefused)
    from scieflow.integrations.scite.policy import exit_code
    assert exit_code(exc.value) == 3


@pytest.mark.parametrize("flag", ["record", "replay", "offline"])
def test_a_no_egress_or_record_flag_needs_a_run(flag):
    with pytest.raises(network.NetworkUsage, match="needs --workspace"):
        network.resolve(None, "search-openalex", flag=flag)


def test_live_without_a_run_is_live():
    assert network.resolve(None, "search-openalex").mode == "live"


def test_an_unknown_flag_is_a_usage_error(run):
    with pytest.raises(network.NetworkUsage):
        network.resolve(run, "search-openalex", flag="bogus")


def test_network_usage_is_a_value_error_so_it_exits_1():
    assert issubclass(network.NetworkUsage, ValueError)


# --- the miss --------------------------------------------------------------------------------

def test_a_miss_names_provider_op_key_mode_and_run_and_nothing_else(run):
    exc = network.NetworkMiss("search-openalex", "search", "ab" * 32, "replay", "demo")
    assert isinstance(exc, PolicyError)
    assert str(exc) == ("offline-miss: search-openalex search (key abababababab): "
                        "run demo is replay; nothing was sent")
