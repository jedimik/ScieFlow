import pytest

from scieflow.core import events
from scieflow.core.run import status


def make_run(tmp_path):
    ws = tmp_path / "workspace" / "r1"
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return ws


def test_emit_appends_structured_events_with_the_run_id(tmp_path):
    ws = make_run(tmp_path)
    e = events.emit(ws, "phase.started", "agent", phase="hypothesize")
    assert e["run"] == status.read_status(ws)["id"] and e["slug"] == "r1"
    assert events.read(ws) == [e]


def test_unknown_types_are_refused_but_notes_are_free(tmp_path):
    ws = make_run(tmp_path)
    with pytest.raises(ValueError):
        events.emit(ws, "phase.strated")
    assert events.emit(ws, "note.idea", "agent", text="x")["type"] == "note.idea"


def test_read_since_returns_only_later_events_in_file_order(tmp_path):
    ws = make_run(tmp_path)
    a = events.emit(ws, "note.a")
    b = events.emit(ws, "note.b")
    c = events.emit(ws, "note.c")
    assert [e["id"] for e in events.read(ws, since=a["id"])] == [b["id"], c["id"]]
    assert [e["type"] for e in events.read(ws, types=("note.b",))] == ["note.b"]
    assert [e["type"] for e in events.read(ws, types=("note.*",))] == ["note.a", "note.b", "note.c"]


def test_follow_yields_new_events_then_stops(tmp_path):
    ws = make_run(tmp_path)
    events.emit(ws, "note.first")
    seen = []
    for e in events.follow(ws, poll=0.01, stop=lambda: len(seen) >= 1):
        seen.append(e)
    assert [e["type"] for e in seen] == ["note.first"]
