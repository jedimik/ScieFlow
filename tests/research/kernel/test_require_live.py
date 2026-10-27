"""M-C1 t3 (Part A 5.7): services that cannot be replayed are refused in replay/offline.

scite, the review evidence build and NotebookLM. Every transport and session here is a counting
fake; nothing reaches a real service. Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scieflow.core import network
from scieflow.core.review import evidence
from scieflow.integrations.scite import gateway as scite_gateway
from scieflow.integrations.scite import policy as scite_policy


def run(tmp_path: Path, mode: str | None) -> Path:
    ws = tmp_path / "workspace" / "demo"
    ws.mkdir(parents=True)
    if mode is not None:
        (ws / "network.yml").write_text(yaml.safe_dump({
            "version": 1, "mode": mode, "set_by": "human",
            "set_at": "2026-10-27T15:00:00Z", "reason": "test"}), encoding="utf-8")
    return ws


class Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("a refused service reached its transport")

    def get(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("a refused service reached the cache")


# --- the helper ------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["replay", "offline"])
def test_require_live_refuses_a_service_in_a_no_egress_file(tmp_path, mode):
    ws = run(tmp_path, mode)
    with pytest.raises(network.NetworkRefused, match="scite cannot be replayed and is refused"):
        network.require_live(ws, "scite")


@pytest.mark.parametrize("mode", ["live", "record"])
def test_require_live_allows_the_egress_modes(tmp_path, mode):
    ws = run(tmp_path, mode)
    network.require_live(ws, "scite")   # no raise


def test_require_live_refuses_an_invalid_file(tmp_path):
    ws = run(tmp_path, None)
    (ws / "network.yml").write_text("mode: nonsense\n", encoding="utf-8")
    with pytest.raises(network.NetworkRefused):
        network.require_live(ws, "scite")


def test_require_live_leaves_no_file_and_no_run_alone(tmp_path):
    network.require_live(run(tmp_path, None), "scite")   # default: live
    network.require_live(None, "scite")                  # no run


def test_require_live_leaves_an_l2_default_to_the_data_rules(tmp_path, monkeypatch):
    # an L2 run without network.yml is handled by M-B (never this check)
    ws = run(tmp_path, None)
    monkeypatch.setattr(network.levels, "run_level", lambda w: "L2")
    monkeypatch.setattr(network, "_l2_lists", lambda w, s: False)
    network.require_live(ws, "scite")   # no raise: origin is l2-default, not a file


# --- scite: refused before the cache and the transport --------------------------------------

def test_scite_request_refuses_in_replay_before_cache_and_transport(tmp_path):
    ws = run(tmp_path, "replay")
    cache, transport = Counting(), Counting()
    ledger = SimpleNamespace(ws=ws)
    with pytest.raises(network.NetworkRefused):
        scite_gateway.request(None, cache, ledger, source="rest", op="search", params={},
                              round=None, ttl_class="search", transport=transport)
    assert transport.calls == 0 and cache.calls == 0


def test_scite_refusal_is_a_policy_refusal(tmp_path):
    # the exit-3 mapping of the scite CLI depends on this (scite policy.PolicyError)
    assert issubclass(network.NetworkRefused, scite_policy.PolicyError)


# --- review evidence: refused before any source ----------------------------------------------

def test_evidence_execute_refuses_in_offline_before_any_source(tmp_path):
    ws = run(tmp_path, "offline")
    sources = Counting()
    with pytest.raises(network.NetworkRefused):
        evidence.execute(None, ws, None, sources=sources)
    assert sources.calls == 0


# --- NotebookLM: refused before any session -------------------------------------------------

@pytest.fixture
def nblm_root(tmp_path, monkeypatch):
    from nblm import nblm

    root_dir = tmp_path / "root"
    (root_dir / "config").mkdir(parents=True)
    (root_dir / "config" / "notebooklm.yml").write_text(
        "profiles:\n"
        "  default:\n"
        "    session_state: /nonexistent/state.json\n"
        "    notebook_prefix: scieflow-\n"
        "    allowed_ops: [check, resolve, fetch, upload, verify, ask]\n"
        "    source_hosts: [arxiv.org]\n"
        "    limits:\n"
        "      max_sources_per_notebook: 2\n"
        "      max_source_mb: 50\n"
        "      max_questions_per_run: 40\n"
        "      max_questions_per_day: 45\n"
        "      max_claims_per_question: 5\n"
        "      reask_threshold: partial\n", encoding="utf-8")
    monkeypatch.setattr(nblm, "_root", lambda: root_dir)
    return root_dir


def test_nblm_session_command_refuses_in_replay_before_any_session(nblm_root, tmp_path):
    """Without the check the profile is valid and no session is installed: the command would
    exit 2 (NO_SESSION). Exit 3 proves the replay refusal came first."""
    from nblm import nblm

    ws = run(tmp_path, "replay")
    code = nblm.main(["check", "default", "--workspace", str(ws)])
    assert code == 3
