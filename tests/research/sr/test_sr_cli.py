"""M-H t2 (plan 5.13): the `scieflow sr` group, its help, `sr protocol show|check` and `sr status`.
The CLI reads the project root through sr_cli._root, which these tests point at the temporary root.
Exit codes: 0 ok, 1 refused, 2 usage."""

import json

import pytest
from click.testing import CliRunner

from scieflow.core import service
from scieflow.core.design import lock
from scieflow.cli import main
from scieflow.research.search import registry
from scieflow.research.sr import cli as sr_cli


@pytest.fixture
def root(srroot, monkeypatch):
    monkeypatch.setattr(sr_cli, "_root", lambda: srroot)
    return srroot


def _run(*args):
    return CliRunner().invoke(sr_cli.sr, list(args))


def _lock(root):
    from scieflow.core.project import Project

    proj = Project(root)
    gate = lock.lock(proj, root / "workspace" / "r1")
    service.answer_gate(proj, "r1", gate["id"], "approve")


def test_sr_help_lists_protocol_and_status():
    result = CliRunner().invoke(sr_cli.sr, ["--help"])
    assert result.exit_code == 0
    assert "protocol" in result.output and "status" in result.output


def test_the_top_level_help_lists_the_sr_group():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "sr" in result.output
    assert "Systematic review" in result.output


def test_protocol_show_json_after_the_lock(root):
    _lock(root)
    result = _run("protocol", "show", "r1", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["version"] == 1
    assert doc["review"]["screening"]["batch_size"] == 50


def test_protocol_show_refuses_an_unlocked_protocol(root):
    result = _run("protocol", "show", "r1")
    assert result.exit_code == 1
    assert "lock the protocol first" in result.output


def test_protocol_check_prints_each_strategy_status(root, monkeypatch):
    _lock(root)

    def fake_check(query):
        return object()

    monkeypatch.setattr(registry, "check_query", fake_check)
    result = _run("protocol", "check", "r1", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["strategies"] == [{"id": "s1", "status": "accepted", "reason": None}]


def test_status_on_an_empty_run_reports_no_committed_operations(root):
    result = _run("status", "r1", "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["protocol"]["status"] == "draft"
    assert doc["committed"] == {}
    assert doc["uncommitted"] == []
    assert doc["problems"] == []
