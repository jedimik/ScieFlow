import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path, approval="per-campaign"):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", approval))
    return Project(ROOT), ws        # schemas come from the real repo


def test_open_and_human_answer(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "campaign-approval", "Run sigma-sweep (7 runs)?",
                        options=["approve", "reject"])
    assert g["state"] == "open" and g["requires_human"] is False
    done = gates.answer(project, ws, g["id"], "approve")
    assert done["state"] == "answered" and done["answered_by"] == "human"
    assert [e["type"] for e in events.read(ws)] == ["gate.opened", "gate.answered"]


def test_answer_must_be_an_offered_option(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "campaign-approval", "?", options=["approve", "reject"])
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, g["id"], "maybe")


def test_agent_cannot_answer_in_a_gated_run(tmp_path):
    project, ws = setup(tmp_path, approval="per-campaign")
    g = gates.open_gate(project, ws, "campaign-approval", "?", in_scope=True)
    with pytest.raises(gates.GateError, match="autonomous"):
        gates.answer(project, ws, g["id"], "approve", actor="agent", rationale="within scope")


def test_agent_answers_in_scope_gate_in_autonomous_run(tmp_path):
    project, ws = setup(tmp_path, approval="autonomous")
    g = gates.open_gate(project, ws, "campaign-approval", "?", in_scope=True)
    done = gates.answer(project, ws, g["id"], "approve", actor="agent",
                        rationale="grid stays inside the approved sigma range")
    assert done["answered_by"] == "agent" and done["rationale"]


@pytest.mark.parametrize("kind", ["upload", "external-sharing", "tier-promotion",
                                  "scope-change", "budget-extension", "claim-check-consent"])
def test_requires_human_gates_block_agents_even_when_autonomous(tmp_path, kind):
    project, ws = setup(tmp_path, approval="autonomous")
    g = gates.open_gate(project, ws, kind, "?", in_scope=True)
    assert g["requires_human"] is True
    with pytest.raises(gates.GateError, match="human"):
        gates.answer(project, ws, g["id"], "yes", actor="agent", rationale="r")


def test_agent_needs_scope_and_rationale(tmp_path):
    project, ws = setup(tmp_path, approval="autonomous")
    out_of_scope = gates.open_gate(project, ws, "outline-approval", "?")
    with pytest.raises(gates.GateError, match="scope"):
        gates.answer(project, ws, out_of_scope["id"], "ok", actor="agent", rationale="r")
    in_scope = gates.open_gate(project, ws, "outline-approval", "?", in_scope=True)
    with pytest.raises(gates.GateError, match="rationale"):
        gates.answer(project, ws, in_scope["id"], "ok", actor="agent")


def test_a_charter_adoption_gate_must_offer_an_adopt_word(tmp_path):
    """Free-form options that never spell an adopt word would let a human
    answer "accept" and have the gate recorded as answered with no charter
    ever written, silently. Refuse the gate at open time instead."""
    project, ws = setup(tmp_path)
    with pytest.raises(gates.GateError, match="adopt"):
        gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                        options=["accept", "reject"])
    # An adopt word among the options is enough, whatever else is offered.
    g = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                        options=["approve", "reject"])
    assert g["state"] == "open"


def test_unknown_kind_and_double_answer_are_refused(tmp_path):
    project, ws = setup(tmp_path)
    with pytest.raises(gates.GateError):
        gates.open_gate(project, ws, "coffee", "?")
    g = gates.open_gate(project, ws, "question", "?")
    gates.answer(project, ws, g["id"], "a")
    with pytest.raises(gates.GateError, match="not open"):
        gates.answer(project, ws, g["id"], "b")


def test_wait_returns_when_answered_elsewhere(tmp_path):
    project, ws = setup(tmp_path)
    g = gates.open_gate(project, ws, "question", "?")
    threading.Timer(0.3, lambda: gates.answer(project, ws, g["id"], "later")).start()
    assert gates.wait(ws, g["id"], timeout=5, poll=0.05)["answer"] == "later"
    other = gates.open_gate(project, ws, "question", "?")
    with pytest.raises(TimeoutError):
        gates.wait(ws, other["id"], timeout=0.2, poll=0.05)


def test_cli_open_list_answer(tmp_path, monkeypatch):
    from scieflow.core.gates import gate

    project, ws = setup(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    for name in ("gates", "status"):
        (tmp_path / "schemas" / f"{name}.yml").write_text((ROOT / "schemas" / f"{name}.yml").read_text())
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    opened = cli.invoke(gate, ["open", "r1", "--kind", "question", "--question", "Which dataset?",
                               "--option", "A", "--option", "B", "--json"])
    assert opened.exit_code == 0, opened.output
    gate_id = __import__("json").loads(opened.output)["id"]
    assert gate_id in cli.invoke(gate, ["list", "r1", "--open"]).output
    assert cli.invoke(gate, ["answer", "r1", gate_id, "B"]).exit_code == 0
    assert "B" in cli.invoke(gate, ["show", "r1", gate_id]).output


def _cli_charter_env(tmp_path):
    """A run workspace plus the config/schemas `gate answer` needs when
    invoked through `Project.discover()` from `tmp_path` as cwd."""
    project, ws = setup(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    for name in ("gates", "status"):
        (tmp_path / "schemas" / f"{name}.yml").write_text((ROOT / "schemas" / f"{name}.yml").read_text())
    return Project(tmp_path), ws


def test_cli_gate_answer_adopt_actually_writes_the_charter(tmp_path, monkeypatch):
    """C1: `scieflow gate answer <slug> <id> adopt` from a terminal used to
    call `gates.answer` directly, which flips the gate to `answered` but
    knows nothing about charters — so no charter.set event, no charter text,
    and (because a gate must be `open` to be answered) no way to retry. This
    must go through `service.answer_gate`, the same as the browser."""
    from scieflow.core.gates import gate
    from scieflow.core.run import charter

    project, ws = _cli_charter_env(tmp_path)
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Adopted from the terminal.")
    g = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                        options=["adopt", "decline"], files=[proposal])
    monkeypatch.chdir(tmp_path)

    cli = CliRunner()
    result = cli.invoke(gate, ["answer", "r1", g["id"], "adopt"])

    assert result.exit_code == 0, result.output
    assert charter.current_text(ws) == "Adopted from the terminal."
    assert "charter.set" in [e["type"] for e in events.read(ws)]


def test_cli_gate_answer_adopt_with_a_missing_proposal_leaves_the_gate_open(tmp_path, monkeypatch):
    """C1's other half: a proposal that vanished between proposing and
    adopting must not be recorded as answered from the terminal either — it
    stays `open` so a fixed file can be adopted on retry."""
    from scieflow.core.gates import gate
    from scieflow.core.run import charter

    project, ws = _cli_charter_env(tmp_path)
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Here now, gone later.")
    g = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                        options=["adopt", "decline"], files=[proposal])
    proposal.unlink()
    monkeypatch.chdir(tmp_path)

    cli = CliRunner()
    result = cli.invoke(gate, ["answer", "r1", g["id"], "adopt"])

    assert result.exit_code != 0
    assert charter.current_text(ws) == ""
    assert gates.get(ws, g["id"])["state"] == "open"
