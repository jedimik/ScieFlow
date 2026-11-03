"""Project-level MCP tools and the per-run placeholders of t4 (M-I t4, plan 5.5).

`workflows`, `conversational_agents` and `agent_settings` (without a run) return the service
value unchanged and write nothing. The per-run tools are declared here; until t5 they answer
with a tool error saying so. Synthetic project only.
"""

import json
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project
from scieflow.mcp_server import tools
from scieflow.mcp_server.protocol import Server

AGENTS_YML = (
    "agents:\n"
    '  stub: {cmd: "echo {prompt}", enabled: true, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
    '  stub2: {cmd: "echo {prompt}", enabled: true, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
    '  off: {cmd: "echo {prompt}", enabled: false, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
)
INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(AGENTS_YML)
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "workspace" / "r1").mkdir(parents=True)
    return Project(tmp_path)


def ready(project):
    server = Server(project)
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": INITIALIZE})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def tool_call(server, name, arguments=None, id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return server.handle({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": params})


def payload(resp):
    result = resp["result"]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_workflows_returns_the_service_value(project):
    assert payload(tool_call(ready(project), "workflows")) == \
        {"result": json.loads(json.dumps(service.workflows()))}


def test_conversational_agents_returns_the_service_value(project):
    expected = service.conversational_agents(project)
    assert payload(tool_call(ready(project), "conversational_agents")) == {"result": expected}
    assert "off" not in expected


def test_agent_settings_without_a_run_returns_the_service_value(project):
    expected = json.loads(json.dumps(service.agent_settings(project), default=str))
    assert payload(tool_call(ready(project), "agent_settings")) == {"result": expected}


def test_project_tools_write_nothing(project):
    before = _tree(project.root)
    server = ready(project)
    for name in ("workflows", "conversational_agents", "agent_settings"):
        tool_call(server, name)
    assert _tree(project.root) == before


def test_each_tool_declares_the_service_function_it_calls():
    expected = {
        "list_runs": "list_runs_with_budget", "run_detail": "run_detail",
        "run_events": "run_events", "open_gates": "open_gates", "run_charter": "run_charter",
        "conversation": "conversation_state", "run_overview": "run_overview",
        "workbench": "workbench", "curation_history": "curation_history",
        "agent_settings": "agent_settings", "workflows": "workflows",
        "conversational_agents": "conversational_agents",
    }
    assert {t.name: t.service for t in tools.TOOLS} == expected


PER_RUN = ["run_detail", "run_events", "run_charter", "conversation", "run_overview",
           "workbench", "curation_history"]


# M-I t5 completed the per-run tools (tests/mcp_server/test_run_tools.py). These t4 placeholder
# assertions ("not available yet until t5") are replaced by the t5 behaviour: a missing run is a
# plain tool error, never the placeholder text.
@pytest.mark.parametrize("name", PER_RUN)
def test_per_run_tools_on_a_missing_run_are_plain_tool_errors(project, name):
    resp = tool_call(ready(project), name, {"slug": "nope"})
    assert resp["result"]["isError"] is True
    assert "no run workspace/nope" in resp["result"]["content"][0]["text"]


def test_agent_settings_with_a_run_is_served_by_t5(project):
    resp = tool_call(ready(project), "agent_settings", {"slug": "r1"})
    assert resp["result"]["isError"] is False
    assert json.loads(resp["result"]["content"][0]["text"]) == {
        "result": json.loads(json.dumps(service.agent_settings(project, "r1"), default=str))}


def test_agent_settings_uses_the_scoped_run_when_no_slug_is_given(project):
    server = Server(project, scope="r1")
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": INITIALIZE})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    resp = tool_call(server, "agent_settings")
    assert resp["result"]["isError"] is False
    assert json.loads(resp["result"]["content"][0]["text"]) == {
        "result": json.loads(json.dumps(service.agent_settings(project, "r1"), default=str))}
