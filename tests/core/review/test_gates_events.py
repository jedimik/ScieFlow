"""Review gate kinds and review/scite event types (ticket 04, plan T0.6).

`review-decision` may be answered by an autonomous coordinator (in scope, with
a rationale); `review-blocker` and `scite-consent` need a human in every mode,
including through `scieflow gate answer --as-agent`. The edits to the shared
`schemas/gates.yml` and `core/events.py` are additive: every kind and event
type that existed before is still there, unchanged.
"""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[3]

REVIEW_EVENTS = (
    "review.assigned", "review.pass_done", "review.evidence_built",
    "review.matrix_ready", "review.decided", "review.blocker",
    "scite.quota_low", "scite.unavailable",
)
HUMAN_KINDS = ("review-blocker", "scite-consent")

# The vocabulary before ticket 04 (must survive unchanged).
OLD_EVENT_TYPES = {
    "run.created", "run.resumed",
    "phase.pending", "phase.started", "phase.done", "phase.failed",
    "iteration.advanced", "checkpoint", "budget.recorded",
    "job.queued", "job.started", "job.finished", "job.failed", "job.timeout",
    "job.cancelled", "job.lost", "job.refused", "sandbox.disabled",
    "gate.opened", "gate.answered", "gate.withdrawn",
    "charter.set", "charter.reverted",
    "turn.sent", "turn.received", "turn.session_lost",
    "curation.changed", "curation.round",
    "provenance.synced", "provenance.skipped",
    "integration.call", "sync.pushed", "sync.pulled",
}
OLD_GATE_KINDS = {
    "campaign-approval": False, "outline-approval": False, "staffing": False,
    "question": False, "claim-check-consent": True, "upload": True,
    "external-sharing": True, "tier-promotion": True, "scope-change": True,
    "budget-extension": True, "charter-adoption": True,
}


def setup(tmp_path, approval="autonomous"):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", approval))
    return Project(ROOT), ws


def cli_env(tmp_path, approval="autonomous"):
    """A project rooted at tmp_path (cwd) with the real gate kinds."""
    _, ws = setup(tmp_path, approval)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text("agents: {}\n")
    (tmp_path / "schemas").mkdir()
    for name in ("gates", "status"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    return Project(tmp_path), ws


# -- gate kinds ------------------------------------------------------------------
def test_gate_kind_table_is_additive():
    table = yaml.safe_load((ROOT / "schemas" / "gates.yml").read_text())["kinds"]
    for kind, human in OLD_GATE_KINDS.items():
        assert table[kind]["requires_human"] is human, kind
    assert table["review-decision"]["requires_human"] is False
    assert table["review-blocker"]["requires_human"] is True
    assert table["scite-consent"]["requires_human"] is True
    for kind in ("review-decision", *HUMAN_KINDS):
        assert table[kind]["help"].strip()


@pytest.mark.parametrize("kind", ["review-decision", *HUMAN_KINDS])
@pytest.mark.parametrize("approval", ["per-campaign", "autonomous"])
def test_review_gates_open_and_take_a_human_answer(tmp_path, kind, approval):
    project, ws = setup(tmp_path, approval)
    g = gates.open_gate(project, ws, kind, "Decide round 1?", options=["yes", "no"],
                        in_scope=True)
    assert g["state"] == "open"
    assert g["requires_human"] is (kind in HUMAN_KINDS)
    done = gates.answer(project, ws, g["id"], "yes", note="from the terminal")
    assert done["state"] == "answered" and done["answered_by"] == "human"
    assert [e["type"] for e in events.read(ws)] == ["gate.opened", "gate.answered"]


@pytest.mark.parametrize("kind", HUMAN_KINDS)
def test_human_review_gates_refuse_agents_even_in_autonomous_runs(tmp_path, kind):
    project, ws = setup(tmp_path, "autonomous")
    g = gates.open_gate(project, ws, kind, "?", in_scope=True)
    with pytest.raises(gates.GateError, match="human"):
        gates.answer(project, ws, g["id"], "yes", actor="agent", rationale="in scope")
    assert gates.get(ws, g["id"])["state"] == "open"


def test_review_decision_gate_may_be_answered_by_an_autonomous_coordinator(tmp_path):
    project, ws = setup(tmp_path, "autonomous")
    g = gates.open_gate(project, ws, "review-decision", "?", in_scope=True)
    done = gates.answer(project, ws, g["id"], "accept-all", actor="agent",
                        rationale="review_decisions: coordinator")
    assert done["answered_by"] == "agent" and done["rationale"]


def test_review_decision_gate_refuses_agents_in_gated_runs(tmp_path):
    project, ws = setup(tmp_path, "per-campaign")
    g = gates.open_gate(project, ws, "review-decision", "?", in_scope=True)
    with pytest.raises(gates.GateError, match="autonomous"):
        gates.answer(project, ws, g["id"], "x", actor="agent", rationale="r")


@pytest.mark.parametrize("kind", HUMAN_KINDS)
def test_cli_as_agent_is_refused_on_human_review_gates(tmp_path, monkeypatch, kind):
    from scieflow.core.gates import gate

    project, ws = cli_env(tmp_path, "autonomous")
    g = gates.open_gate(project, ws, kind, "?", options=["yes", "no"], in_scope=True)
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    refused = cli.invoke(gate, ["answer", "r1", g["id"], "yes", "--as-agent",
                                "--rationale", "inside scope"])
    assert refused.exit_code != 0
    assert "human" in refused.output
    assert gates.get(ws, g["id"])["state"] == "open"
    ok = cli.invoke(gate, ["answer", "r1", g["id"], "yes"])
    assert ok.exit_code == 0, ok.output
    assert gates.get(ws, g["id"])["answered_by"] == "human"


def test_cli_opens_every_review_kind(tmp_path, monkeypatch):
    from scieflow.core.gates import gate

    _, ws = cli_env(tmp_path)
    monkeypatch.chdir(tmp_path)
    cli = CliRunner()
    for kind in ("review-decision", *HUMAN_KINDS):
        opened = cli.invoke(gate, ["open", "r1", "--kind", kind, "--question", "?", "--json"])
        assert opened.exit_code == 0, opened.output
        assert json.loads(opened.output)["kind"] == kind


# -- events ----------------------------------------------------------------------
def test_event_vocabulary_is_additive():
    assert OLD_EVENT_TYPES <= events.TYPES
    assert events.TYPES == OLD_EVENT_TYPES | set(REVIEW_EVENTS)


@pytest.mark.parametrize("type_", REVIEW_EVENTS)
def test_review_and_scite_events_are_accepted(tmp_path, type_):
    _, ws = setup(tmp_path)
    ev = events.emit(ws, type_, "system", round=1)
    assert ev["type"] == type_
    assert events.read(ws, types=(type_,))[0]["data"] == {"round": 1}


def test_review_events_filter_by_prefix(tmp_path):
    _, ws = setup(tmp_path)
    for t in REVIEW_EVENTS:
        events.emit(ws, t)
    assert [e["type"] for e in events.read(ws, types=("review.*",))] == list(REVIEW_EVENTS[:6])
    assert [e["type"] for e in events.read(ws, types=("scite.*",))] == list(REVIEW_EVENTS[6:])


@pytest.mark.parametrize("type_", [
    "review.unknown", "review", "scite.called", "scite.quota", "review.decided.extra",
])
def test_unknown_types_are_still_refused(tmp_path, type_):
    _, ws = setup(tmp_path)
    with pytest.raises(ValueError, match="unknown event type"):
        events.emit(ws, type_)
