"""`scieflow data` declare, levels, pin (M-B Part A 5.10) and the event vocabulary (5.11)."""

import json
import re

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events
from scieflow.core.data import policy
from scieflow.core.data.cli import data
from scieflow.core.data.policy import pin_sha

# The same entry as conftest.REGISTRY["agents"]["safe-stub"] (conftest is not importable here).
SAFE_STUB = {"cmd": "{python} -m stub --endpoint {endpoint} {prompt}",
             "enabled": True, "timeout_min": 1}


def run(*args):
    return CliRunner().invoke(data, list(args))


def read_data(ws):
    return yaml.safe_load((ws / "data.yml").read_text())


def write_policy(root, text):
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "data-policy.yml").write_text(text)


def declared_events(ws):
    return events.read(ws, types=("data.declared",))


def test_human_declares_an_entry_and_it_is_recorded(ws):
    r = run("declare", "r1", "inputs/x.csv", "--level", "L2", "--kind", "tabular",
            "--reason", "REDCap export")
    assert r.exit_code == 0, r.output
    entry = read_data(ws)["levels"][0]
    assert (entry["path"], entry["level"], entry["declared_by"]) == ("inputs/x.csv", "L2", "human")
    ev = declared_events(ws)[-1]
    assert ev["actor"] == "human" and ev["data"]["lowered"] is False


def test_adding_without_a_reason_is_refused_and_writes_nothing(ws):
    r = run("declare", "r1", "inputs/x.csv", "--level", "L2")
    assert r.exit_code == 1
    assert not (ws / "data.yml").exists()


def test_lowering_needs_a_reason_and_then_records_lowered(ws):
    assert run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "x").exit_code == 0
    before = (ws / "data.yml").read_text()
    r = run("declare", "r1", "inputs/x.csv", "--level", "L0")
    assert r.exit_code == 1
    assert (ws / "data.yml").read_text() == before
    r = run("declare", "r1", "inputs/x.csv", "--level", "L0", "--reason", "aggregated")
    assert r.exit_code == 0, r.output
    assert read_data(ws)["levels"][0]["level"] == "L0"
    assert declared_events(ws)[-1]["data"]["lowered"] is True


def test_removing_needs_a_reason(ws):
    run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "x")
    before = (ws / "data.yml").read_text()
    assert run("declare", "r1", "inputs/x.csv", "--remove").exit_code == 1
    assert (ws / "data.yml").read_text() == before
    assert run("declare", "r1", "inputs/x.csv", "--remove", "--reason", "gone").exit_code == 0
    assert read_data(ws)["levels"] == []


def test_an_agent_may_add_a_new_entry(ws):
    run("declare", "r1", "inputs/a.csv", "--level", "L1", "--reason", "aggregate")
    r = run("declare", "r1", "inputs/b.csv", "--level", "L2", "--reason", "rec", "--as-agent")
    assert r.exit_code == 0, r.output
    added = read_data(ws)["levels"][1]
    assert added["declared_by"] == "agent"
    assert declared_events(ws)[-1]["actor"] == "agent"


@pytest.mark.parametrize("args", [
    ["inputs/a.csv", "--level", "L2", "--reason", "raise"],
    ["inputs/a.csv", "--level", "L0", "--reason", "lower"],
    ["inputs/a.csv", "--remove", "--reason", "remove"],
])
def test_an_agent_may_not_change_or_remove_an_existing_entry(ws, args):
    run("declare", "r1", "inputs/a.csv", "--level", "L1", "--reason", "aggregate")
    before = (ws / "data.yml").read_text()
    r = run("declare", "r1", *args, "--as-agent")
    assert r.exit_code == 1
    assert (ws / "data.yml").read_text() == before


def test_an_agent_may_raise_the_default_but_not_lower_it(ws):
    assert run("declare", "r1", "--default", "L1", "--reason", "x", "--as-agent").exit_code == 0
    assert read_data(ws)["default_level"] == "L1"
    before = (ws / "data.yml").read_text()
    assert run("declare", "r1", "--default", "L0", "--reason", "x", "--as-agent").exit_code == 1
    assert (ws / "data.yml").read_text() == before


def test_every_write_refuses_inside_a_dispatch(ws, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    r = run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "x", "--as-agent")
    assert r.exit_code == 1
    assert not (ws / "data.yml").exists()


def test_an_invalid_data_file_is_never_overwritten(ws):
    (ws / "data.yml").write_text("version: 1\nlevels: nope\n")
    before = (ws / "data.yml").read_text()
    r = run("declare", "r1", "inputs/x.csv", "--level", "L0", "--reason", "x")
    assert r.exit_code == 1
    assert (ws / "data.yml").read_text() == before


