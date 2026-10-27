"""M-B t4, Part A 5.7: scite requests for an L2 run are refused before the cache and the transport.

Mocked HTTP only; no network, no token. Synthetic data only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import responses
import yaml

from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import policy as pol
from scieflow.integrations.scite import public

API = "https://api.scite.ai"
T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    return root, ws, tmp_path / "cachehome" / "scite"


class _Clock:
    def __init__(self):
        self.t = 50.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def client(env, environ=None, write=True, **cfg):
    root, ws, day_dir = env
    if write:
        (root / "config" / "scite.yml").write_text(yaml.safe_dump(cfg))
    p = pol.load(root, environ={} if environ is None else environ)
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=lambda: T0)
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=lambda: T0)
    fc = _Clock()
    return public.PublicClient(p, c, led, sleep=fc.sleep, clock=fc.clock), led, fc


def echo_tallies(request):
    dois = json.loads(request.body)
    body = {"tallies": {d: {"doi": d, "total": 10, "supporting": 3, "contradicting": 2,
                            "mentioning": 5, "unclassified": 0, "citingPublications": 9}
                        for d in dois}}
    return 200, {}, json.dumps(body)


def _l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))


def _policy(root, text):
    (root / "config" / "data-policy.yml").write_text(text)


@responses.activate
def test_l2_run_refuses_scite_before_any_request(env):
    cl, led, _ = client(env, environ={})
    _l2(env[1])
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    with pytest.raises(pol.PolicyError, match="data protection"):
        cl.tallies(["10.1000/a"], round=1)
    with pytest.raises(pol.PolicyError, match="data protection"):
        cl.paper("10.1000/a", round=1)
    assert len(responses.calls) == 0
    assert led.rows() == []


@responses.activate
def test_a_cached_answer_is_refused_too_for_an_l2_run(env):
    cl, _, _ = client(env, environ={})
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    assert cl.tallies(["10.1000/a"], round=1)["10.1000/a"]["status"] == "ok"
    calls_before = len(responses.calls)
    _l2(env[1])
    with pytest.raises(pol.PolicyError, match="data protection"):
        cl.tallies(["10.1000/a"], round=1)
    assert len(responses.calls) == calls_before


@responses.activate
def test_listed_scite_passes_for_an_l2_run(env):
    root, ws, _ = env
    cl, _, _ = client(env, environ={})
    _l2(ws)
    _policy(root, yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "scite", "reason": "test: reviewed service"}]}))
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    assert cl.tallies(["10.1000/a"], round=1)["10.1000/a"]["status"] == "ok"


@responses.activate
def test_invalid_policy_refuses_scite(env):
    root, _, _ = env
    cl, _, _ = client(env, environ={})
    _policy(root, "version: 1\nenforcement: maybe\n")
    with pytest.raises(pol.PolicyError, match="data-policy"):
        cl.tallies(["10.1000/a"], round=1)
    assert len(responses.calls) == 0
