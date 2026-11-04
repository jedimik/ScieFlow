"""The read-only proof (plan 6 t5 item 8): every tool, every run, refusals included, writes nothing
but new `*.lock` files. Synthetic fixture only."""

import hashlib
import json

from scieflow.core import service
from scieflow.mcp_server import tools
from scieflow.mcp_server.protocol import Server

INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}
RUNS = ("r1", "secret", "l1")
NO_ARGS = {"list_runs", "workflows", "conversational_agents"}


def snapshot(root):
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            stat = path.stat()
            out[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns,
                                                hashlib.sha256(data).hexdigest())
    return out


def diff(before, after):
    return {"changed": [p for p in before if p in after and before[p] != after[p]],
            "removed": [p for p in before if p not in after],
            "added": [p for p in after if p not in before]}


def _server(project):
    server = Server(project)
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": INITIALIZE})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def _call(server, name, arguments):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})


def test_every_tool_on_every_run_writes_nothing_but_lock_files(mcp_project, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(mcp_project.root / "state"))
    before = snapshot(mcp_project.root)
    server = _server(mcp_project)
    for tool in tools.TOOLS:
        if tool.name in NO_ARGS:
            _call(server, tool.name, {})
            continue
        if tool.name == "open_gates":
            _call(server, tool.name, {})
        for run in RUNS:
            _call(server, tool.name, {"slug": run})
    result = diff(before, snapshot(mcp_project.root))
    assert result["changed"] == [] and result["removed"] == []
    assert all(p.endswith(".lock") for p in result["added"]), result["added"]


def test_the_snapshot_would_catch_a_write(mcp_project, monkeypatch):
    """The proof is meaningful: a read whose service function writes a file fails it."""
    before = snapshot(mcp_project.root)

    def writes(project, slug):
        (project.run_dir("r1") / "written-by-a-read.md").write_text("x")
        return {}

    monkeypatch.setattr(service, "run_charter", writes)
    _call(_server(mcp_project), "run_charter", {"slug": "r1"})
    result = diff(before, snapshot(mcp_project.root))
    assert any(p.endswith("written-by-a-read.md") for p in result["added"])
    assert not all(p.endswith(".lock") for p in result["added"])
