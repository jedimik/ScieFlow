"""`scieflow mcp` command (M-I t4, plan 5.6 items 1, 2 and 8).

Synthetic project only. The dispatch refusal is checked before stdin is read.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import scieflow
from scieflow.cli import main
from scieflow.core import sandbox
from scieflow.core.project import Project
from scieflow.mcp_server import cli as mcp_cli
from scieflow.mcp_server import tools

REFUSAL = "scieflow mcp: refused inside a dispatch"
AGENTS_YML = (
    "agents:\n"
    '  stub: {cmd: "echo {prompt}", enabled: true, timeout_min: 1, family: claude, '
    'session_cmd: "echo {prompt}", resume_cmd: "echo {session}"}\n'
)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(AGENTS_YML)
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "workspace" / "r1").mkdir(parents=True)
    monkeypatch.setattr(Project, "discover", lambda *a, **k: Project(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    return Project(tmp_path)


def _lines(*messages):
    return "".join(json.dumps(m) + "\n" for m in messages)


def _invoke(args, *, env=None, input=None):
    return CliRunner().invoke(main, list(args), env=env or {}, input=input,
                              catch_exceptions=False)


def test_root_help_lists_the_mcp_group():
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert any(line.split()[:1] == ["mcp"] for line in result.output.splitlines())


@pytest.mark.parametrize("env", [
    {"SCIEFLOW_DISPATCH": "1"},
    {"UV_CACHE_DIR": f"/tmp/cache/{sandbox.CACHE_DIRNAME}"},
])
@pytest.mark.parametrize("args", [["mcp", "serve"], ["mcp", "tools"], ["mcp", "tools", "--json"]])
def test_dispatch_refuses_before_stdin_is_read(project, args, env):
    result = _invoke(args, env=env, input=_lines(INIT, INITIALIZED, LIST))
    assert result.exit_code == 1
    assert REFUSAL in result.output
    assert not [line for line in result.output.splitlines() if line.startswith("{")]


def test_tools_json_prints_the_tools_list(project):
    result = _invoke(["mcp", "tools", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == tools.tools_list()
    assert [t["name"] for t in json.loads(result.output)] == [t.name for t in tools.TOOLS]


def test_tools_text_prints_one_name_and_title_per_line(project):
    result = _invoke(["mcp", "tools"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"{t.name}: {t.title}" for t in tools.TOOLS]


def test_serve_workspace_must_be_an_existing_run(project):
    result = _invoke(["mcp", "serve", "--workspace", "nope"], input="")
    assert result.exit_code == 1
    assert "nope" in result.output


def test_serve_workspace_refuses_a_path_outside_the_run_directory(project):
    result = _invoke(["mcp", "serve", "--workspace", "../r1"], input="")
    assert result.exit_code == 1


def test_serve_passes_the_scope_to_the_server(project, monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, project, scope=None):
            seen["scope"] = scope

        def serve(self, stdin, stdout):
            seen["served"] = True

    monkeypatch.setattr(mcp_cli, "Server", Recorder)
    result = _invoke(["mcp", "serve", "--workspace", "r1"], input="")
    assert result.exit_code == 0, result.output
    assert seen == {"scope": "r1", "served": True}
    seen.clear()
    assert _invoke(["mcp", "serve"], input="").exit_code == 0
    assert seen == {"scope": None, "served": True}


def test_serve_subprocess_answers_over_stdio_and_exits_zero_on_eof(project):
    env = {k: v for k, v in os.environ.items()
           if k not in ("SCIEFLOW_DISPATCH", "UV_CACHE_DIR")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(scieflow.__file__).resolve().parents[1])] + [env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "scieflow.cli", "mcp", "serve"],
        input=_lines(INIT, INITIALIZED, LIST), capture_output=True, text=True,
        cwd=project.root, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2]
    assert [t["name"] for t in responses[1]["result"]["tools"]] == [t.name for t in tools.TOOLS]


def _serve_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("SCIEFLOW_DISPATCH", "UV_CACHE_DIR")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(scieflow.__file__).resolve().parents[1])] + [env.get("PYTHONPATH", "")])
    env.update(extra or {})
    return env


def _run_mcp(project, args, data: bytes, extra_env=None):
    return subprocess.run([sys.executable, "-m", "scieflow.cli", *args], input=data,
                          capture_output=True, cwd=project.root, env=_serve_env(extra_env),
                          timeout=30)


def test_subprocess_invalid_utf8_line_is_a_parse_error_and_the_server_continues(project):
    data = (_lines(INIT, INITIALIZED).encode() + b"\xff\xfe{\"jsonrpc\"\n"
            + _lines(LIST).encode())
    proc = _run_mcp(project, ["mcp", "serve"], data)
    assert proc.returncode == 0, proc.stderr.decode()
    responses = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    assert [r.get("id") for r in responses] == [1, None, 2]
    assert responses[1]["error"]["code"] == -32700
    assert proc.stderr == b""


def test_subprocess_oversized_line_is_refused_and_the_next_request_is_answered(project):
    big = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping",
                      "params": {"pad": "a" * (5 * 1024 * 1024)}}) + "\n"
    proc = _run_mcp(project, ["mcp", "serve"], (_lines(INIT, INITIALIZED) + big + _lines(LIST)).encode())
    assert proc.returncode == 0, proc.stderr.decode()
    responses = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    assert [r.get("id") for r in responses] == [1, None, 2]
    assert responses[1]["error"]["code"] == -32700
    assert [t["name"] for t in responses[2]["result"]["tools"]] == [t.name for t in tools.TOOLS]


@pytest.mark.parametrize("args", [["mcp", "serve"], ["mcp", "tools", "--json"]])
def test_subprocess_dispatch_refusal_prints_nothing_on_stdout(project, args):
    proc = _run_mcp(project, args, _lines(INIT, INITIALIZED, LIST).encode(),
                    {"SCIEFLOW_DISPATCH": "1"})
    assert proc.returncode == 1
    assert REFUSAL in proc.stderr.decode()
    assert proc.stdout == b""
