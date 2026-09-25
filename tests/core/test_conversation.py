"""The conversation record: which agent, whose session, what was said."""

import pytest

from scieflow.core import events
from scieflow.core.run import conversation


@pytest.fixture
def ws(tmp_path):
    from scieflow.core.run import status

    workspace = tmp_path / "workspace" / "r1"
    workspace.mkdir(parents=True)
    status.write_status(workspace, status.new_status("r1", "autonomous"))
    return workspace


def test_a_run_without_a_conversation_reads_as_empty(ws):
    assert conversation.read(ws) == {"agent": "", "session": None, "turns": []}


def test_choosing_an_agent_then_recording_a_session(ws):
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "abc-123")
    doc = conversation.read(ws)
    assert doc["agent"] == "claude" and doc["session"] == "abc-123"


def test_turns_accumulate_in_order(ws):
    conversation.set_agent(ws, "claude")
    conversation.add_turn(ws, role="human", text="What next?")
    conversation.add_turn(ws, role="agent", text="Run the sweep.", job_id="J1")
    turns = conversation.read(ws)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert turns[1]["job_id"] == "J1"
    assert turns[0]["ts"]
    kinds = [e["type"] for e in events.read(ws)]
    assert kinds.count("turn.sent") == 1 and kinds.count("turn.received") == 1


def test_switching_the_agent_clears_the_session(ws):
    """A session id means nothing to a different CLI. Switching starts a new
    conversation, and the record must not pretend otherwise."""
    conversation.set_agent(ws, "claude")
    conversation.add_turn(ws, role="human", text="First turn")
    conversation.record_session(ws, "claude-session")
    conversation.set_agent(ws, "codex")
    doc = conversation.read(ws)
    assert doc["agent"] == "codex"
    assert doc["session"] is None
    # Turns are history, not session state — switching agents preserves them
    assert len(doc["turns"]) == 1
    assert doc["turns"][0]["text"] == "First turn"


def test_switching_to_the_same_agent_keeps_the_session(ws):
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "keep-me")
    conversation.set_agent(ws, "claude")
    assert conversation.read(ws)["session"] == "keep-me"


def test_recording_no_session_id_leaves_the_old_one_alone(ws):
    """A turn whose output had no id — the agent crashed, or was cancelled.
    Silently clearing would restart the conversation; silently keeping a
    stale id is what we want, because the session on the CLI's side is
    still there."""
    conversation.set_agent(ws, "claude")
    conversation.record_session(ws, "first")
    conversation.record_session(ws, None)
    assert conversation.read(ws)["session"] == "first"


def test_a_turn_needs_a_role_we_recognise(ws):
    with pytest.raises(conversation.ConversationError):
        conversation.add_turn(ws, role="wizard", text="hi")


def test_concurrent_turns_do_not_lose_one(ws):
    import threading

    conversation.set_agent(ws, "claude")
    threads = [threading.Thread(target=conversation.add_turn, kwargs={
        "ws": ws, "role": "human", "text": f"m{i}"}) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(conversation.read(ws)["turns"]) == 8


def test_invalid_actor_on_add_turn_leaves_file_untouched(ws):
    """An invalid actor must be rejected BEFORE the write, so the file and
    event log don't diverge."""
    conversation.set_agent(ws, "claude")
    with pytest.raises(conversation.ConversationError):
        conversation.add_turn(ws, role="human", text="hi", actor="wizard")
    # File was not modified - no turn was added
    assert conversation.read(ws)["turns"] == []
    # No event was emitted
    assert not events.read(ws)


def test_invalid_actor_on_set_agent_is_rejected(ws):
    """set_agent must validate the actor parameter it accepts."""
    with pytest.raises(conversation.ConversationError):
        conversation.set_agent(ws, "claude", actor="wizard")
    # File was not modified
    assert conversation.read(ws) == {"agent": "", "session": None, "turns": []}


def test_malformed_yaml_in_all_write_paths(ws):
    """Hand-edited non-mapping files produce ConversationError consistently."""
    # Write a YAML list to conversation.yml to simulate hand-editing
    import yaml
    (ws / "conversation.yml").write_text(yaml.safe_dump([1, 2, 3]))

    # All write paths should refuse with ConversationError, not bare exceptions
    with pytest.raises(conversation.ConversationError):
        conversation.set_agent(ws, "claude")
    with pytest.raises(conversation.ConversationError):
        conversation.add_turn(ws, role="human", text="hi")
    with pytest.raises(conversation.ConversationError):
        conversation.record_session(ws, "session-id")
