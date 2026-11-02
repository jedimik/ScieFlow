"""M-H t2 (plan 5.3): the ledger adapter. EventsLedger writes sr.* events that only count once a
later sr.committed names their op; view() yields the committed detail events mapped back to the
vendored types, plus protocol_locked and amendment synthesized from the design lock ledger.
Synthetic data only."""

import copy

import pytest
import yaml

from scieflow.core import events, service
from scieflow.core.design import lock
from scieflow.research.sr import ledger as srledger
from scieflow.research.sr.errors import SRError
from scieflow._vendor.researcher_core._ledger import ProvenanceEvent, RunContext

TS = "2026-11-02T10:00:00+00:00"


def _event(type_, payload):
    return ProvenanceEvent(run_id="r1", type=type_, ts=TS, payload=payload)


def _retrieval():
    return _event("retrieval", {"round": 1, "strategy": "s1", "source": "openalex",
                                "query": "synthetic protocol query", "record_ids": ["h1"],
                                "capped": False})


def _writer(ws, op="op1"):
    return srledger.EventsLedger(ws, actor="agent", op=op, extra={"protocol_version": 1})


def test_append_writes_an_sr_event_with_op_extra_and_actor(srws):
    stored = _writer(srws).append(_retrieval())
    rows = events.read(srws, types=("sr.retrieval",))
    assert len(rows) == 1
    assert rows[0]["actor"] == "agent"
    assert rows[0]["data"]["op"] == "op1"
    assert rows[0]["data"]["protocol_version"] == 1
    assert stored.event_id == rows[0]["id"]


def test_append_refuses_a_type_the_adapter_does_not_map(srws):
    with pytest.raises(SRError, match="protocol_locked"):
        _writer(srws).append(_event("protocol_locked", {}))


@pytest.mark.parametrize("key", ["ws", "type_", "actor", "op"])
def test_append_refuses_colliding_payload_keys(srws, key):
    with pytest.raises(SRError, match=key):
        _writer(srws).append(_event("retrieval", {key: "x"}))


def test_uncommitted_detail_events_are_not_read_and_are_listed(srws):
    _writer(srws, op="op1").append(_retrieval())
    view = srledger.view(srws)
    assert list(view.iter_events(type="retrieval")) == []
    assert view.uncommitted_ops == ["op1"]


def test_a_committed_group_is_read_back_as_its_vendored_type(srws):
    writer = _writer(srws)
    writer.append(_retrieval())
    writer.commit("search", files={}, round=1)
    got = list(srledger.view(srws).iter_events(type="retrieval"))
    assert len(got) == 1
    assert got[0].type == "retrieval"
    assert got[0].payload["query"] == "synthetic protocol query"
    assert got[0].run_id == "r1"
    assert srledger.view(srws).uncommitted_ops == []


def test_the_view_ignores_a_run_id_argument_and_stamps_the_run_name(srws):
    writer = _writer(srws)
    writer.append(_retrieval())
    writer.commit("search", files={}, round=1)
    got = list(srledger.view(srws).iter_events(run_id="some-other-run", type="retrieval"))
    assert [e.run_id for e in got] == ["r1"]


def test_a_duplicate_commit_op_is_ledger_invalid(srws):
    writer = _writer(srws)
    writer.commit("search", files={}, round=1)
    writer.commit("search", files={}, round=2)
    with pytest.raises(SRError, match="duplicate"):
        srledger.view(srws)


def test_the_view_refuses_writes(srws):
    with pytest.raises(SRError):
        srledger.view(srws).append(_retrieval())


def test_the_view_of_an_unlocked_run_yields_no_protocol_event(srws):
    assert list(srledger.view(srws).iter_events(type="protocol_locked")) == []


def test_a_lock_and_an_amendment_are_synthesized_into_the_view(srproj, srws):
    gate = lock.lock(srproj, srws)
    service.answer_gate(srproj, "r1", gate["id"], "approve")
    path = srws / "design" / "design.yml"
    design = yaml.safe_load(path.read_text(encoding="utf-8"))
    design["review"]["eligibility"]["include"][0]["text"] = "Synthetic amended criterion"
    path.write_text(yaml.safe_dump(design, sort_keys=False), encoding="utf-8")
    amend = lock.amend(srproj, srws, "criterion wording", "clearer wording")
    service.answer_gate(srproj, "r1", amend["id"], "approve")

    locked = list(srledger.view(srws).iter_events(type="protocol_locked"))
    amended = list(srledger.view(srws).iter_events(type="amendment"))
    assert len(locked) == 1 and locked[0].payload["version"] == 1
    assert len(amended) == 1 and amended[0].payload["version"] == 2
    assert locked[0].event_id.startswith("design-v")
    assert amended[0].payload["content_hash"] != locked[0].payload["content_hash"]


def test_run_context_carries_the_run_name_and_the_extra_keys(srws):
    context = srledger.run_context(srws, {"gate": "g1"})
    assert isinstance(context, RunContext)
    assert context.run_id == "r1"
    assert context.extra == {"gate": "g1"}


def test_the_writer_adds_op_and_its_extra_to_each_event_not_the_context(srws):
    context = srledger.run_context(srws, {"gate": "g1"})
    event = context.event("retrieval", {"round": 1}, TS)
    assert "op" not in event.payload
    assert event.payload == {"round": 1, "gate": "g1"}
