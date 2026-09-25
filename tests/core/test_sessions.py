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

# agy prints one JSON object per call, not a line-delimited stream of events.
AGY_OUTPUT = json.dumps({
    "conversation_id": "9a563a1a-601b-45aa-8677-10689cf3b31e",
    "status": "SUCCESS",
    "response": "Hello.\n",
    "duration_seconds": 3.001819099,
    "num_turns": 1,
    "usage": {"input_tokens": 18722, "output_tokens": 3, "thinking_tokens": 0,
              "cache_read_tokens": 0, "total_tokens": 18725},
})


def test_claude_session_id_and_text():
    parsed = sessions.parse({"family": "claude"}, CLAUDE_STREAM)
    assert parsed.id == "11ea03ad-e731-4917-8e89-e3f3b4a58776"
    assert "Hello." in parsed.text


def test_codex_session_id_and_text():
    parsed = sessions.parse({"family": "codex"}, CODEX_STREAM)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"
    assert "Hello." in parsed.text


def test_agy_session_id_and_text():
    parsed = sessions.parse({"family": "agy"}, AGY_OUTPUT)
    assert parsed.id == "9a563a1a-601b-45aa-8677-10689cf3b31e"
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


def test_a_truncated_single_object_degrades_instead_of_raising():
    """A cancelled or timed-out call leaves agy's one JSON object unclosed.
    `conversation_id` is written early in it, so it survives truncation even
    though the object as a whole no longer parses."""
    truncated = AGY_OUTPUT[:len(AGY_OUTPUT) // 2]
    parsed = sessions.parse({"family": "agy"}, truncated)
    assert parsed.id == "9a563a1a-601b-45aa-8677-10689cf3b31e"


def test_output_with_no_session_id_parses_as_none():
    parsed = sessions.parse({"family": "claude"}, "not json at all\nnor this\n")
    assert parsed.id is None
    assert parsed.text.strip()


def test_agy_output_with_no_session_id_parses_as_none():
    parsed = sessions.parse({"family": "agy"}, "not json at all\nnor this\n")
    assert parsed.id is None
    assert parsed.text.strip()


def test_noise_before_the_json_is_tolerated():
    """codex writes a models-cache warning to the same stream on this host."""
    noisy = "2026-09-24T22:02:03Z ERROR codex_models_manager: cache miss\n" + CODEX_STREAM
    assert sessions.parse({"family": "codex"}, noisy).id is not None


def test_claude_malformed_message_does_not_raise_and_the_id_survives():
    """A malformed nested value must not crash the parser, and an id already
    read from the same event must not be lost because of it."""
    parsed = sessions.parse(
        {"family": "claude"},
        json.dumps({"type": "assistant", "session_id": "x", "message": "oops"}))
    assert parsed.id == "x"


def test_codex_malformed_item_does_not_raise_and_the_id_survives():
    stream = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "01a0d570-a280-7f22-b14f-04df145c95fc"}),
        json.dumps({"type": "item.completed", "item": "not a dict"}),
    ])
    parsed = sessions.parse({"family": "codex"}, stream)
    assert parsed.id == "01a0d570-a280-7f22-b14f-04df145c95fc"


# json.loads raises RecursionError (a RuntimeError, not a ValueError) on a
# sufficiently nested object; a stray `except ValueError` does not catch it.
_PATHOLOGICALLY_NESTED = '{"a":' + "[" * 100_000 + "]" * 100_000 + "}"


def test_a_pathologically_nested_stream_degrades_instead_of_raising():
    parsed = sessions.parse({"family": "codex"}, _PATHOLOGICALLY_NESTED)
    assert parsed.id is None
    assert parsed.text.strip()


def test_a_pathologically_nested_agy_object_degrades_instead_of_raising():
    parsed = sessions.parse({"family": "agy"}, _PATHOLOGICALLY_NESTED)
    assert parsed.id is None
    assert parsed.text.strip()


def test_can_converse_requires_both_commands():
    assert sessions.can_converse(
        {"family": "claude", "session_cmd": "x {prompt}", "resume_cmd": "y {session}"})
    assert not sessions.can_converse({"cmd": "x {prompt}"})
    assert not sessions.can_converse({"family": "claude", "session_cmd": "x {prompt}"})


def test_can_converse_requires_a_registered_family():
    """A session_cmd/resume_cmd pair with a missing or misspelled family
    would otherwise report True while parse() always returns id=None — the
    silent session loss this module exists to prevent."""
    assert not sessions.can_converse(
        {"session_cmd": "x {prompt}", "resume_cmd": "y {session}"})
    assert not sessions.can_converse(
        {"family": "not-a-real-family", "session_cmd": "x {prompt}", "resume_cmd": "y {session}"})


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


@pytest.mark.live
@pytest.mark.skipif(shutil.which("agy") is None, reason="agy CLI not installed")
def test_agy_really_reports_and_resumes_a_conversation():
    """Pins the real CLI. agy prints one JSON object per call (not a stream);
    unlike codex's resume, no flag from the starting call was found to be
    rejected on `--conversation`."""
    import subprocess

    start = subprocess.run(
        ["agy", "-p", "Reply with exactly: marker-gamma",
         "--model", "gemini-3.6-flash-low", "--effort", "low",
         "--print-timeout", "55m", "--dangerously-skip-permissions",
         "--output-format", "json"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    parsed = sessions.parse({"family": "agy"}, start.stdout)
    assert parsed.id, f"no conversation id in agy output: {start.stdout[:400]}"

    resumed = subprocess.run(
        ["agy", "-p", "What word did I ask you to reply with? Answer with just that word.",
         "--conversation", parsed.id,
         "--model", "gemini-3.6-flash-low", "--effort", "low",
         "--print-timeout", "55m", "--dangerously-skip-permissions",
         "--output-format", "json"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    assert "marker-gamma" in resumed.stdout, "the resumed conversation lost its context"
