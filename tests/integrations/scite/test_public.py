"""scite public REST tier (no key): bulk tallies (chunked), papers, 404 -> unknown,
429 honours Retry-After, per-second rate limit — all through the gateway
(policy -> cache -> ledger -> transport). Recorded fixtures, no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import responses
import yaml

from scieflow.core import events
from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import policy as pol
from scieflow.integrations.scite import public
from scieflow.research.lib.http import HTTPFailure

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "scite" / "rest"
SECRET = "sk-sci...6789"
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
API = "https://api.scite.ai"


def fixture(name):
    return json.loads((FIX / name).read_text())


class FakeClock:
    def __init__(self):
        self.t = 50.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    return root, ws, tmp_path / "cachehome" / "scite"


def client(env, environ=None, write=True, **cfg):
    root, ws, day_dir = env
    if write:
        (root / "config" / "scite.yml").write_text(yaml.safe_dump(cfg))
    p = pol.load(root, environ={"SCITE_TOKEN": SECRET} if environ is None else environ)
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=lambda: T0)
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=lambda: T0)
    fc = FakeClock()
    cl = public.PublicClient(p, c, led, sleep=fc.sleep, clock=fc.clock)
    return cl, led, fc


def tallies_body(dois):
    return {"tallies": {d: {"doi": d, "total": 10, "supporting": 3, "contradicting": 2,
                            "mentioning": 5, "unclassified": 0, "citingPublications": 9}
                        for d in dois}}


def echo_tallies(request):
    dois = json.loads(request.body)
    return 200, {}, json.dumps(tallies_body(dois))


# --- bulk tallies ---------------------------------------------------------------------


@responses.activate
def test_bulk_tallies_one_request_recorded_fixture(env):
    cl, led, _ = client(env)
    responses.post(f"{API}/tallies", json=fixture("tallies_bulk.json"))
    out = cl.tallies(["10.1016/J.BIOPSYCH.2005.08.012", "https://doi.org/10.1093/brain/awn044",
                      "10.1234/not-in-scite"], round=1)
    assert len(responses.calls) == 1
    sent = json.loads(responses.calls[0].request.body)
    assert sent == sorted(["10.1016/j.biopsych.2005.08.012", "10.1093/brain/awn044",
                           "10.1234/not-in-scite"])            # a JSON array of DOIs
    assert out["10.1016/j.biopsych.2005.08.012"] == {
        "status": "ok", "supporting": 27, "contrasting": 6, "mentioning": 308,
        "unclassified": 6, "total": 347, "citing_publications": 436}
    assert out["10.1093/brain/awn044"]["contrasting"] == 1
    assert out["10.1234/not-in-scite"] == {"status": "unknown"}   # absent from response
    rows = led.rows()
    assert [(r["source"], r["op"], r["results"], r["status"]) for r in rows] == [
        ("rest", "tallies", 2, "ok")]
    evs = [e for e in events.read(env[1]) if e["type"] == "integration.call"]
    assert len(evs) == 1 and evs[0]["data"]["source"] == "rest"


@responses.activate
def test_bulk_tallies_chunked_at_configured_max(env):
    cl, led, _ = client(env, rest_max_dois_per_request=2)
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    dois = [f"10.1000/p{i}" for i in range(5)]
    out = cl.tallies(dois, round=1)
    bodies = [json.loads(c.request.body) for c in responses.calls]
    assert [len(b) for b in bodies] == [2, 2, 1]
    assert sorted(sum(bodies, [])) == sorted(dois)
    assert all(out[d]["status"] == "ok" for d in dois)
    assert led.calls(source="rest") == 3


@responses.activate
def test_tallies_second_time_is_cached_and_free(env):
    cl, led, _ = client(env)
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    first = cl.tallies(["10.1000/a", "10.1000/b"], round=1)
    again = cl.tallies(["10.1000/B", "doi:10.1000/a"], round=2)
    assert len(responses.calls) == 1
    assert first == again
    assert led.cached(source="rest") == 1


@responses.activate
def test_invalid_dois_never_sent(env):
    cl, _, _ = client(env)
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    out = cl.tallies(["10.1000/ok", "../../etc/passwd", "10.1000/../x", "not a doi"], round=1)
    sent = json.loads(responses.calls[0].request.body)
    assert sent == ["10.1000/ok"]
    assert out["../../etc/passwd"] == {"status": "invalid"}
    assert out["not a doi"] == {"status": "invalid"}


@responses.activate
def test_only_invalid_dois_make_no_request(env):
    cl, led, _ = client(env)
    out = cl.tallies(["nope"], round=1)
    assert out == {"nope": {"status": "invalid"}}
    assert len(responses.calls) == 0 and led.rows() == []


@responses.activate
def test_malformed_tally_entry_is_unknown(env):
    cl, _, _ = client(env)
    responses.post(f"{API}/tallies", json={"tallies": {
        "10.1000/a": {"total": "many", "supporting": 1, "contradicting": 0,
                      "mentioning": 0, "unclassified": 0},
        "10.1000/b": {"total": -1, "supporting": 1, "contradicting": 0,
                      "mentioning": 0, "unclassified": 0}}})
    out = cl.tallies(["10.1000/a", "10.1000/b"], round=1)
    assert out == {"10.1000/a": {"status": "unknown"}, "10.1000/b": {"status": "unknown"}}


@responses.activate
def test_malformed_body_is_recorded_and_chunk_unavailable(env):
    cl, led, _ = client(env)
    responses.post(f"{API}/tallies", body="<html>oops</html>")
    out = cl.tallies(["10.1000/a"], round=1)
    assert out == {"10.1000/a": {"status": "unavailable", "error": "MALFORMED"}}
    assert led.rows()[0]["status"] == "error" and led.rows()[0]["error"] == "MALFORMED"


@responses.activate
def test_tallies_429_honours_retry_after(env):
    cl, led, fc = client(env)
    responses.post(f"{API}/tallies", status=429, headers={"Retry-After": "3"})
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    out = cl.tallies(["10.1000/a"], round=1)
    assert out["10.1000/a"]["status"] == "ok"
    assert 3.0 in fc.sleeps
    assert len(responses.calls) == 2
    assert [r["status"] for r in led.rows()] == ["ok"]      # one logical call


@responses.activate
def test_rate_exhausted_stops_remaining_chunks(env):
    cl, led, _ = client(env, rest_max_dois_per_request=1)
    responses.post(f"{API}/tallies", status=429, headers={"Retry-After": "7200"})
    out = cl.tallies(["10.1000/a", "10.1000/b", "10.1000/c"], round=1)
    assert len(responses.calls) == 1                        # not hammered after RATE
    assert {v["status"] for v in out.values()} == {"unavailable"}
    assert {v["error"] for v in out.values()} == {"RATE"}
    assert [r.get("error") for r in led.rows()] == ["RATE"]


@responses.activate
def test_transient_chunk_failure_others_continue(env):
    cl, led, _ = client(env, rest_max_dois_per_request=1)
    calls = {"n": 0}

    def cb(request):
        dois = json.loads(request.body)
        if dois == ["10.1000/a"]:
            return 503, {}, ""
        return 200, {}, json.dumps(tallies_body(dois))

    responses.add_callback(responses.POST, f"{API}/tallies", callback=cb)
    out = cl.tallies(["10.1000/a", "10.1000/b"], round=1)
    assert out["10.1000/a"] == {"status": "unavailable", "error": "TRANSIENT"}
    assert out["10.1000/b"]["status"] == "ok"
    assert [r["status"] for r in led.rows()] == ["error", "ok"]


@responses.activate
def test_rate_limit_per_second(env):
    cl, _, fc = client(env, rest_rate_per_s=2, rest_max_dois_per_request=1)
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    cl.tallies(["10.1000/a", "10.1000/b", "10.1000/c"], round=1)
    assert fc.sleeps == [0.5, 0.5]


@responses.activate
def test_no_authorization_header_even_with_token(env):
    cl, _, _ = client(env)
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    responses.get(f"{API}/papers/10.1016/j.biopsych.2005.08.012", json=fixture("paper.json"))
    cl.tallies(["10.1000/a"], round=1)
    cl.paper("10.1016/j.biopsych.2005.08.012", round=1)
    for c in responses.calls:
        assert "Authorization" not in c.request.headers
        assert SECRET not in json.dumps(dict(c.request.headers))


@responses.activate
def test_scite_off_sends_nothing(env):
    cl, led, _ = client(env, write=False)
    with pytest.raises(pol.PolicyError):
        cl.tallies(["10.1000/a"], round=1)
    with pytest.raises(pol.PolicyError):
        cl.paper("10.1000/a", round=1)
    assert len(responses.calls) == 0 and led.rows() == []


@responses.activate
def test_public_tier_works_without_token(env):
    cl, _, _ = client(env, environ={})
    assert cl.policy.tier == "public"
    responses.add_callback(responses.POST, f"{API}/tallies", callback=echo_tallies)
    assert cl.tallies(["10.1000/a"], round=1)["10.1000/a"]["status"] == "ok"


# --- papers ---------------------------------------------------------------------------


@responses.activate
def test_paper_recorded_fixture(env):
    cl, led, _ = client(env)
    responses.get(f"{API}/papers/10.1016/j.biopsych.2005.08.012", json=fixture("paper.json"))
    p = cl.paper("https://doi.org/10.1016/J.BIOPSYCH.2005.08.012", round=1)
    assert p["status"] == "ok"
    assert p["doi"] == "10.1016/j.biopsych.2005.08.012"
    assert p["title"].startswith("Association between Amygdala")
    assert p["year"] == 2006 and p["journal"] == "Biological Psychiatry"
    assert p["retracted"] is False and p["notices"] == []
    assert led.rows()[0]["op"] == "paper"


@responses.activate
def test_paper_retracted_notices(env):
    cl, _, _ = client(env)
    responses.get(f"{API}/papers/10.1016/s0140-6736(97)11096-0",
                  json=fixture("paper_retracted.json"))
    p = cl.paper("10.1016/S0140-6736(97)11096-0", round=1)
    assert p["retracted"] is True
    assert p["notices"] == [{"status": "Retracted", "date": "2010-02-06",
                             "notice_doi": "10.1016/s0140-6736(10)60175-4"}]


@responses.activate
def test_paper_404_is_unknown_and_cached(env):
    cl, led, _ = client(env)
    responses.get(f"{API}/papers/10.1234/missing", status=404, json=fixture("not_found.json"))
    assert cl.paper("10.1234/missing", round=1) == {"status": "unknown",
                                                    "doi": "10.1234/missing"}
    assert cl.paper("10.1234/missing", round=1)["status"] == "unknown"
    assert len(responses.calls) == 1
    assert [(r["status"], r["cached"], r["results"]) for r in led.rows()] == [
        ("ok", False, 0), ("ok", True, 0)]


@responses.activate
def test_paper_doi_is_url_quoted(env):
    cl, _, _ = client(env)
    responses.get(f"{API}/papers/10.1234/a%3Fb%23c", status=404)
    assert cl.paper("10.1234/a?b#c", round=1)["status"] == "unknown"
    assert responses.calls[0].request.url == f"{API}/papers/10.1234/a%3Fb%23c"


@responses.activate
def test_paper_invalid_doi_never_sent(env):
    cl, led, _ = client(env)
    assert cl.paper("10.1234/../../admin", round=1) == {"status": "invalid"}
    assert len(responses.calls) == 0 and led.rows() == []


@responses.activate
def test_paper_429_retry_after(env):
    cl, _, fc = client(env)
    url = f"{API}/papers/10.1016/j.biopsych.2005.08.012"
    responses.get(url, status=429, headers={"Retry-After": "1"})
    responses.get(url, json=fixture("paper.json"))
    assert cl.paper("10.1016/j.biopsych.2005.08.012", round=1)["status"] == "ok"
    assert 1.0 in fc.sleeps


@responses.activate
def test_paper_transient_failure_raises_classified(env):
    cl, led, _ = client(env)
    url = f"{API}/papers/10.1000/x"
    for _ in range(3):
        responses.get(url, status=502)
    with pytest.raises(HTTPFailure) as exc:
        cl.paper("10.1000/x", round=1)
    assert exc.value.classification == "TRANSIENT"
    assert led.rows()[0]["error"] == "TRANSIENT"


@responses.activate
def test_failures_are_not_cached(env):
    cl, _, _ = client(env)
    url = f"{API}/papers/10.1000/x"
    for _ in range(3):
        responses.get(url, status=502)
    responses.get(url, json={"doi": "10.1000/x", "title": "T"})
    with pytest.raises(HTTPFailure):
        cl.paper("10.1000/x", round=1)
    assert cl.paper("10.1000/x", round=1)["status"] == "ok"
