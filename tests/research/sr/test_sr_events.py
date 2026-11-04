"""M-H t2 (plan 5.3): the six sr.* event types are additive members of the closed vocabulary."""

import pytest

from scieflow.core import events

SR_TYPES = ("sr.retrieval", "sr.dedup_decision", "sr.screening_decision", "sr.adjudication",
            "sr.appraisal", "sr.committed")


def test_the_six_sr_types_are_in_the_closed_vocabulary():
    assert len(SR_TYPES) == 6
    assert set(SR_TYPES) <= events.TYPES


@pytest.mark.parametrize("type_", SR_TYPES)
def test_each_sr_type_is_accepted_by_emit(srws, type_):
    event = events.emit(srws, type_, "agent", op="op1", protocol_version=1)
    assert event["type"] == type_
    assert event["data"]["op"] == "op1"


def test_an_unknown_sr_type_is_still_refused(srws):
    with pytest.raises(ValueError, match="sr.bogus"):
        events.emit(srws, "sr.bogus", "agent")
