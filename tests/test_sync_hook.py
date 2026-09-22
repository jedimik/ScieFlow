"""The UserPromptSubmit hook that reminds an agent to sync before stopping."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "dvc-sync-reminder.py"


def fire(payload) -> str:
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize("prompt", [
    "pause now",
    "Pause here please",
    "sync with dvc",
    "dvc sync before we stop",
    "upload agent chat",
    "please back up the conversation",
    "upload workspace",
    "finalize workspace",
    "let's wrap up the session",
    "push the run data",
    "we are done for today",
])
def test_wrap_up_phrases_remind_the_agent(prompt):
    out = json.loads(fire({"prompt": prompt}))
    block = out["hookSpecificOutput"]
    assert block["hookEventName"] == "UserPromptSubmit"
    assert "skills/workspace-sync/SKILL.md" in block["additionalContext"]
    assert "without the user's yes" in block["additionalContext"]


@pytest.mark.parametrize("prompt", [
    "run the tests",
    "what does sync-status do?",
    "explain how dvc works",
    "add a pause button to the UI",
    "",
])
def test_ordinary_prompts_stay_quiet(prompt):
    assert fire({"prompt": prompt}) == ""


@pytest.mark.parametrize("payload", [{}, {"prompt": 42}, {"other": "x"}])
def test_unexpected_payloads_never_break_the_turn(payload):
    assert fire(payload) == ""


def test_malformed_stdin_never_breaks_the_turn():
    done = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and done.stdout == ""


def test_hook_is_registered_in_project_settings():
    settings = json.loads((HOOK.parents[2] / ".claude" / "settings.json").read_text())
    commands = [h["command"] for entry in settings["hooks"]["UserPromptSubmit"]
                for h in entry["hooks"]]
    assert any("dvc-sync-reminder.py" in c for c in commands)
