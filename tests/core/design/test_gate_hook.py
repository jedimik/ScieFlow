"""M-D1 t4 (plan 5.5, 5.9): the design-lock gate kind and its answer hook. Synthetic data only."""

from __future__ import annotations

import json

import pytest

from scieflow.core import gates, service
from scieflow.core.design import lock


def test_the_design_lock_kind_is_schema_data_and_needs_a_human(lproj):
    table = gates.kinds(lproj)
    assert table["design-lock"]["requires_human"] is True
    assert table["design-lock"]["help"].strip()


def test_the_hook_runs_before_the_gate_is_marked_answered(lproj, lws, monkeypatch):
    gate = lock.lock(lproj, lws)
    seen = {}
    real = lock.on_answer

    def spy(project, ws, g, answer, actor):
        seen["state_during_hook"] = gates.get(ws, g["id"])["state"]
        return real(project, ws, g, answer, actor)

    monkeypatch.setattr(lock, "on_answer", spy)
    service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert seen["state_during_hook"] == "open"
    assert gates.get(lws, gate["id"])["state"] == "answered"


def test_a_hook_refusal_leaves_the_gate_open(lproj, lws):
    gate = lock.lock(lproj, lws)
    (lws / "design" / "pending.json").unlink()        # the pending record is gone: no lock to approve
    with pytest.raises(gates.GateError, match="no pending"):
        gates.answer(lproj, lws, gate["id"], "approve", actor="human")
    assert gates.get(lws, gate["id"])["state"] == "open"
    assert not (lws / "design" / "lock.jsonl").exists()


def test_approving_a_design_lock_gate_that_no_lock_opened_is_refused(lproj, lws):
    gate = gates.open_gate(lproj, lws, "design-lock", "Lock the study design of r1 (sha256 000000000000)?",
                           options=["approve", "reject"], actor="agent")
    with pytest.raises(service.ServiceError, match="no pending"):
        service.answer_gate(lproj, "r1", gate["id"], "approve")
    assert gates.get(lws, gate["id"])["state"] == "open"


def test_rejecting_another_gate_keeps_the_pending_lock(lproj, lws):
    pending_gate = lock.lock(lproj, lws)
    other = gates.open_gate(lproj, lws, "design-lock", "Lock the study design of r1 (sha256 111111111111)?",
                            options=["approve", "reject"], actor="agent")
    service.answer_gate(lproj, "r1", other["id"], "reject")
    names = json.loads((lws / "design" / "pending.json").read_text())["gate"]
    assert names == pending_gate["id"]
    assert not (lws / "design" / "lock.jsonl").exists()


def test_reject_removes_its_own_pending_lock_and_writes_nothing(lproj, lws):
    gate = lock.lock(lproj, lws)
    service.answer_gate(lproj, "r1", gate["id"], "reject")
    assert not (lws / "design" / "pending.json").exists()
    assert not (lws / "design" / "lock.jsonl").exists()
    assert gates.get(lws, gate["id"])["answer"] == "reject"
