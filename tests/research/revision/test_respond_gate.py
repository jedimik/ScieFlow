"""M-G2 t2: the respond gate on M-G1's real revision coverage (M-G2 5.5). Synthetic data only.

The letter is M-G1's invented fixture; no comment text, quote or value may reach a message or an event (M-G2 5.10).
"""

import re

import pytest

from scieflow.core import events
from scieflow.core.run import actions, status
from tests.research.revision.revision_support import events_text
from tests.research.revision.t6_support import adopted_round, change_methods, fill_all, respond, round_path

LEAK_CANARY = "sample size calculation"


@pytest.fixture(autouse=True)
def fresh_status_vocabulary(monkeypatch):
    monkeypatch.setattr(status, "_VOCAB", None)


def start(synth, workflow: str = "paper-revision"):
    status.write_status(synth.run_dir, status.new_status(synth.run_dir.name, "per-campaign", workflow))
    return synth.run_dir


def gate_events(run):
    return events.read(run, types=("note.phase_gate",))


def test_no_revision_folder_refuses_respond(synth):
    run = start(synth)
    with pytest.raises(ValueError) as info:
        actions.mark_phase(run, "respond", "done", "agent")
    assert "respond refused: no decision letter ingested" in str(info.value)
    assert "scieflow manuscript revision ingest ms-run --letter FILE" in str(info.value)
    assert status.read_status(run)["phases"]["respond"] == "pending"


def test_an_adopted_round_with_the_unfilled_starter_is_refused(synth):
    run = start(synth)
    adopted_round(synth)
    respond(synth)
    with pytest.raises(ValueError) as info:
        actions.mark_phase(run, "respond", "done", "agent")
    msg = str(info.value)
    match = re.search(r"respond refused: round 1 coverage has (\d+) problem\(s\)", msg)
    assert match and int(match.group(1)) > 0, msg
    assert "scieflow manuscript revision coverage ms-run --round 1" in msg
    (ev,) = gate_events(run)
    assert ev["data"]["outcome"] == "refused" and ev["data"]["round"] == 1


def test_a_filled_and_changed_round_passes_and_writes_no_coverage_file(synth):
    run = start(synth)
    adopted_round(synth)
    respond(synth)
    fill_all(synth)
    change_methods(synth)
    assert not round_path(synth, "coverage.json").exists()
    actions.mark_phase(run, "respond", "done", "agent")
    assert status.read_status(run)["phases"]["respond"] == "done"
    (ev,) = gate_events(run)
    assert ev["data"]["outcome"] == "passed" and ev["data"]["gate"] == "coverage" and ev["data"]["round"] == 1
    assert not round_path(synth, "coverage.json").exists(), "the gate writes no coverage.json"
    assert events.read(run, types=("note.revision_coverage",)) == []


def test_the_highest_round_is_checked_and_a_missing_adoption_refuses(synth):
    run = start(synth)
    adopted_round(synth)
    respond(synth)
    fill_all(synth)
    change_methods(synth)
    (synth.run_dir / "revision" / "round-2").mkdir()
    with pytest.raises(ValueError, match=r"respond refused: \[adopted-missing\] round 2"):
        actions.mark_phase(run, "respond", "done", "agent")
    (ev,) = gate_events(run)
    assert ev["data"]["rule"] == "adopted-missing" and ev["data"]["round"] == 2


def test_a_linked_revision_folder_is_refused(synth, tmp_path):
    run = start(synth)
    target = tmp_path / "elsewhere"
    (target / "round-1").mkdir(parents=True)
    (synth.run_dir / "revision").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match=r"respond refused: \[symlink\]"):
        actions.mark_phase(run, "respond", "done", "agent")
    assert gate_events(run)[0]["data"]["outcome"] == "refused"


def test_the_gate_leaks_no_letter_text_into_a_message_or_an_event(synth):
    run = start(synth)
    adopted_round(synth)
    respond(synth)
    with pytest.raises(ValueError) as info:
        actions.mark_phase(run, "respond", "done", "agent")
    assert LEAK_CANARY not in str(info.value)
    assert LEAK_CANARY not in events_text(synth)


def test_respond_on_a_paper_draft_run_is_refused_by_the_status_vocabulary(synth):
    run = start(synth, "paper-draft")
    with pytest.raises(ValueError, match="unknown phase: respond"):
        actions.mark_phase(run, "respond", "done", "agent")
    assert gate_events(run) == []
