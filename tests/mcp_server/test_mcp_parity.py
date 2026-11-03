"""MCP-CLI parity (plan 6 t5 item 9): nothing is reachable only over MCP (D7), and each tool calls
its service function exactly once. Synthetic fixture only."""

import json
from pathlib import Path

import pytest
import yaml

from scieflow.core import service
from scieflow.mcp_server import tools
from scieflow.mcp_server.protocol import Server

INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}
NO_ARGS = {"list_runs", "workflows", "conversational_agents"}
ROOT = Path(__file__).resolve().parents[2]


def _server(project):
    server = Server(project)
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": INITIALIZE})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def _parity_functions():
    return yaml.safe_load((ROOT / "tests" / "core" / "cli_parity.yml").read_text())["functions"]


def test_every_tool_service_function_has_a_cli_entry():
    table = _parity_functions()
    for tool in tools.TOOLS:
        entry = table.get(tool.service)
        assert entry is not None, tool.service
        assert entry.get("cli"), f"{tool.service} would be reachable over MCP only"


def test_every_tool_names_a_public_service_function():
    for tool in tools.TOOLS:
        assert callable(getattr(service, tool.service)), tool.service


@pytest.mark.parametrize("tool", [t.name for t in tools.TOOLS])
def test_each_tool_calls_its_service_function_exactly_once(mcp_project, monkeypatch, tool):
    spec = next(t for t in tools.TOOLS if t.name == tool)
    seen = []

    def recorder(*args, **kwargs):
        seen.append((args, kwargs))
        return []

    monkeypatch.setattr(service, spec.service, recorder)
    arguments = {} if tool in NO_ARGS or tool == "open_gates" else {"slug": "r1"}
    reply = _server(mcp_project).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": tool, "arguments": arguments}})
    assert reply["result"]["isError"] is False, reply
    assert len(seen) == 1, (tool, seen)


def test_the_listed_tool_set_matches_the_documented_twelve(mcp_project):
    listed = _server(mcp_project).handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert json.dumps(sorted(names)) == json.dumps(sorted(t.name for t in tools.TOOLS))
    assert len(names) == 12
