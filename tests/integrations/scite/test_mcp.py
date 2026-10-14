"""scite MCP client: recorded JSON-RPC fixtures (initialize, tools/list, tools/call for
search_literature and citation_graph, auth / quota / rate / malformed errors), error
classification (AUTH, QUOTA without retry, RATE honouring Retry-After, TRANSIENT bounded
retry, NOT_INSTALLED with the install hint), `scite.unavailable` once per run, and the
refusal inside a ScieFlow dispatch. No network: the SDK-free tests replay the fixtures
through a fake RPC, the SDK tests serve them through `httpx2.MockTransport`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scieflow.core import events
from scieflow.integrations.scite import cache as cache_mod
from scieflow.integrations.scite import ledger as ledger_mod
from scieflow.integrations.scite import mcp as smcp
from scieflow.integrations.scite import policy as pol

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "scite" / "mcp"
SECRET = "sk-scite-FIXTURE-0123456789abcdef"
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
HOST_ENV = {"SCITE_TOKEN": SECRET, "UV_CACHE_DIR": "/home/u/.cache/uv"}
DISPATCH_ENV = {"SCITE_TOKEN": SECRET,
                "UV_CACHE_DIR": "/proj/workspace/demo/agents/claude/.uv-cache"}
SEARCH_ARGS = {"term": "REM sleep behaviour disorder", "limit": 2}


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text())


# --- fake RPC replaying fixtures (what the SDK adapter hands the client) ----------------


def fixture_reply(name: str) -> dict:
    """Interpret one recorded HTTP exchange as the SDK adapter does: a CallToolResult
    dict, or an `RPCError` carrying the HTTP status / JSON-RPC error / malformed flag."""
    http = fixture(name)["http"]
    status = http["status"]
    if status >= 400:
        body = http.get("body")
        raise smcp.RPCError("Server returned an error response", status=status,
                            retry_after=http["headers"].get("retry-after"),
                            body=json.dumps(body) if body is not None else http.get("raw"))
    if "raw" in http:
        try:
            json.loads(http["raw"])
        except ValueError:
            raise smcp.RPCError("Failed to parse JSON response", code=-32700,
                                malformed=True) from None
    body = http["body"]
    if "error" in body:
        raise smcp.RPCError(body["error"]["message"], code=body["error"]["code"])
    return body["result"]


class FakeRPC:
    def __init__(self, *script: str):
        self.script = list(script)
        self.calls: list[tuple[str, dict]] = []
        self.tokens: list[str] = []

    def factory(self, url: str, token: str):
        self.tokens.append(token)

        def rpc(tool: str, args: dict) -> dict:
            self.calls.append((tool, dict(args)))
            name = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            return fixture_reply(name)

        return rpc


class Sleeps(list):
    def __call__(self, s):
        self.append(s)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    return root, ws, tmp_path / "cachehome" / "scite"


def make(env, fake: FakeRPC | None = None, environ=None, write=True, retries=2,
         max_retry_after=60.0, **cfg):
    root, ws, day_dir = env
    if write:
        (root / "config" / "scite.yml").write_text(yaml.safe_dump(cfg))
    environ = HOST_ENV if environ is None else environ
    p = pol.load(root, environ=environ)
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=lambda: T0)
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=lambda: T0)
    sleeps = Sleeps()
    client = smcp.MCPClient(p, c, led, environ=environ, sleep=sleeps, jitter=lambda: 0.5,
                            retries=retries, max_retry_after=max_retry_after,
                            rpc_factory=fake.factory if fake is not None else None)
    return client, led, sleeps


def unavailable(ws):
    return events.read(ws, types=("scite.unavailable",))


def no_secret_anywhere(ws, day_dir, *texts):
    blobs = list(texts)
    for path in list(ws.rglob("*")) + list(day_dir.rglob("*")):
        if path.is_file():
            blobs.append(path.read_text(errors="replace"))
    assert not any(SECRET in b for b in blobs)


# --- dispatch detection ------------------------------------------------------------------


@pytest.mark.parametrize("environ, expected", [
    ({"UV_CACHE_DIR": "/p/workspace/demo/agents/x/.uv-cache"}, True),
    ({"UV_CACHE_DIR": "/p/workspace/demo/agents/x/.uv-cache/"}, True),
    ({"UV_CACHE_DIR": "/home/u/.cache/uv"}, False),
    ({"UV_CACHE_DIR": ""}, False),
    ({}, False),
    ({"SCIEFLOW_DISPATCH": "1"}, True),
    ({"SCIEFLOW_DISPATCH": "0"}, False),
])
def test_in_dispatch_heuristic(environ, expected):
    assert smcp.in_dispatch(environ) is expected


def test_dispatch_marker_matches_the_sandbox_cache_dirname():
    from scieflow.core import sandbox
    env = sandbox.environment(Path("/tmp/x/agents/a"), env={})
    assert smcp.in_dispatch(env)


def test_call_inside_a_dispatch_is_refused_before_anything_else(env):
    fake = FakeRPC("call_search_literature.json")
    client, led, _ = make(env, fake, environ=DISPATCH_ENV)
    with pytest.raises(smcp.DispatchRefused) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert isinstance(ei.value, pol.PolicyError)
    assert pol.exit_code(ei.value) == pol.EXIT_REFUSED_POLICY
    assert "dispatch" in str(ei.value)
    assert fake.tokens == [] and fake.calls == []      # token never read, nothing sent
    assert led.rows("run") == [] and led.rows("day") == []
    assert events.read(env[1]) == []


class RecordingEnv(dict):
    """An environ that remembers which variables were looked up."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reads: list[str] = []

    def get(self, key, default=None):
        self.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)


