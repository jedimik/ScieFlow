"""Acceptance (plan 6 t5 item 10): `scieflow mcp serve` as a real subprocess on the stub fixture.

Requests are written at once and stdin is closed (EOF ends the server). Synthetic data only, no
network. The subprocess inherits PYTHONPATH from the test run, so it imports the worktree source.
"""

import json
import os
import subprocess
import sys

from scieflow.mcp_server import tools

INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}
NO_ARGS = {"list_runs", "workflows", "conversational_agents", "agent_settings"}


def _serve(project, requests):
    env = {k: v for k, v in os.environ.items() if k not in ("SCIEFLOW_DISPATCH", "UV_CACHE_DIR")}
    proc = subprocess.Popen([sys.executable, "-m", "scieflow.cli", "mcp", "serve"],
                            cwd=str(project.root), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    out, err = proc.communicate(stdin, timeout=120)
    return proc.returncode, out, err


def test_the_stdio_server_lists_and_serves_every_tool_on_the_stub_fixture(mcp_project):
    names = [t.name for t in tools.TOOLS]
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INITIALIZE},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    call_id = {}
    for name in names:
        call_id[name] = len(requests) + 1
        arguments = {} if name in NO_ARGS else {"slug": "r1"}
        requests.append({"jsonrpc": "2.0", "id": call_id[name], "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}})
    refused_id, listed_id = len(requests) + 1, len(requests) + 2
    requests += [
        {"jsonrpc": "2.0", "id": refused_id, "method": "tools/call",
         "params": {"name": "run_detail", "arguments": {"slug": "secret"}}},
        {"jsonrpc": "2.0", "id": listed_id, "method": "tools/call",
         "params": {"name": "list_runs", "arguments": {}}},
    ]

    code, out, err = _serve(mcp_project, requests)

    assert code == 0, err
    assert "Traceback" not in err
    replies = {}
    for line in out.splitlines():
        message = json.loads(line)  # every stdout line is one JSON-RPC object
        replies[message["id"]] = message

    assert len(replies[2]["result"]["tools"]) == 12
    for name in names:
        assert replies[call_id[name]]["result"]["isError"] is False, name

    detail = json.loads(replies[call_id["run_detail"]]["result"]["content"][0]["text"])
    assert any(job["state"] == "done" for job in detail["result"]["jobs"])
    conversation = replies[call_id["conversation"]]["result"]["content"][0]["text"]
    assert "hello from the fixture" in conversation
    events_text = replies[call_id["run_events"]]["result"]["content"][0]["text"]
    assert '"job.finished"' in events_text

    listed = json.loads(replies[listed_id]["result"]["content"][0]["text"])
    assert "secret" not in [row["slug"] for row in listed["result"]]
    assert listed["hidden_runs"] == 1
    assert replies[refused_id]["result"]["isError"] is True
    assert "refused (l2-cloud)" in replies[refused_id]["result"]["content"][0]["text"]
