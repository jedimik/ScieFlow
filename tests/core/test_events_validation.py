"""events.emit validates each record before it is written (M-I t2, plan 5.2)."""

import importlib
from pathlib import Path

import pytest

from scieflow.core import events, store
from scieflow.core.run import status

LEAK = "SECRETMARK"


def records():
    return importlib.import_module("scieflow.core.records")


def make_run(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return ws


def log_bytes(ws):
    path = ws / events.EVENTS_FILE
    return path.read_bytes() if path.exists() else None


class Leaky:
    def __repr__(self):
        return LEAK


@pytest.mark.parametrize("type_, actor, data, rule, leak", [
    ("note.<img src=x onerror=alert(1)>", "human", {}, "note-name", "<img"),
    ("note.", "agent", {}, "note-name", None),
    ("note.a b", "agent", {}, "note-name", None),
    ("note.idea", "agent", {"x": {LEAK, 2}}, "data-value", LEAK),
    ("note.idea", "agent", {"x": LEAK.encode()}, "data-value", LEAK),
    ("note.idea", "agent", {"x": Leaky()}, "data-value", LEAK),
    ("note.idea", "agent", {"x": float("nan")}, "data-finite", None),
    ("job.finished", "system", {}, "required-data", None),
    ("gate.answered", "human", {"gate": "g", "kind": "k"}, "required-data", None),
])
def test_emit_refuses_invalid_records_and_leaves_the_log_unchanged(tmp_path, type_, actor, data, rule, leak):
    ws = make_run(tmp_path)
    events.emit(ws, "note.start", "agent")
    before = log_bytes(ws)
    with pytest.raises(records().EventInvalid) as info:
        events.emit(ws, type_, actor, **data)
    assert info.value.rule == rule
    assert log_bytes(ws) == before
    if leak is not None:
        assert leak not in str(info.value)


def test_emit_refuses_the_hostile_note_name_from_e1(tmp_path):
    ws = make_run(tmp_path)
    with pytest.raises(ValueError):
        events.emit(ws, "note.<img src=x onerror=alert(1)>", "human")
    assert events.read(ws) == []


def test_unknown_type_and_actor_keep_their_existing_messages(tmp_path):
    ws = make_run(tmp_path)
    with pytest.raises(ValueError, match=r"^unknown event type 'phase\.strated' \(free-form events use 'note\.<name>'\)$"):
        events.emit(ws, "phase.strated")
    with pytest.raises(ValueError, match=r"^unknown actor 'robot' \(one of agent, human, system\)$"):
        events.emit(ws, "note.idea", "robot")


def test_every_type_and_free_note_names_are_written_and_read_back(tmp_path):
    ws = make_run(tmp_path)
    module = records()
    written = []
    for type_ in sorted(events.TYPES):
        keys = module.REQUIRED_DATA.get(type_, ())
        written.append(events.emit(ws, type_, "system", **{k: "v" for k in keys}))
    for name in ("note.idea", "note.design_warning", "note.a:b"):
        written.append(events.emit(ws, name, "agent", text="x"))
    assert events.read(ws) == written


def test_path_tuple_and_integral_values_are_written_as_plain_json(tmp_path):
    import numpy as np
    from pathlib import Path as P

    ws = make_run(tmp_path)
    e = events.emit(ws, "note.idea", "agent", path=P("a/b.txt"), pair=(1, 2),
                    n=np.int64(3), flag=True, nested={"refs": [{"id": 1}]})
    assert e["data"] == {"path": "a/b.txt", "pair": [1, 2], "n": 3, "flag": True,
                         "nested": {"refs": [{"id": 1}]}}
    assert type(e["data"]["n"]) is int
    assert events.read(ws)[-1] == e


def test_the_validator_runs_on_the_exact_record_written(tmp_path, monkeypatch):
    ws = make_run(tmp_path)
    written = []
    monkeypatch.setattr(store, "append_jsonl", lambda path, record: written.append((path, record)))
    e = events.emit(ws, "note.idea", "agent", path=Path("p"))
    assert len(written) == 1
    assert written[0][1] is e
    assert e["data"] == {"path": "p"}