def test_dispatch_refusal_happens_before_the_token_is_looked_up(env):
    rec = RecordingEnv(DISPATCH_ENV)
    client, _, _ = make(env, FakeRPC("call_search_literature.json"), environ=DISPATCH_ENV)
    client.environ = rec
    with pytest.raises(smcp.DispatchRefused):
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert "SCITE_TOKEN" not in rec.reads
    assert set(rec.reads) <= {"SCIEFLOW_DISPATCH", "UV_CACHE_DIR"}


def test_dispatch_refusal_wins_even_when_scite_is_off(env):
    client, _, _ = make(env, FakeRPC("call_search_literature.json"), environ=DISPATCH_ENV,
                        write=False)
    with pytest.raises(smcp.DispatchRefused):
        client.call("search_literature", SEARCH_ARGS, round=1)


# --- NOT_INSTALLED / policy precedence ------------------------------------------------------


def test_not_installed_gives_the_install_hint_and_sends_nothing(env, monkeypatch):
    monkeypatch.setattr(smcp, "sdk_available", lambda: False)
    client, led, _ = make(env)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "NOT_INSTALLED"
    assert "uv sync --group scite" in str(ei.value)
    assert led.rows("run") == [] and events.read(env[1]) == []


def test_policy_refusal_comes_before_not_installed(env, monkeypatch):
    monkeypatch.setattr(smcp, "sdk_available", lambda: False)
    client, _, _ = make(env, write=False)                     # no config/scite.yml
    with pytest.raises(pol.PolicyError):
        client.call("search_literature", SEARCH_ARGS, round=1)
    client, _, _ = make(env, allowed_tools=["citation_graph"])
    with pytest.raises(pol.PolicyError, match="allowed_tools"):
        client.call("search_literature", SEARCH_ARGS, round=1)


def test_no_token_is_a_policy_refusal(env):
    fake = FakeRPC("call_search_literature.json")
    client, led, _ = make(env, fake, environ={"UV_CACHE_DIR": "/home/u/.cache/uv"})
    with pytest.raises(pol.PolicyError):
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert fake.calls == [] and led.rows("run") == []


def test_token_file_is_read_at_call_time(env, tmp_path):
    tf = tmp_path / "scite-token"
    tf.write_text(SECRET + "\n")
    fake = FakeRPC("call_search_literature.json")
    client, _, _ = make(env, fake, environ={}, token_file=str(tf))
    client.call("search_literature", SEARCH_ARGS, round=1)
    assert fake.tokens == [SECRET]


# --- successful calls -----------------------------------------------------------------------


def test_search_literature_returns_hits_and_is_ledgered(env):
    fake = FakeRPC("call_search_literature.json")
    client, led, _ = make(env, fake)
    res = client.call("search_literature", SEARCH_ARGS, round=1)
    assert res.cached is False and res.results == 2
    assert [h["doi"] for h in res.value["hits"]] == ["10.1038/s41586-020-2012-7",
                                                     "10.1000/example.2"]
    assert fake.tokens == [SECRET]                            # Bearer token from policy
    assert fake.calls == [("search_literature", {"term": "REM sleep behaviour disorder",
                                                 "limit": 2})]
    (row,) = led.rows("run")
    assert row["source"] == "mcp" and row["op"] == "search_literature"
    assert row["status"] == "ok" and row["results"] == 2 and row["round"] == 1
    (ev,) = events.read(env[1], types=("integration.call",))
    assert ev["data"]["op"] == "search_literature" and ev["data"]["status"] == "ok"
    no_secret_anywhere(env[1], env[2])


