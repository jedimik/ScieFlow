"""Opt-in live smoke test against the real scite service — excluded from the default run.

Run explicitly (spends one MCP call of your scite quota):

    uv sync --group scite
    SCITE_TOKEN=… uv run pytest -m scite_live tests/integrations/scite/test_live.py

It makes exactly one public REST request (bulk `POST /tallies` for one DOI) and one
MCP `search_literature` call with `limit: 1`, through the same policy → cache → ledger
path as the pipeline, with retries off. The cache is a fresh temporary directory (so
both calls really go out); the machine's day ledger is the real one, so the MCP call
counts against `max_mcp_calls_per_day`. The token is read from `$SCITE_TOKEN` by the
client and never printed.
"""

from __future__ import annotations

import os

import pytest
import yaml

from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite import policy as pol

pytestmark = pytest.mark.scite_live

DOI = "10.1038/s41586-020-2012-7"


@pytest.fixture
def live(tmp_path):
    if not (os.environ.get("SCITE_TOKEN") or "").strip():
        pytest.skip("SCITE_TOKEN not set")
    if not smcp.sdk_available():
        pytest.skip(f"scite MCP SDK not installed ({smcp.INSTALL_HINT})")
    pytest.importorskip("requests")
    if smcp.in_dispatch():
        pytest.skip("inside a ScieFlow dispatch: scite is coordinator-only")
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "scite.yml").write_text(yaml.safe_dump({"token_env": "SCITE_TOKEN"}))
    ws = root / "workspace" / "live"
    ws.mkdir(parents=True)
    p = pol.load(root)
    assert p.tier == "mcp"
    led = ledger_mod.Ledger(ws, p)
    cache = cache_mod.Cache(tmp_path / "cache", p.cache_ttl_days)
    return p, led, cache


def test_one_tallies_request_and_one_search(live):
    from scieflow.integrations.scite import public

    p, led, cache = live
    tallies = public.PublicClient(p, cache, led, retries=1).tallies([DOI])
    assert tallies[DOI]["status"] == "ok", tallies[DOI]
    assert tallies[DOI]["tally"]["total"] >= 0

    res = smcp.MCPClient(p, cache, led, retries=0).call(
        "search_literature", {"term": "coronavirus bat origin pneumonia", "limit": 1}, round=1)
    assert res.cached is False
    assert isinstance(res.value.get("hits"), list) and len(res.value["hits"]) <= 1

    rows = led.rows("run")
    assert [(r["source"], r["op"], r["cached"], r["status"]) for r in rows] == [
        ("rest", "tallies", False, "ok"), ("mcp", "search_literature", False, "ok")]
