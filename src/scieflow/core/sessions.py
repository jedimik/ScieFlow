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
import re
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
        except (ValueError, RecursionError):
            # RecursionError is a RuntimeError, not a ValueError, but a
            # sufficiently nested object raises it from deep inside the
            # decoder — it means "unparseable" here just as much as a
            # malformed one does, and must degrade the same way.
            continue


def _claude(output: str) -> Session:
    """`claude -p`'s final `result` event carries the assistant's reply
    verbatim — the same text the `assistant` events' text blocks already
    built up turn by turn. Preferring `result` when it is there (and falling
    back to the accumulated blocks only when it never arrives — a cancelled
    or timed-out turn, say) is what stops the reply being said twice."""
    session_id, blocks, result = None, [], None
    for event in _events(output):
        if session_id is None and event.get("session_id"):
            session_id = str(event["session_id"])
        if event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    blocks.append(block.get("text", ""))
        elif event.get("type") == "result" and event.get("result"):
            result = str(event["result"])
    said = [result] if result is not None else blocks
    return Session(session_id, _readable(said, output))


def _codex(output: str) -> Session:
    session_id, said = None, []
    for event in _events(output):
        if session_id is None and event.get("type") == "thread.started":
            session_id = str(event.get("thread_id") or "") or None
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and item.get("text"):
                said.append(str(item["text"]))
    return Session(session_id, _readable(said, output))


_CONVERSATION_ID_RE = re.compile(r'"conversation_id"\s*:\s*"([^"]*)"')


def _largest_json_object(text: str) -> object | None:
    """`text` parsed whole, or — when a banner or a warning line agy adds
    ahead of (or after) its one JSON object stops the whole thing from
    parsing — the widest `{...}` span inside it, parsed instead.

    Without this, any surrounding noise dropped straight to the
    `conversation_id` regex below, which recovers the id but leaves `said`
    empty — the chat then shows the raw JSON blob (this module's `_readable`
    falls back to the whole raw `output`) instead of the reply the JSON
    object actually carried.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except (ValueError, RecursionError):
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(stripped[start:end + 1])
    except (ValueError, RecursionError):
        return None


def _agy(output: str) -> Session:
    """agy prints one JSON object per call, not a line-delimited stream of
    events — `_events` (built for the other two) doesn't fit here, so this
    parses the object directly instead of bending that helper to it.

    A cancelled or timed-out call can truncate that object mid-flight, and an
    unclosed object doesn't parse at all. `conversation_id` is written early
    in it, so a regex recovers it from the fragment even when the whole
    object cannot be decoded — the same tolerance `_events` gives the
    line-delimited formats, adapted to a single-object one.
    """
    session_id, said = None, []
    obj = _largest_json_object(output)
    if isinstance(obj, dict):
        if obj.get("conversation_id"):
            session_id = str(obj["conversation_id"])
        if obj.get("response"):
            said.append(str(obj["response"]))
    else:
        match = _CONVERSATION_ID_RE.search(output)
        if match:
            session_id = match.group(1)
    return Session(session_id, _readable(said, output))


def _readable(said: list[str], output: str) -> str:
    """What a person sees on the job page.

    Falls back to the raw output when nothing parsed, because an agent that
    failed before emitting any JSON has usually written the useful part —
    an error — in plain text.
    """
    joined = "\n\n".join(part for part in said if part.strip())
    return joined if joined.strip() else output


FAMILIES = {"claude": _claude, "codex": _codex, "agy": _agy}


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
    and resume one, *and* its output can actually be read for the id that
    makes resuming possible. A session_cmd/resume_cmd pair with a missing or
    misspelled `family` would otherwise report True while parse() always
    returns id=None — the exact silent session loss this module exists to
    prevent, with nothing in the logs to say so."""
    return (
        bool(agent_cfg.get("session_cmd"))
        and bool(agent_cfg.get("resume_cmd"))
        and family_of(agent_cfg) in FAMILIES
    )