def test_identical_second_call_is_a_free_cache_hit(env):
    fake = FakeRPC("call_search_literature.json")
    client, led, _ = make(env, fake)
    client.call("search_literature", SEARCH_ARGS, round=1)
    again = client.call("search_literature", {"limit": 2, "term": "  REM sleep  behaviour disorder"},
                        round=1)
    assert again.cached is True and len(fake.calls) == 1
    assert led.calls("run") == 1 and led.cached("run") == 1


def test_citation_graph_counts_edges(env):
    fake = FakeRPC("call_citation_graph.json")
    client, led, _ = make(env, fake)
    res = client.call("citation_graph", {"seeds": ["10.1038/s41586-020-2012-7"],
                                         "direction": "both", "include_intent": True}, round=1)
    assert res.results == 3
    assert {e["type"] for e in res.value["edges"]} == {"supporting", "contrasting", "mentioning"}
    assert led.rows("run")[0]["results"] == 3


# --- error classification -------------------------------------------------------------------


def test_auth_error_is_not_retried_and_marks_scite_unavailable(env):
    fake = FakeRPC("error_auth.json")
    client, led, sleeps = make(env, fake)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "AUTH" and ei.value.status == 401
    assert len(fake.calls) == 1 and sleeps == []
    (row,) = led.rows("run")
    assert row["status"] == "error" and row["error"] == "AUTH"
    (ev,) = unavailable(env[1])
    assert ev["data"]["reason"] == "AUTH"
    no_secret_anywhere(env[1], env[2], str(ei.value))


@pytest.mark.parametrize("name", ["error_quota.json", "error_quota_tool.json"])
def test_quota_error_is_never_retried_and_emits_unavailable_once_per_run(env, name):
    fake = FakeRPC(name)
    client, led, sleeps = make(env, fake)
    for _ in range(2):
        with pytest.raises(smcp.MCPFailure) as ei:
            client.call("search_literature", SEARCH_ARGS, round=1)
        assert ei.value.classification == "QUOTA"
    assert len(fake.calls) == 2 and sleeps == []           # one attempt per call, no retry
    assert [r["error"] for r in led.rows("run")] == ["QUOTA", "QUOTA"]
    (ev,) = unavailable(env[1])                              # once per run
    assert ev["data"]["reason"] == "QUOTA"


def test_unavailable_is_once_per_run_across_clients(env):
    fake = FakeRPC("error_quota.json")
    for _ in range(2):
        client, _, _ = make(env, fake)
        with pytest.raises(smcp.MCPFailure):
            client.call("search_literature", SEARCH_ARGS, round=1)
    assert len(unavailable(env[1])) == 1


def test_rate_limit_reached_429_honours_retry_after():
    f = smcp.classify(smcp.RPCError("x", status=429, retry_after="3",
                                    body='{"message": "Rate limit reached"}'))
    assert f.classification == "RATE" and f.retry_after == 3.0


def test_unexpected_sdk_failure_is_not_retried(env):
    class Odd(FakeRPC):
        def factory(self, url, token):
            def rpc(tool, args):
                self.calls.append((tool, args))
                raise smcp.RPCError("MCP session failed (RuntimeError)", unexpected=True)
            return rpc

    fake = Odd()
    client, _, sleeps = make(env, fake)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "TOOL_ERROR"
    assert len(fake.calls) == 1 and sleeps == []


def test_quota_status_402_is_quota():
    f = smcp.classify(smcp.RPCError("Server returned an error response", status=402))
    assert f.classification == "QUOTA"


def test_429_that_names_the_quota_is_quota_not_rate():
    f = smcp.classify(smcp.RPCError("x", status=429, body='{"error": "monthly quota exceeded"}'))
    assert f.classification == "QUOTA"


@pytest.mark.parametrize("name", ["malformed.json", "malformed_payload.json",
                                  "error_server.json"])
