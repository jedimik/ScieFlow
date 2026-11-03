"""The access rules of plan 5.6 on the fixture project: aggregated open_gates (RED/GREEN item 3 a-e)
and a refused run is never read (plan 5.6 items 4 and 4b). Synthetic data only."""

import json

import pytest

from scieflow.core import service
from scieflow.mcp_server import access
from scieflow.mcp_server.protocol import Server

PROPOSAL_MARKER = "SYNTHETIC-PROPOSAL-MARKER-7f3a"
REFUSE = "version: 1\nenforcement: refuse\n"
INVALID = "version: 2\n"
INITIALIZE = {"protocolVersion": "2025-06-18", "capabilities": {},
              "clientInfo": {"name": "test", "version": "0"}}
PER_RUN = {"run_detail": "run_detail", "run_charter": "run_charter",
           "conversation": "conversation_state", "run_overview": "run_overview",
           "workbench": "workbench", "curation_history": "curation_history"}


def ready(project, scope=None):
    server = Server(project, scope=scope)
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": INITIALIZE})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def call(server, name, arguments=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": params})["result"]


def body(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def error_text(result):
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def _policy(project, text):
    (project.root / "config" / "data-policy.yml").write_text(text)


def test_case_a_refuse_hides_l1_and_secret_and_never_names_them(mcp_project):
    _policy(mcp_project, REFUSE)
    raw = json.dumps(body(call(ready(mcp_project), "open_gates")))
    got = json.loads(raw)
    assert {gate["slug"] for gate in got["result"]} == {"r1"}
    assert all(w["slug"] not in {"l1", "secret"} for w in got.get("data_warnings", []))
    assert PROPOSAL_MARKER not in raw
    assert got["hidden_runs"] == 2


def test_case_b_warn_serves_l1_with_its_warning(mcp_project):
    got = body(call(ready(mcp_project), "open_gates"))
    assert {gate["slug"] for gate in got["result"]} == {"r1", "l1"}
    assert {"slug": "l1", "rule": "l1-external-sharing", "path": "."} in got["data_warnings"]
    assert got["hidden_runs"] == 1  # secret is L2


def test_case_c_invalid_policy_gives_an_empty_result_and_counts_every_run(mcp_project):
    _policy(mcp_project, INVALID)
    raw = json.dumps(body(call(ready(mcp_project), "open_gates")))
    got = json.loads(raw)
    assert got["result"] == []
    assert got["hidden_runs"] == 3  # r1, l1, secret: all three refused
    assert PROPOSAL_MARKER not in raw


def test_case_d_refused_runs_proposals_are_never_read(mcp_project, monkeypatch):
    read = []
    real = service._with_proposal_preview

    def spy(ws, gate_list):
        read.append(ws.name)
        return real(ws, gate_list)

    monkeypatch.setattr(service, "_with_proposal_preview", spy)
    for text in (REFUSE, INVALID):
        read.clear()
        _policy(mcp_project, text)
        call(ready(mcp_project), "open_gates")
        assert "l1" not in read and "secret" not in read, text


def test_case_e_an_empty_allowed_set_passes_slugs_as_an_empty_list(mcp_project, monkeypatch):
    calls = []

    def recorder(project, slug=None, **kwargs):
        calls.append((slug, kwargs))
        return []

    monkeypatch.setattr(service, "open_gates", recorder)
    _policy(mcp_project, INVALID)
    body(call(ready(mcp_project), "open_gates"))
    assert calls == [(None, {"slugs": []})]
    assert isinstance(calls[0][1]["slugs"], list)


@pytest.mark.parametrize("tool", [*PER_RUN, "run_events", "agent_settings"])
def test_a_refused_run_is_never_read_by_any_per_run_service_function(mcp_project, monkeypatch,
                                                                     tool):
    service_name = PER_RUN.get(tool, tool)
    reads = []

    def recorder(*args, **kwargs):
        reads.append(args)
        return {}

    monkeypatch.setattr(service, service_name, recorder)
    result = call(ready(mcp_project), tool, {"slug": "secret"})
    assert "refused (l2-cloud)" in error_text(result)
    assert reads == []


def test_refused_runs_are_not_named_in_list_runs(mcp_project):
    _policy(mcp_project, REFUSE)
    assert "secret" not in json.dumps(body(call(ready(mcp_project), "list_runs")))


def test_the_client_name_is_the_reserved_mcp_client():
    assert access.MCP_CLIENT == "scieflow-mcp-client"