def test_declare_prints_the_suggestion_only_while_warn(ws, project):
    r = run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "rec")
    assert "set enforcement: refuse" in r.output
    write_policy(project, "version: 1\nenforcement: refuse\n")
    r = run("declare", "r1", "inputs/y.csv", "--level", "L2", "--reason", "rec")
    assert "set enforcement: refuse" not in r.output


def test_levels_reports_level_declarations_and_counts(ws):
    run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "rec")
    r = run("levels", "r1", "--json")
    assert r.exit_code == 0, r.output
    got = json.loads(r.output)
    assert got["run_level"] == "L2"
    assert got["enforcement"] == "warn"
    assert got["declarations"][0]["path"] == "inputs/x.csv"
    assert (got["pending_releases"], got["released"], got["imports"]) == (0, 0, 0)
    assert "set enforcement: refuse" in got["suggestion"]


def test_levels_suggestion_counts_an_l2_in_another_run(ws, project):
    other = project / "workspace" / "r2"
    other.mkdir()
    (other / "data.yml").write_text(yaml.safe_dump(
        {"version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
         "released": [], "imports": []}))
    got = json.loads(run("levels", "r1", "--json").output)
    assert got["run_level"] == "L0"
    assert got["suggestion"] is not None


def test_no_suggestion_when_nothing_is_l2_or_when_refusing(ws, project):
    assert json.loads(run("levels", "r1", "--json").output)["suggestion"] is None
    run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "rec")
    write_policy(project, "version: 1\nenforcement: refuse\n")
    assert json.loads(run("levels", "r1", "--json").output)["suggestion"] is None


def test_levels_shows_the_boundary_of_each_assigned_agent(ws, project):
    write_policy(project, "version: 1\n"
                 "safe_endpoints:\n  - url: https://llm.example.org/v1\n    reason: t\n"
                 "safe_agents:\n  safe-stub:\n    endpoint: https://llm.example.org/v1\n"
                 f"    templates_sha256: \"{pin_sha(SAFE_STUB)}\"\n"
                 "    reason: test\n")
    got = json.loads(run("levels", "r1", "--json").output)
    assert got["boundaries"]["safe-stub"]["kind"] == "safe"
    assert got["boundaries"]["safe-stub"]["endpoint"] == "https://llm.example.org/v1"


def test_pin_prints_the_sha_and_writes_nothing(project):
    r = run("pin", "safe-stub")
    assert r.exit_code == 0, r.output
    assert pin_sha(SAFE_STUB) in r.output
    assert not (project / "config" / "data-policy.yml").exists()


def test_the_pinned_sha_from_pin_makes_boundary_safe(project):
    # F5: the sha printed by `pin` is the one that makes `boundary` return safe.
    from scieflow.core.data.policy import boundary

    printed = re.search(r"[0-9a-f]{64}", run("pin", "safe-stub").output).group(0)
    write_policy(project, "version: 1\n"
                 "safe_endpoints:\n  - url: https://llm.example.org/v1\n    reason: t\n"
                 "safe_agents:\n  safe-stub:\n    endpoint: https://llm.example.org/v1\n"
                 f"    templates_sha256: \"{printed}\"\n    reason: test\n")
    pol = policy.load(project)
    got = boundary(pol, "safe-stub", SAFE_STUB, root=project, writable_areas=[])
    assert (got.kind, got.reason) == ("safe", "pinned")


def test_levels_and_pin_are_allowed_inside_a_dispatch(ws, monkeypatch):
    # F5: the 5.10 table allows the read-only commands inside a dispatch.
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    assert run("levels", "r1", "--json").exit_code == 0
    assert run("pin", "safe-stub").exit_code == 0


def test_pin_of_an_unknown_agent_is_a_usage_error(project):
    assert run("pin", "no-such-agent").exit_code == 2


def test_no_data_command_writes_the_policy_file(ws, project):
    write_policy(project, "version: 1\nenforcement: warn\n")
    before = (project / "config" / "data-policy.yml").read_bytes()
    run("declare", "r1", "inputs/x.csv", "--level", "L2", "--reason", "rec")
    run("declare", "r1", "inputs/x.csv", "--level", "L0", "--reason", "agg")
    run("levels", "r1", "--json")
    run("pin", "safe-stub")
    assert (project / "config" / "data-policy.yml").read_bytes() == before
    assert policy.load(project).enforcement == "warn"


def test_invalid_policy_makes_levels_exit_3(ws, project):
    write_policy(project, "version: 1\nbogus: 1\n")
    assert run("levels", "r1").exit_code == 3


def test_data_event_types_are_in_the_closed_vocabulary():
    for type_ in ("data.declared", "data.level_resolved", "data.warning",
                  "data.released", "data.imported", "data.ingested"):
        assert type_ in events.TYPES