def test_transient_errors_get_bounded_jittered_retries(env, name):
    fake = FakeRPC(name)
    client, led, sleeps = make(env, fake, retries=2)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "TRANSIENT"
    assert len(fake.calls) == 3                              # 1 + 2 retries, then stop
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1] <= smcp.MAX_BACKOFF
    assert [r["error"] for r in led.rows("run")] == ["TRANSIENT"] * 3   # every attempt counts
    assert unavailable(env[1]) == []


def test_transient_then_success_returns_the_answer(env):
    fake = FakeRPC("malformed.json", "call_search_literature.json")
    client, led, sleeps = make(env, fake)
    res = client.call("search_literature", SEARCH_ARGS, round=1)
    assert res.results == 2 and len(fake.calls) == 2 and len(sleeps) == 1
    assert [r["status"] for r in led.rows("run")] == ["error", "ok"]


def test_retries_stop_at_the_budget(env):
    fake = FakeRPC("error_server.json")
    client, led, _ = make(env, fake, retries=5, max_mcp_calls_per_round=2)
    with pytest.raises(ledger_mod.BudgetError):
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert len(fake.calls) == 2 and led.calls("run", round=1) == 2


def test_rate_limit_honours_retry_after(env):
    fake = FakeRPC("error_rate.json", "call_search_literature.json")
    client, led, sleeps = make(env, fake)
    res = client.call("search_literature", SEARCH_ARGS, round=1)
    assert res.results == 2 and sleeps == [2.0]
    assert [r.get("error") for r in led.rows("run")] == ["RATE", None]


def test_rate_limit_beyond_the_cap_is_a_boundary(env):
    fake = FakeRPC("error_rate.json")
    client, _, sleeps = make(env, fake, max_retry_after=1.0)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "RATE" and ei.value.retry_after == 2.0
    assert len(fake.calls) == 1 and sleeps == []


def test_rate_limit_retries_are_bounded(env):
    fake = FakeRPC("error_rate.json")
    client, _, sleeps = make(env, fake, retries=2)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "RATE"
    assert len(fake.calls) == 3 and sleeps == [2.0, 2.0]


def test_generic_tool_error_is_not_retried():
    result = {"content": [{"type": "text", "text": "Invalid arguments: seeds must not be empty"}],
              "isError": True}
    with pytest.raises(smcp.MCPFailure) as ei:
        smcp.parse_result("citation_graph", result)
    assert ei.value.classification == "TOOL_ERROR"
    assert smcp.classify(smcp.RPCError("Invalid params", code=-32602)).classification \
        == "TOOL_ERROR"


@pytest.mark.parametrize("err, expected", [
    (smcp.RPCError("x", status=401), "AUTH"),
    (smcp.RPCError("x", status=403), "AUTH"),
    (smcp.RPCError("x", status=429), "RATE"),
    (smcp.RPCError("x", status=500), "TRANSIENT"),
    (smcp.RPCError("x", status=502), "TRANSIENT"),
    (smcp.RPCError("x", status=408), "TRANSIENT"),
    (smcp.RPCError("connection reset", network=True), "TRANSIENT"),
    (smcp.RPCError("Failed to parse JSON response", code=-32700, malformed=True), "TRANSIENT"),
    (smcp.RPCError("Server returned an error response", code=-32603), "TRANSIENT"),
    (smcp.RPCError("Invalid token supplied", code=-32001), "AUTH"),
    (smcp.RPCError("Rate limit exceeded, slow down", code=-32000), "RATE"),
    (smcp.RPCError("You have used all your credits", code=-32000), "QUOTA"),
    (smcp.RPCError("x", status=404), "TOOL_ERROR"),
    (smcp.RPCError("x", status=429, body='{"message": "Rate limit reached"}'), "RATE"),
    (smcp.RPCError("x", status=429, body="Too many requests: hourly limit reached"), "RATE"),
    (smcp.RPCError("You have exceeded your rate limit", code=-32000), "RATE"),
    (smcp.RPCError("insufficient_scope", code=-32000), "AUTH"),
    (smcp.RPCError("Insufficient permissions for this tool", code=-32000), "AUTH"),
    (smcp.RPCError("Monthly MCP usage limit reached", code=-32000), "QUOTA"),
    (smcp.RPCError("MCP session failed (RuntimeError)", unexpected=True), "TOOL_ERROR"),
])
def test_classification_table(err, expected):
    assert smcp.classify(err).classification == expected


