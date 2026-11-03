"""MCP protocol layer (M-I t4, plan 5.4) and the static access checks of plan 6 t4 item 7.

Synthetic project only (tmp_path with a two-agent registry). The server is driven through
`Server.handle` in process, and through `Server.serve` over in-memory streams.
"""

import ast
import io
import json
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project
from scieflow.mcp_server import protocol, tools
from scieflow.mcp_server.protocol import SUPPORTED, Server

AGENTS_YML = (
    "agents:\n"
    '  stub: {cmd: "echo {prompt}", enabled: true, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
    '  stub2: {cmd: "echo {prompt}", enabled: true, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
)

INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(AGENTS_YML)
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "workspace").mkdir()
    return Project(tmp_path)


def call(server, method, params=None, id=1):
    message = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def initialize(server, version="2025-06-18", id=0):
    return call(server, "initialize", dict(INITIALIZE, protocolVersion=version), id=id)


def ready(server):
    initialize(server)
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def tool_call(server, name, arguments=None, id=5):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return call(ready(server), "tools/call", params, id=id)


def error_code(resp):
    return resp["error"]["code"]


# --- lifecycle (5.4) -----------------------------------------------------------------------


def test_ping_is_answered_before_initialize(project):
    assert call(Server(project), "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_list_before_initialize_is_refused_with_32002(project):
    resp = call(Server(project), "tools/list")
    assert error_code(resp) == -32002
    assert resp["id"] == 1


def test_initialize_result_shape(project):
    result = initialize(Server(project), "2025-06-18")["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "scieflow"
    assert isinstance(result["serverInfo"]["version"], str)
    assert "read-only" in result["instructions"].lower()


@pytest.mark.parametrize("asked, negotiated", [
    ("2025-06-18", "2025-06-18"),
    ("2025-03-26", "2025-03-26"),
    ("2024-11-05", "2024-11-05"),
    ("1999-01-01", SUPPORTED[0]),
])
def test_initialize_negotiates_the_protocol_version(project, asked, negotiated):
    assert initialize(Server(project), asked)["result"]["protocolVersion"] == negotiated


def test_initialize_without_protocol_version_is_invalid_params(project):
    resp = call(Server(project), "initialize", {"capabilities": {}})
    assert error_code(resp) == -32602


def test_second_initialize_is_invalid_request(project):
    server = ready(Server(project))
    assert error_code(initialize(server, id=7)) == -32600


def test_notifications_are_never_answered(project):
    server = Server(project)
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                          "params": {"requestId": 1}}) is None
    initialize(server)
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    # A request form without an id is a notification: it gets no response, and is not run.
    assert server.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_ping_result_after_initialize(project):
    assert call(ready(Server(project)), "ping", id="p")["result"] == {}


# --- JSON-RPC errors (5.4) -----------------------------------------------------------------


def test_invalid_json_is_parse_error_with_null_id(project):
    resp = Server(project).handle("{not json")
    assert error_code(resp) == -32700
    assert resp["id"] is None


def test_batch_array_is_invalid_request_with_null_id(project):
    resp = Server(project).handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert error_code(resp) == -32600
    assert resp["id"] is None


def test_missing_jsonrpc_is_invalid_request(project):
    resp = Server(project).handle({"id": 1, "method": "ping"})
    assert error_code(resp) == -32600
    assert resp["id"] is None


def test_null_id_is_invalid_request(project):
    resp = Server(project).handle({"jsonrpc": "2.0", "id": None, "method": "ping"})
    assert error_code(resp) == -32600
    assert resp["id"] is None


def test_unknown_method_after_initialize_is_32601(project):
    assert error_code(call(ready(Server(project)), "resources/list")) == -32601


def test_string_and_integer_ids_are_echoed_exactly(project):
    server = ready(Server(project))
    assert call(server, "ping", id="abc-1")["id"] == "abc-1"
    assert call(server, "ping", id=42)["id"] == 42


