"""Reading a session id, and readable text, out of an agent's output.

ScieFlow holds no long-lived agent process. A conversation is a sequence of
jobs, and what makes it a conversation rather than a series of strangers is
the agent's own session id: captured from the first turn, handed back on
every later one.

Each CLI reports it differently and neither documents it as a contract, so
the shapes below are pinned by tests that run the real binaries. The failure
this guards against is silent — a renamed field means every turn starts a
fresh context and the conversation stops remembering, with nothing in the
logs to say so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    id: str | None
    text: str


def _events(output: str):
    """Every parseable JSON object in the stream, in order.

    Line by line, tolerating both noise the CLI writes around its JSON (codex
    emits a models-cache warning on some hosts) and a final truncated line
    from a cancelled or timed-out turn.
    """
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _claude(output: str) -> Session:
    session_id, said = None, []
    for event in _events(output):
        if session_id is None and event.get("session_id"):
            session_id = str(event["session_id"])
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    said.append(block.get("text", ""))
        elif event.get("type") == "result" and event.get("result"):
            said.append(str(event["result"]))
    return Session(session_id, _readable(said, output))


def _codex(output: str) -> Session:
    session_id, said = None, []
    for event in _events(output):
        if session_id is None and event.get("type") == "thread.started":
            session_id = str(event.get("thread_id") or "") or None
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                said.append(str(item["text"]))
    return Session(session_id, _readable(said, output))


def _readable(said: list[str], output: str) -> str:
    """What a person sees on the job page.

    Falls back to the raw output when nothing parsed, because an agent that
    failed before emitting any JSON has usually written the useful part —
    an error — in plain text.
    """
    joined = "\n\n".join(part for part in said if part.strip())
    return joined if joined.strip() else output


FAMILIES = {"claude": _claude, "codex": _codex}


def family_of(agent_cfg: dict) -> str:
    """Which output dialect this agent speaks.

    Declared per agent rather than guessed from its name, so a second agent
    wrapping the same CLI parses correctly.
    """
    return str(agent_cfg.get("family", ""))


def parse(agent_cfg: dict, output: str) -> Session:
    parser = FAMILIES.get(family_of(agent_cfg))
    if parser is None:
        return Session(None, output)
    return parser(output)


def can_converse(agent_cfg: dict) -> bool:
    """An agent can host a conversation only if it can both start a session
    and resume one. Anything less would silently restart the context."""
    return bool(agent_cfg.get("session_cmd")) and bool(agent_cfg.get("resume_cmd"))