@pytest.mark.parametrize("tool, result", [
    ("search_literature", {"content": [{"type": "text", "text": json.dumps({"no": "hits"})}]}),
    ("citation_graph", {"content": [{"type": "text", "text": json.dumps({"edges": "x"})}]}),
    ("search_literature", {"content": "not a list"}),
    ("search_literature", {"content": []}),
    ("search_literature", "not a dict"),
])
def test_malformed_payloads_are_transient(tool, result):
    with pytest.raises(smcp.MCPFailure) as ei:
        smcp.parse_result(tool, result)
    assert ei.value.classification == "TRANSIENT"


def test_structured_content_is_preferred():
    value, n = smcp.parse_result("search_literature", {
        "content": [{"type": "text", "text": "summary only"}],
        "structuredContent": {"hits": [{"doi": "10.1/a"}]}})
    assert value == {"hits": [{"doi": "10.1/a"}]} and n == 1


def test_server_text_echoing_the_token_is_redacted(env):
    class Echo(FakeRPC):
        def factory(self, url, token):
            def rpc(tool, args):
                self.calls.append((tool, args))
                raise smcp.RPCError(f"bad credentials {token}", code=-32001)
            return rpc

    fake = Echo()
    client, led, _ = make(env, fake)
    with pytest.raises(smcp.MCPFailure) as ei:
        client.call("search_literature", SEARCH_ARGS, round=1)
    assert ei.value.classification == "AUTH"
    assert SECRET not in str(ei.value) and SECRET not in repr(ei.value)
    no_secret_anywhere(env[1], env[2], str(ei.value))


def test_cli_exit_codes():
    assert smcp.exit_code(smcp.DispatchRefused("x")) == pol.EXIT_REFUSED_POLICY
    assert smcp.exit_code(smcp.MCPFailure("x", "NOT_INSTALLED")) == smcp.EXIT_NOT_INSTALLED
    assert smcp.exit_code(smcp.MCPFailure("x", "QUOTA")) == smcp.EXIT_QUOTA
    assert smcp.exit_code(smcp.MCPFailure("x", "RATE")) == smcp.EXIT_QUOTA
    assert smcp.exit_code(smcp.MCPFailure("x", "TRANSIENT")) == 1


# --- through the real SDK (optional group) ---------------------------------------------------


class FixtureServer:
    """Serves recorded JSON-RPC exchanges to the official SDK through httpx2.MockTransport."""

    def __init__(self, calls: str, *, initialize: str = "initialize.json",
                 tools_list: str = "tools_list.json"):
        self.by_method = {"initialize": initialize, "tools/list": tools_list,
                          "tools/call": calls}
        self.seen: list[tuple[str, str | None]] = []

    def handler(self, request):
        import httpx2

        if request.method != "POST":
            return httpx2.Response(405)
        msg = json.loads(request.content)
        method = msg.get("method")
        self.seen.append((method, request.headers.get("authorization")))
        if "id" not in msg:                                   # notifications
            return httpx2.Response(202)
        name = self.by_method.get(method)
        if name is None:
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        http = fixture(name)["http"] if isinstance(name, str) else name
        headers = dict(http["headers"])
        if "raw" in http:
            return httpx2.Response(http["status"], content=http["raw"].encode(), headers=headers)
        body = json.loads(json.dumps(http["body"]))
        if isinstance(body, dict) and body.get("jsonrpc"):
            body["id"] = msg["id"]
            if method == "initialize" and "result" in body:
                body["result"]["protocolVersion"] = msg["params"]["protocolVersion"]
        return httpx2.Response(http["status"], json=body, headers=headers)


@pytest.fixture
def sdk():
    pytest.importorskip("mcp", reason="scite MCP SDK not installed (uv sync --group scite)")
    import httpx2

    def run(calls, tool="search_literature", args=None, *, initialize="initialize.json",
            tools_list="tools_list.json"):
        server = FixtureServer(calls, initialize=initialize, tools_list=tools_list)
        rpc = smcp.sdk_rpc(smcp.MCP_URL, SECRET, timeout=5,
                           http_transport=httpx2.MockTransport(server.handler))
        return server, (lambda: rpc(tool, args if args is not None else SEARCH_ARGS))

    return run