# --- tools/list (5.5) ----------------------------------------------------------------------

TOOL_NAMES = [
    "list_runs", "run_detail", "run_events", "open_gates", "run_charter", "conversation",
    "run_overview", "workbench", "curation_history", "agent_settings", "workflows",
    "conversational_agents",
]


def test_tools_list_declares_the_twelve_tools_with_final_annotations(project):
    result = call(ready(Server(project)), "tools/list")["result"]
    assert [t["name"] for t in result["tools"]] == TOOL_NAMES
    for tool in result["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert tool["title"] and tool["description"]
        assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False,
                                       "idempotentHint": True, "openWorldHint": False}


def test_tools_list_ignores_a_cursor(project):
    server = ready(Server(project))
    assert call(server, "tools/list", {"cursor": "x"})["result"] == \
        call(server, "tools/list", {})["result"]


# --- tools/call (5.4, 5.5) -----------------------------------------------------------------


def test_workflows_result_equals_the_service_value(project):
    resp = tool_call(Server(project), "workflows")
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload == {"result": json.loads(json.dumps(service.workflows()))}


def test_structured_content_only_from_2025_06_18(project):
    new = tool_call(Server(project), "workflows")["result"]
    assert new["structuredContent"] == json.loads(new["content"][0]["text"])
    old_server = Server(project)
    initialize(old_server, "2025-03-26")
    old_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    old = call(old_server, "tools/call", {"name": "workflows"})["result"]
    assert "structuredContent" not in old


def test_unknown_tool_is_invalid_params_naming_the_tool(project):
    resp = tool_call(Server(project), "write_run")
    assert error_code(resp) == -32602
    assert "write_run" in resp["error"]["message"]


def test_unknown_argument_is_named_but_its_value_is_not_echoed(project):
    resp = tool_call(Server(project), "workflows", {"secret": "PATIENT-XYZ-771"})
    assert error_code(resp) == -32602
    assert "secret" in resp["error"]["message"]
    assert "PATIENT-XYZ-771" not in json.dumps(resp)


def test_wrong_type_argument_is_named_but_its_value_is_not_echoed(project):
    resp = tool_call(Server(project), "run_events", {"slug": "r1", "limit": "NOPE-123"})
    assert error_code(resp) == -32602
    assert "limit" in resp["error"]["message"]
    assert "NOPE-123" not in json.dumps(resp)


@pytest.mark.parametrize("arguments", [
    {"slug": "r1", "limit": 0},
    {"slug": "r1", "limit": 1001},
    {"slug": "r1", "limit": True},
    {"slug": "r1", "since": "x" * 65},
    {"slug": "r1", "types": ["note.a"] * 21},
    {"slug": "r1", "types": [1]},
])
def test_range_and_length_limits_are_enforced(project, arguments):
    assert error_code(tool_call(Server(project), "run_events", arguments)) == -32602


def test_missing_required_argument_is_named(project):
    resp = tool_call(Server(project), "run_detail", {})
    assert error_code(resp) == -32602
    assert "slug" in resp["error"]["message"]


