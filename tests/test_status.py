import pytest

import status


def test_new_status_starts_pending():
    st = status.new_status("run-a", "autonomous")
    assert st["iteration"] == 1
    assert st["stopped"] is None
    assert all(v == "pending" for v in st["phases"].values())
    assert list(st["phases"]) == status.PHASES


def test_new_status_rejects_bad_approval():
    with pytest.raises(ValueError):
        status.new_status("run-a", "yolo")


def test_mark_and_next_pending():
    st = status.new_status("run-a", "per-campaign")
    assert status.next_pending(st) == "hypothesize"
    status.mark(st, "hypothesize", "done")
    status.mark(st, "experiment", "running")
    assert status.next_pending(st) == "experiment"
    with pytest.raises(ValueError):
        status.mark(st, "experiment", "sideways")


def test_advance_iteration_requires_all_done():
    st = status.new_status("run-a", "per-campaign")
    with pytest.raises(ValueError):
        status.advance_iteration(st)
    for p in status.PHASES:
        status.mark(st, p, "done")
    status.advance_iteration(st)
    assert st["iteration"] == 2
    assert status.next_pending(st) == "hypothesize"


def test_stop_and_roundtrip(tmp_path):
    st = status.new_status("run-a", "autonomous")
    status.stop(st, "anomaly", detail="metric collapse", resume="resume at phase experiment")
    status.write_status(tmp_path, st)
    st2 = status.read_status(tmp_path)
    assert st2["stopped"]["reason"] == "anomaly"
    assert st2["stopped"]["resume"] == "resume at phase experiment"
    with pytest.raises(ValueError):
        status.stop(st, "because")