def test_sdk_initialize_and_tools_call_send_the_bearer_token(sdk):
    server, go = sdk("call_search_literature.json")
    result = go()
    value, n = smcp.parse_result("search_literature", result)
    assert n == 2 and value["hits"][0]["doi"] == "10.1038/s41586-020-2012-7"
    methods = [m for m, _ in server.seen]
    assert methods[0] == "initialize" and "tools/call" in methods
    assert all(auth == f"Bearer {SECRET}" for _, auth in server.seen)


def _with_output_schema() -> dict:
    http = fixture("tools_list.json")["http"]
    for tool in http["body"]["result"]["tools"]:
        tool["outputSchema"] = {"type": "object", "required": ["hits"],
                                "properties": {"hits": {"type": "array"}}}
    return http


@pytest.mark.parametrize("tools_list", ["tools_list.json", "error_server.json", "error_rate.json",
                                        "output_schema"])
def test_sdk_sends_exactly_one_tools_call_and_never_lists_tools(sdk, tools_list):
    """A successful (metered) tools/call must never be thrown away because of an SDK
    follow-up request: no tools/list, so its failure or an output schema cannot void it."""
    listing = _with_output_schema() if tools_list == "output_schema" else tools_list
    server, go = sdk("call_search_literature.json", tools_list=listing)
    assert smcp.parse_result("search_literature", go())[1] == 2
    methods = [m for m, _ in server.seen if m is not None]
    assert methods.count("tools/call") == 1
    assert "tools/list" not in methods


def test_sdk_error_body_is_redacted_before_truncation_and_drops_the_context(sdk):
    body = {"error": "unauthorized", "message": "x" * 470 + f" token {SECRET} rejected"}
    http = {"status": 401, "headers": {"content-type": "application/json"}, "body": body}
    _, go = sdk("call_search_literature.json", initialize=http)
    with pytest.raises(smcp.RPCError) as ei:
        go()
    assert ei.value.status == 401
    assert SECRET[:10] not in (ei.value.body or "") and SECRET[:10] not in repr(ei.value)
    assert ei.value.__context__ is None and ei.value.__cause__ is None


def test_sdk_citation_graph(sdk):
    _, go = sdk("call_citation_graph.json", "citation_graph",
                {"seeds": ["10.1038/s41586-020-2012-7"]})
    assert smcp.parse_result("citation_graph", go())[1] == 3


@pytest.mark.parametrize("calls, initialize, expected, status", [
    ("call_search_literature.json", "error_auth.json", "AUTH", 401),
    ("error_quota.json", "initialize.json", "QUOTA", None),
    ("error_rate.json", "initialize.json", "RATE", 429),
    ("error_server.json", "initialize.json", "TRANSIENT", 503),
    ("malformed.json", "initialize.json", "TRANSIENT", None),
])
def test_sdk_errors_are_classified(sdk, calls, initialize, expected, status):
    _, go = sdk(calls, initialize=initialize)
    with pytest.raises(smcp.RPCError) as ei:
        go()
    failure = smcp.classify(ei.value, SECRET)
    assert failure.classification == expected
    assert ei.value.status == status
    if expected == "RATE":
        assert failure.retry_after == 2.0
    assert SECRET not in str(failure) and SECRET not in repr(ei.value)


def test_sdk_quota_tool_error_is_a_result_classified_quota(sdk):
    _, go = sdk("error_quota_tool.json")
    with pytest.raises(smcp.MCPFailure) as ei:
        smcp.parse_result("search_literature", go())
    assert ei.value.classification == "QUOTA"


def test_sdk_client_end_to_end(env, monkeypatch):
    pytest.importorskip("mcp", reason="scite MCP SDK not installed (uv sync --group scite)")
    import httpx2

    server = FixtureServer("call_search_literature.json")
    real = smcp.sdk_rpc

    def factory(url, token):
        return real(url, token, timeout=5, http_transport=httpx2.MockTransport(server.handler))

    root, ws, day_dir = env
    (root / "config" / "scite.yml").write_text(yaml.safe_dump({}))
    p = pol.load(root, environ=HOST_ENV)
    led = ledger_mod.Ledger(ws, p, day_dir=day_dir, now=lambda: T0)
    c = cache_mod.Cache(day_dir / "cache", p.cache_ttl_days, now=lambda: T0)
    client = smcp.MCPClient(p, c, led, environ=HOST_ENV, rpc_factory=factory)
    res = client.call("search_literature", SEARCH_ARGS, round=1)
    assert res.results == 2 and led.calls("run") == 1
    no_secret_anywhere(ws, day_dir)