def test_service_error_is_a_tool_error_with_its_message(project, monkeypatch):
    def boom():
        raise service.ServiceError("no such thing")

    monkeypatch.setattr(service, "workflows", boom)
    result = tool_call(Server(project), "workflows")["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "no such thing"


def test_unexpected_exception_is_internal_error_and_traceback_stays_on_stderr(
        project, monkeypatch, capsys):
    def boom():
        raise RuntimeError("PATIENT-SECRET-PATH /home/x/secret")

    monkeypatch.setattr(service, "workflows", boom)
    resp = tool_call(Server(project), "workflows")
    captured = capsys.readouterr()
    assert error_code(resp) == -32603
    assert "PATIENT-SECRET" not in json.dumps(resp)
    assert "RuntimeError" in captured.err
    assert captured.out == ""


def test_oversized_result_is_a_tool_error(project, monkeypatch):
    monkeypatch.setattr(service, "workflows", lambda: [{"name": "x" * 1_100_000}])
    text = tool_call(Server(project), "workflows")["result"]["content"][0]["text"]
    assert text.startswith("result too large (")


def test_result_text_is_sanitised(project, monkeypatch):
    monkeypatch.setattr(service, "workflows", lambda: [{"name": "\x1b[31mred\x1b[0m"}])
    text = tool_call(Server(project), "workflows")["result"]["content"][0]["text"]
    assert "\x1b" not in text
    assert "red" in text


# --- serve over streams (5.4) --------------------------------------------------------------


def _lines(*messages):
    return "".join(json.dumps(m) + "\n" for m in messages)


PING = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INITIALIZE}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_serve_answers_each_line_and_returns_on_eof(project):
    stdin = io.StringIO(_lines(INIT, INITIALIZED,
                               {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}))
    stdout = io.StringIO()
    Server(project).serve(stdin, stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 3]
    assert [t["name"] for t in responses[1]["result"]["tools"]] == TOOL_NAMES


def test_serve_skips_blank_lines(project):
    stdout = io.StringIO()
    Server(project).serve(io.StringIO("\n   \n" + _lines(PING)), stdout)
    assert [json.loads(x)["id"] for x in stdout.getvalue().splitlines()] == [2]


def test_oversized_line_gets_a_parse_error_and_the_next_request_is_answered(project):
    big = {"jsonrpc": "2.0", "id": 9, "method": "ping",
           "params": {"pad": "a" * (4 * 1024 * 1024 + 10)}}
    stdout = io.StringIO()
    Server(project).serve(io.StringIO(_lines(big, PING)), stdout)
    first, second = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert first["error"]["code"] == -32700
    assert first["id"] is None
    assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}


# --- static access checks (plan 6 t4 item 7) -----------------------------------------------

FORBIDDEN_IMPORTS = {"socket", "http", "urllib", "requests", "httpx", "subprocess", "mcp"}
WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "touch", "append_jsonl", "emit", "_emit",
               "write_json", "write_yaml"}


def _violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                mode = node.args[1] if len(node.args) > 1 else None
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode = kw.value
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                        and set(mode.value) & set("wax+"):
                    found.append("open for writing")
            elif isinstance(func, ast.Attribute):
                owner = func.value.id if isinstance(func.value, ast.Name) else None
                if func.attr in WRITE_CALLS:
                    found.append(f".{func.attr}()")
                elif owner == "store" and func.attr.startswith("write_"):
                    found.append(f"store.{func.attr}()")
                elif owner == "jobs" and func.attr == "save":
                    found.append("jobs.save()")
    return found


def test_ast_check_matches_the_top_level_module_name_exactly():
    for source in ("import mcp", "import mcp.types", "from mcp.server import x",
                   "from mcp import types", "import socket", "from urllib.request import x"):
        assert _violations(source), source
    for source in ("from scieflow.integrations.scite.mcp import in_dispatch",
                   "import mcpserver_helpers", "from scieflow.core import service"):
        assert not _violations(source), source


def test_ast_check_flags_writes_and_jobs_save():
    assert _violations("open('x', 'w')")
    assert _violations("p.write_text('x')")
    assert _violations("store.write_json(p, {})")
    assert _violations("jobs.save(j)")
    assert not _violations("open('x')")


def test_mcp_server_package_has_no_network_subprocess_sdk_or_write_capability():
    package = Path(protocol.__file__).parent
    files = sorted(package.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "protocol.py", "tools.py", "cli.py"}
    problems = {f.name: _violations(f.read_text()) for f in files}
    assert all(not v for v in problems.values()), problems


def test_tools_module_is_the_registry_and_names_one_service_function_each():
    assert [t.name for t in tools.TOOLS] == TOOL_NAMES
    for tool in tools.TOOLS:
        assert hasattr(service, tool.service), tool.name
