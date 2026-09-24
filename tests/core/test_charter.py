"""The run charter: a versioned record of what has been agreed."""

import pytest

from scieflow.core import events
from scieflow.core.run import charter


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


def test_a_run_without_a_charter_reads_as_empty(ws):
    assert charter.read(ws) == {"current": 0, "versions": []}
    assert charter.current_text(ws) == ""
    assert charter.history(ws) == []


def test_setting_the_first_version(ws):
    version = charter.set_text(ws, "Find a better catalyst.", note="initial goal")
    assert version["n"] == 1
    assert charter.current_text(ws) == "Find a better catalyst."
    assert [e["type"] for e in events.read(ws)] == ["charter.set"]


def test_each_set_appends_a_version(ws):
    charter.set_text(ws, "First goal.")
    charter.set_text(ws, "Second goal.")
    doc = charter.read(ws)
    assert doc["current"] == 2
    assert [v["n"] for v in doc["versions"]] == [1, 2]
    assert charter.current_text(ws) == "Second goal."


def test_history_is_newest_first_and_carries_provenance(ws):
    charter.set_text(ws, "First.", actor="agent", note="proposed")
    charter.set_text(ws, "Second.", actor="human")
    first_of_history = charter.history(ws)[0]
    assert first_of_history["n"] == 2 and first_of_history["actor"] == "human"
    assert charter.history(ws)[1]["note"] == "proposed"
    assert first_of_history["ts"]


def test_revert_appends_rather_than_rewinding(ws):
    """The history is the feature. A revert that deleted versions would
    destroy the record of how the goal moved."""
    charter.set_text(ws, "Original goal.")
    charter.set_text(ws, "Drifted goal.")
    restored = charter.revert(ws, 1)
    assert restored["n"] == 3
    assert charter.current_text(ws) == "Original goal."
    assert [v["n"] for v in charter.read(ws)["versions"]] == [1, 2, 3]
    assert "charter.reverted" in [e["type"] for e in events.read(ws)]


@pytest.mark.parametrize("bad", [0, -1, 99])
def test_reverting_to_a_version_that_does_not_exist_is_refused(ws, bad):
    """A stale tab, or a typed version number. Expect a readable refusal and
    an unchanged charter, not an IndexError."""
    charter.set_text(ws, "Only version.")
    with pytest.raises(charter.CharterError, match="version"):
        charter.revert(ws, bad)
    assert charter.current_text(ws) == "Only version."
    assert len(charter.read(ws)["versions"]) == 1


def test_empty_text_is_refused(ws):
    with pytest.raises(charter.CharterError):
        charter.set_text(ws, "   ")


def test_concurrent_writers_do_not_lose_a_version(ws):
    """Two tabs, or a tab and the CLI. Versions are append-only and numbered,
    so a lost update or two versions sharing a number would corrupt the very
    history this feature exists to provide."""
    import threading

    errors = []

    def write(i):
        try:
            charter.set_text(ws, f"goal {i}")
        except Exception as exc:              # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    numbers = [v["n"] for v in charter.read(ws)["versions"]]
    assert numbers == list(range(1, 9)), f"lost or duplicated versions: {numbers}"
    assert charter.read(ws)["current"] == 8
