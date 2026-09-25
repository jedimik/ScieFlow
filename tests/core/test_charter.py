"""The run charter: a versioned record of what has been agreed."""

from pathlib import Path

import pytest

from scieflow.core import events
from scieflow.core.run import charter

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


@pytest.fixture
def project_ws(tmp_path):
    """A `(Project, ws)` pair for run `r1`, with `schemas/gates.yml` copied
    in so `gates.kinds(project)` can read it."""
    from scieflow.core.project import Project
    from scieflow.core.run import status

    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "gates.yml").write_text(
        (ROOT / "schemas" / "gates.yml").read_text())

    project = Project(tmp_path)
    workspace = project.run_dir("r1")
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return project, workspace


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


def test_an_invalid_actor_in_set_text_is_refused_before_anything_is_written(ws):
    """An invalid actor must not create a version, because that would leave
    the file in disagreement with the event log."""
    with pytest.raises(charter.CharterError, match="actor"):
        charter.set_text(ws, "goal text", actor="bogus")
    assert charter.read(ws)["versions"] == []


def test_an_invalid_actor_in_revert_is_refused_before_anything_is_written(ws):
    """An invalid actor must not create a version, even on revert."""
    charter.set_text(ws, "Original goal.")
    with pytest.raises(charter.CharterError, match="actor"):
        charter.revert(ws, 1, actor="invalid")
    assert len(charter.read(ws)["versions"]) == 1


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


def test_a_proposal_becomes_the_charter_when_the_gate_is_answered(project_ws):
    """The coordinator proposes; a human adopts. The decision is recorded
    with an actor and a timestamp, like every other approval."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Goal: characterise the catalyst before scaling.")

    gate = gates.open_gate(project, ws, "charter-adoption",
                           "Adopt this plan as the run's charter?",
                           options=["adopt", "decline"], files=[proposal])
    assert gate["requires_human"] is True

    service.answer_gate(project, "r1", gate["id"], "adopt")

    assert charter.current_text(ws) == "Goal: characterise the catalyst before scaling."
    assert charter.history(ws)[0]["actor"] == "human"
    assert "adopted from a proposal" in charter.history(ws)[0]["note"]


def test_declining_a_proposal_changes_nothing(project_ws):
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("A plan nobody wants.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    service.answer_gate(project, "r1", gate["id"], "decline")

    assert charter.current_text(ws) == ""


def test_an_agent_cannot_adopt_its_own_proposal(project_ws):
    """requires_human is the point: a coordinator that could rewrite its own
    goal is not autonomous within a scope, it is unbounded."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Let me do whatever I like.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    with pytest.raises(service.ServiceError):
        service.answer_gate(project, "r1", gate["id"], "adopt",
                            actor="agent", rationale="I wrote it myself")
    assert charter.current_text(ws) == ""


def test_adopting_a_proposal_whose_file_is_gone_is_refused(project_ws):
    """The agent's run is writable by the agent, so the file it named can be
    deleted between proposing and adopting."""
    from scieflow.core import gates, service
    from scieflow.core.run import charter

    project, ws = project_ws
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Here now, gone later.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])
    proposal.unlink()

    with pytest.raises(service.ServiceError, match="proposal"):
        service.answer_gate(project, "r1", gate["id"], "adopt")
    assert charter.current_text(ws) == ""
