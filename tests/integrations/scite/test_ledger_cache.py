"""Ledger (per run / per day, calls vs results), cache (TTL, key normalisation)
and the gateway every scite request goes through: policy -> cache -> ledger -> transport."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scieflow.core import events
from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import gateway
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import policy as pol

SECRET = "sk-scite-TOPSECRET-0123456789"
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + timedelta(**kw)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    day_dir = tmp_path / "cachehome" / "scite"
    return root, ws, day_dir


def make_policy(root, environ=None, **cfg):
    (root / "config" / "scite.yml").write_text(yaml.safe_dump(cfg))
    return pol.load(root, environ={"SCITE_TOKEN": SECRET} if environ is None else environ)


def make(env, clock=None, **cfg):
    root, ws, day_dir = env
    clock = clock or Clock()
    p = make_policy(root, **cfg)
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=clock)
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=clock)
    return p, led, c, clock


class Transport:
    def __init__(self, results=3, fail=None):
        self.calls = []
        self.results = results
        self.fail = fail

    def __call__(self, op, params):
        self.calls.append((op, params))
        if self.fail:
            raise self.fail
        return {"op": op, "hits": list(range(self.results))}, self.results


def mcp(p, led, c, tr, round_=1, term="sleep spindles", op="search_literature", **extra):
    return gateway.request(p, c, led, source="mcp", op=op,
                           params={"term": term, "limit": 3, **extra}, round=round_,
                           ttl_class="search", transport=tr)


# --- cache key normalisation ------------------------------------------------------


def test_doi_normalisation():
    n = cache_mod.normalise_doi
    assert n("10.1000/ABC.Def") == "10.1000/abc.def"
    assert n(" https://doi.org/10.1000/X ") == "10.1000/x"
    assert n("doi:10.1000/X") == "10.1000/x"
    assert n("http://dx.doi.org/10.1000/X") == "10.1000/x"


def test_request_key_normalises_case_whitespace_and_order():
    k = cache_mod.request_key
    a = k("mcp", "search_literature", {"term": "rem  sleep\n behaviour", "dois": ["10.1/B", "10.1/a"]})
    b = k("mcp", "search_literature", {"term": " rem sleep behaviour ",
                                       "dois": ["https://doi.org/10.1/a", "10.1/b", "10.1/A"]})
    assert a == b
    assert k("rest", "paper", {"doi": "10.1/A"}) == k("rest", "paper", {"doi": "doi:10.1/a"})
    assert k("rest", "paper", {"doi": "10.1/a"}) != k("mcp", "paper", {"doi": "10.1/a"})
    assert k("mcp", "search_literature", {"term": "x", "limit": 3}) != \
        k("mcp", "search_literature", {"term": "x", "limit": 5})
    assert len(a) == 64


def test_cache_put_get_and_ttl(env):
    _, _, day_dir = env
    clock = Clock()
    c = cache_mod.Cache(day_dir / "cache", {"metadata": 30, "search": 7}, now=clock)
    key = cache_mod.request_key("mcp", "search_literature", {"term": "x"})
    assert c.get(key, "search") is None
    c.put(key, "search", {"v": 1})
    assert c.get(key, "search") == {"v": 1}
    clock.advance(days=6, hours=23)
    assert c.get(key, "search") == {"v": 1}
    clock.advance(hours=2)
    assert c.get(key, "search") is None
    c.put(key, "metadata", {"v": 2})
    clock.advance(days=29)
    assert c.get(key, "metadata") == {"v": 2}
    with pytest.raises(ValueError):
        c.get(key, "unknown-class")


def test_cache_ignores_corrupt_and_foreign_keys(env):
    _, _, day_dir = env
    c = cache_mod.Cache(day_dir / "cache", {"metadata": 30, "search": 7}, now=Clock())
    key = cache_mod.request_key("mcp", "search_literature", {"term": "x"})
    c.put(key, "search", {"v": 1})
    path = c.path(key)
    path.write_text("{not json")
    assert c.get(key, "search") is None
    with pytest.raises(ValueError):
        c.path("../../etc/passwd")


# --- ledger -----------------------------------------------------------------------


def test_ledger_counts_calls_and_results_separately(env):
    p, led, _, _ = make(env)
    led.record(source="mcp", op="search_literature", round=1, results=7)
    led.record(source="mcp", op="citation_graph", round=1, results=0)
    led.record(source="rest", op="tallies", round=1, results=40)
    led.record(source="mcp", op="search_literature", round=1, results=7, cached=True)
    assert led.calls("run") == 2
    assert led.results("run") == 7
    assert led.calls("run", source="rest") == 1
    assert led.results("run", source="rest") == 40
    assert led.cached("run") == 1
    root, ws, day_dir = env
    run_file = ws / "scite" / "ledger.jsonl"
    day_file = day_dir / "ledger-2026-10-13.jsonl"
    assert len(run_file.read_text().splitlines()) == 4
    assert len(day_file.read_text().splitlines()) == 4
    rows = [json.loads(x) for x in run_file.read_text().splitlines()]
    assert rows[-1]["cached"] is True and rows[0]["cached"] is False


def test_ledger_per_round_run_and_day(env):
    p, led, _, clock = make(env, max_mcp_calls_per_round=3, max_mcp_calls_per_run=5,
                            max_mcp_calls_per_day=4)
    assert led.remaining(1) == {"round": 3, "run": 5, "day": 4}
    led.record(source="mcp", op="search_literature", round=1, results=1)
    led.record(source="mcp", op="search_literature", round=1, results=1)
    assert led.remaining(1) == {"round": 1, "run": 3, "day": 2}
    assert led.remaining(2) == {"round": 3, "run": 3, "day": 2}
    # another run on the same day shares the day budget
    root, _, day_dir = env
    ws2 = root / "workspace" / "other"
    ws2.mkdir()
    led2 = ledger_mod.Ledger(ws2, p, day_dir=day_dir, now=clock)
    led2.record(source="mcp", op="search_literature", round=1, results=1)
    assert led2.remaining(1) == {"round": 2, "run": 4, "day": 1}
    assert led.remaining(2)["day"] == 1
    clock.advance(days=1)
    assert led.remaining(2) == {"round": 3, "run": 3, "day": 4}


def test_unlimited_day_budget(env):
    _, led, _, _ = make(env)
    assert led.remaining(1)["day"] is None


def test_ledger_without_run_counts_day_only(env):
    root, _, day_dir = env
    p = make_policy(root, max_mcp_calls_per_day=2)
    led = ledger_mod.Ledger(None, p, day_dir=day_dir, now=Clock())
    led.record(source="mcp", op="search_literature", round=None, results=1)
    assert led.remaining(None) == {"round": None, "run": None, "day": 1}


def test_ledger_rejects_bad_records(env):
    _, led, _, _ = make(env)
    with pytest.raises(ValueError):
        led.record(source="ftp", op="x", round=1, results=0)
    with pytest.raises(ValueError):
        led.record(source="mcp", op="x", round=1, results=-1)


def test_ledger_tolerates_malformed_lines(env):
    _, led, _, _ = make(env)
    _, ws, _ = env
    (ws / "scite").mkdir()
    (ws / "scite" / "ledger.jsonl").write_text('garbage\n{"source": "mcp"}\n[1]\n')
    led.record(source="mcp", op="search_literature", round=1, results=1)
    assert led.calls("run") >= 1


def test_quota_low_fires_once_at_ten_percent(env):
    _, ws, _ = env
    p, led, _, _ = make(env, max_mcp_calls_per_round=50, max_mcp_calls_per_run=20)
    for _ in range(17):
        led.record(source="mcp", op="search_literature", round=1, results=1)
    assert events.read(ws, types=("scite.quota_low",)) == []
    led.record(source="mcp", op="search_literature", round=1, results=1)  # 2 left = 10 %
    led.record(source="mcp", op="search_literature", round=1, results=1)
    evs = events.read(ws, types=("scite.quota_low",))
    assert len(evs) == 1
    assert evs[0]["data"]["budget"] == "run"
    assert evs[0]["data"]["remaining"] == 2 and evs[0]["data"]["limit"] == 20


def test_quota_low_not_fired_by_round_budget(env):
    # a per-round budget of 5 reaches 0 every round: that is the boundary, not a warning
    _, ws, _ = env
    p, led, _, _ = make(env, max_mcp_calls_per_round=5, max_mcp_calls_per_run=100)
    for _ in range(5):
        led.record(source="mcp", op="search_literature", round=1, results=1)
    assert events.read(ws, types=("scite.quota_low",)) == []


def test_quota_low_on_day_budget(env):
    _, ws, _ = env
    p, led, _, _ = make(env, max_mcp_calls_per_round=50, max_mcp_calls_per_run=100,
                        max_mcp_calls_per_day=10)
    for _ in range(9):
        led.record(source="mcp", op="search_literature", round=1, results=1)
    evs = events.read(ws, types=("scite.quota_low",))
    assert [e["data"]["budget"] for e in evs] == ["day"]


# --- gateway ----------------------------------------------------------------------


def test_real_call_records_and_emits_integration_call(env):
    _, ws, _ = env
    p, led, c, _ = make(env)
    tr = Transport(results=3)
    r = mcp(p, led, c, tr)
    assert r.cached is False and r.results == 3 and r.value["hits"] == [0, 1, 2]
    assert len(tr.calls) == 1
    evs = events.read(ws, types=("integration.call",))
    assert len(evs) == 1
    d = evs[0]["data"]
    assert d["integration"] == "scite" and d["source"] == "mcp"
    assert d["op"] == "search_literature" and d["results"] == 3 and d["status"] == "ok"


def test_cache_hit_costs_nothing_and_is_recorded_cached(env):
    _, ws, _ = env
    p, led, c, _ = make(env)
    tr = Transport()
    mcp(p, led, c, tr, term="rem  sleep")
    r = mcp(p, led, c, tr, term=" rem sleep ")
    assert r.cached is True
    assert len(tr.calls) == 1
    assert led.calls("run") == 1 and led.cached("run") == 1
    assert led.remaining(1)["round"] == 4
    assert len(events.read(ws, types=("integration.call",))) == 1


def test_cache_hit_served_even_when_budget_exhausted(env):
    p, led, c, _ = make(env, max_mcp_calls_per_round=1)
    tr = Transport()
    mcp(p, led, c, tr, term="a")
    assert mcp(p, led, c, tr, term="a").cached is True
    with pytest.raises(ledger_mod.BudgetError):
        mcp(p, led, c, tr, term="b")
    assert len(tr.calls) == 1


def test_budget_per_round_run_day_enforced_before_transport(env):
    p, led, c, _ = make(env, max_mcp_calls_per_round=2, max_mcp_calls_per_run=3,
                        max_mcp_calls_per_day=10)
    tr = Transport()
    mcp(p, led, c, tr, round_=1, term="a")
    mcp(p, led, c, tr, round_=1, term="b")
    with pytest.raises(ledger_mod.BudgetError) as exc:
        mcp(p, led, c, tr, round_=1, term="c")
    assert "round" in str(exc.value)
    assert exc.value.code == "REFUSED_POLICY" and pol.exit_code(exc.value) == 3
    mcp(p, led, c, tr, round_=2, term="c")
    with pytest.raises(ledger_mod.BudgetError) as exc:
        mcp(p, led, c, tr, round_=2, term="d")
    assert "run" in str(exc.value)
    assert len(tr.calls) == 3
    # a refusal is not a call
    assert led.calls("run") == 3


def test_day_budget_enforced(env):
    p, led, c, _ = make(env, max_mcp_calls_per_day=1)
    tr = Transport()
    mcp(p, led, c, tr, term="a")
    with pytest.raises(ledger_mod.BudgetError) as exc:
        mcp(p, led, c, tr, round_=2, term="b")
    assert "day" in str(exc.value)


def test_cache_ttl_expiry_makes_a_new_call(env):
    p, led, c, clock = make(env)
    tr = Transport()
    mcp(p, led, c, tr, term="a")
    clock.advance(days=8)
    assert mcp(p, led, c, tr, round_=2, term="a").cached is False
    assert len(tr.calls) == 2


def test_tool_outside_allowlist_never_reaches_transport(env):
    p, led, c, _ = make(env, allowed_tools=["search_literature"])
    tr = Transport()
    with pytest.raises(pol.PolicyError):
        mcp(p, led, c, tr, op="citation_graph")
    assert tr.calls == [] and led.calls("run") == 0


def test_disabled_policy_never_reaches_transport_or_cache(env):
    root, ws, day_dir = env
    p = pol.load(root, environ={})  # no config/scite.yml
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=Clock())
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=Clock())
    tr = Transport()
    for source, op in (("mcp", "search_literature"), ("rest", "tallies")):
        with pytest.raises(pol.PolicyError):
            gateway.request(p, c, led, source=source, op=op, params={"doi": "10.1/a"},
                            round=1, ttl_class="metadata", transport=tr)
    assert tr.calls == []


def test_rest_works_on_public_tier_and_is_not_budgeted(env):
    root, ws, day_dir = env
    p, led, c, _ = make(env, environ={}, max_mcp_calls_per_round=1)
    assert p.tier == "public"
    tr = Transport(results=2)
    for i in range(3):
        gateway.request(p, c, led, source="rest", op="tallies", params={"dois": [f"10.1/{i}"]},
                        round=1, ttl_class="metadata", transport=tr)
    assert len(tr.calls) == 3
    assert led.calls("run", source="rest") == 3 and led.calls("run") == 0


def test_failed_call_counts_and_reraises(env):
    _, ws, _ = env
    p, led, c, _ = make(env)

    class Boom(RuntimeError):
        classification = "QUOTA"

    tr = Transport(fail=Boom("quota exhausted"))
    with pytest.raises(Boom):
        mcp(p, led, c, tr)
    assert led.calls("run") == 1
    rows = led.rows("run")
    assert rows[-1]["status"] == "error" and rows[-1]["error"] == "QUOTA"
    # a failure is never cached
    tr.fail = None
    assert mcp(p, led, c, tr).cached is False
    evs = events.read(ws, types=("integration.call",))
    assert [e["data"]["status"] for e in evs] == ["error", "ok"]


def test_token_never_written_anywhere(env, capsys):
    root, ws, day_dir = env
    p, led, c, _ = make(env)
    tr = Transport()
    mcp(p, led, c, tr)
    mcp(p, led, c, tr)
    gateway.request(p, c, led, source="rest", op="tallies", params={"dois": ["10.1/a"]},
                    round=1, ttl_class="metadata", transport=tr)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    for base in (root, day_dir):
        for f in Path(base).rglob("*"):
            if f.is_file():
                assert SECRET.encode() not in f.read_bytes(), f


def test_request_recorded_in_ledger_is_normalised_request(env):
    p, led, c, _ = make(env)
    mcp(p, led, c, Transport(), term="  rem   sleep ")
    row = led.rows("run")[-1]
    assert row["request"] == {"term": "rem sleep", "limit": 3}
    assert row["key"] == cache_mod.request_key("mcp", "search_literature",
                                               {"term": "rem sleep", "limit": 3})


def test_default_day_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert ledger_mod.default_dir() == tmp_path / "xdg" / "scieflow" / "scite"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert ledger_mod.default_dir() == tmp_path / "home" / ".cache" / "scieflow" / "scite"


# --- review findings ----------------------------------------------------------------


def test_gateway_requires_a_run(env):
    root, _, day_dir = env
    p = make_policy(root)
    led = ledger_mod.Ledger(None, p, day_dir=day_dir, now=Clock())
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=Clock())
    tr = Transport()
    for source in ("mcp", "rest"):
        with pytest.raises(pol.PolicyError):
            gateway.request(p, c, led, source=source, op="search_literature",
                            params={"term": "x"}, round=1, ttl_class="search", transport=tr)
    assert tr.calls == []


@pytest.mark.parametrize("bad", [None, 0, -1, "1", True])
def test_mcp_requires_a_round(env, bad):
    p, led, c, _ = make(env)
    tr = Transport()
    with pytest.raises(ValueError):
        mcp(p, led, c, tr, round_=bad)
    assert tr.calls == []


@pytest.mark.parametrize("bad", [None, -1, "3", 2.5])
def test_bad_result_count_still_ledgered_and_emitted(env, bad):
    _, ws, _ = env
    p, led, c, _ = make(env)

    def tr(op, params):
        return {"x": 1}, bad

    with pytest.raises(ValueError):
        mcp(p, led, c, tr)
    assert led.calls("run") == 1
    assert led.rows("run")[-1]["status"] == "error"
    assert len(events.read(ws, types=("integration.call",))) == 1


def test_unserialisable_value_is_counted_emitted_and_returned(env):
    _, ws, _ = env
    p, led, c, _ = make(env)
    marker = object()

    def tr(op, params):
        return {"x": marker}, 1

    r = mcp(p, led, c, tr)
    assert r.value["x"] is marker and r.cached is False
    assert led.calls("run") == 1
    assert len(events.read(ws, types=("integration.call",))) == 1


def test_cache_entry_expires_by_its_stored_class(env):
    _, _, day_dir = env
    clock = Clock()
    c = cache_mod.Cache(day_dir / "cache", {"metadata": 30, "search": 7}, now=clock)
    key = cache_mod.request_key("mcp", "search_literature", {"term": "x"})
    c.put(key, "search", {"v": 1})
    clock.advance(days=20)
    assert c.get(key, "metadata") is None


def test_day_budget_is_atomic_across_concurrent_callers(env):
    import threading
    import time

    root, ws, day_dir = env
    p = make_policy(root, max_mcp_calls_per_day=1)
    ws2 = root / "workspace" / "other"
    ws2.mkdir()
    calls, errors = [], []

    def tr(op, params):
        calls.append(op)
        time.sleep(0.3)
        return {"ok": True}, 1

    def worker(w, term):
        led = ledger_mod.Ledger(w, p, day_dir=day_dir)
        c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days)
        try:
            gateway.request(p, c, led, source="mcp", op="search_literature",
                            params={"term": term}, round=1, ttl_class="search", transport=tr)
        except ledger_mod.BudgetError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(ws, "a")),
               threading.Thread(target=worker, args=(ws2, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(errors) == 1
