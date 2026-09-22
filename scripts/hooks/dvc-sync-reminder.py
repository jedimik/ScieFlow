#!/usr/bin/env python3
"""UserPromptSubmit hook: notice when the user is wrapping up.

Phrases like "pause now", "sync with dvc", "upload the agent chat" or
"finalize workspace" mean the session's work should be handed over to
storage before it stops. The hook does not sync anything itself — it reminds
the agent to follow skills/workspace-sync/SKILL.md, which asks before
uploading anything large.

Reads the hook JSON on stdin, prints hook JSON on stdout, never fails the
turn: any error exits 0 silently.
"""

import json
import re
import sys

TRIGGERS = re.compile(
    r"""
    \b(
        pause \s+ (now|here|the\s+work|for\s+(now|today))
      | (let'?s\s+)? (pause|stop|wrap\s*up|finish|finalize|finalise) \s+ (the\s+)?
        (work|session|workspace|run|project|for\s+today)
      | (sync|synchronise|synchronize) \s+ (with\s+|to\s+)? dvc
      | dvc \s+ sync
      | (upload|push|back\s*up|backup|save) \s+ (the\s+|my\s+|our\s+)?
        (agent\s+)? (chat|chats|conversation|session\s+history)
      | (upload|push|back\s*up|backup|sync|save|finalize|finalise) \s+ (the\s+|my\s+|our\s+)?
        (workspace|run\s+data|results|project\s+data)
      | finalize \s+ workspace | finalise \s+ workspace
      | done \s+ for \s+ (today|now|the\s+day)
      | (that'?s|thats) \s+ (it|all) \s+ for \s+ (today|now)
      | call \s+ it \s+ a \s+ day
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

REMINDER = (
    "The user is wrapping up or asked to sync. Follow "
    "skills/workspace-sync/SKILL.md now: report what changed in the run you "
    "are working on (`uv run scieflow workspace sync-status <slug>`), ask "
    "before uploading anything large or numerous, then push the workspace "
    "and this project's agent chat. Do not upload anything without the "
    "user's yes."
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not TRIGGERS.search(prompt):
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": REMINDER,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — a hook must never break the turn
        pass
