"""Reading a session id, and readable text, out of an agent's output.

The offline tests here use captured fixture lines. The `live` tests run the
real CLIs and are deselected by default (`pyproject.toml` sets
`addopts = "-m 'not slow and not live'"`), because they cost money and network
— but they are the only thing that catches a CLI changing its output between
releases, which is this feature's most likely silent failure.
"""

import json
import shutil

import pytest

from scieflow.core import sessions

CLAUDE_STREAM = "\n".join([
    json.dumps({"type": "system", "subtype": "init",
                "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776"}),
    json.dumps({"type": "assistant", "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776",
                "message": {"content": [{"type": "text", "text": "Hello."}]}}),
    json.dumps({"type": "result", "subtype": "success",
                "session_id": "11ea03ad-e731-4917-8e89-e3f3b4a58776",
                "result": "Hello."}),
])

CODEX_STREAM = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "01a0d570-a280-7f22-b14f-04df145c95fc"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "Hello."}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
])


def test_claude_session_id_and_text():
    parsed = sessions.parse({"family": "claude"}, CLAUDE_STREAM)
    assert parsed.id == "11ea03ad-e731-4917-8e89-e3f3b4a58776"
    assert "Hello." in parsed.text


def test_codex_session_id_and_text():
    parsed = sessions.parse({"family": "codex"}, CODEX_STREAM)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"
    assert "Hello." in parsed.text


def test_the_readable_text_is_not_the_raw_stream():
    """A human opens the job page to read what the agent said. Handing them
    the JSON stream would be worse than the plain output they had before."""
    parsed = sessions.parse({"family": "claude"}, CLAUDE_STREAM)
    assert "session_id" not in parsed.text
    assert '"type"' not in parsed.text


def test_a_truncated_stream_degrades_instead_of_raising():
    """A cancelled or timed-out turn leaves a partial line."""
    truncated = CODEX_STREAM[:len(CODEX_STREAM) // 2]
    parsed = sessions.parse({"family": "codex"}, truncated)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"


def test_output_with_no_session_id_parses_as_none():
    parsed = sessions.parse({"family": "claude"}, "not json at all\nnor this\n")
    assert parsed.id is None
    assert parsed.text.strip()


def test_noise_before_the_json_is_tolerated():
    """codex writes a models-cache warning to the same stream on this host."""
    noisy = "2026-09-24T22:02:03Z ERROR codex_models_manager: cache miss\n" + CODEX_STREAM
    assert sessions.parse({"family": "codex"}, noisy).id is not None


def test_can_converse_requires_both_commands():
    assert sessions.can_converse({"session_cmd": "x {prompt}", "resume_cmd": "y {session}"})
    assert not sessions.can_converse({"cmd": "x {prompt}"})
    assert not sessions.can_converse({"session_cmd": "x {prompt}"})


@pytest.mark.live
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_really_reports_and_resumes_a_session():
    """Pins the real CLI. A field rename in a future release fails here
    instead of silently restarting the conversation on every turn."""
    import subprocess

    start = subprocess.run(
        ["claude", "-p", "--output-format=stream-json", "--verbose",
         "--model", "claude-haiku-4-5", "Reply with exactly: marker-alpha"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    parsed = sessions.parse({"family": "claude"}, start.stdout)
    assert parsed.id, f"no session id in claude output: {start.stdout[:400]}"

    resumed = subprocess.run(
        ["claude", "-p", "--resume", parsed.id, "--model", "claude-haiku-4-5",
         "What word did I ask you to reply with? Answer with just that word."],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    assert "marker-alpha" in resumed.stdout, "the resumed session lost its context"


@pytest.mark.live
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_codex_really_reports_and_resumes_a_thread():
    import subprocess

    start = subprocess.run(
        ["codex", "exec", "--json", "--sandbox", "read-only",
         "Reply with exactly: marker-beta"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    parsed = sessions.parse({"family": "codex"}, start.stdout)
    assert parsed.id, f"no thread id in codex output: {start.stdout[:400]}"

    # Flags come BEFORE the id, and `resume` rejects --sandbox.
    resumed = subprocess.run(
        ["codex", "exec", "resume", "--json", parsed.id,
         "What word did I ask you to reply with? Answer with just that word."],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert "marker-beta" in resumed.stdout, "the resumed thread lost its context"
