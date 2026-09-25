"""A run's conversation: which agent is talking, whose session, what was said.

There is no long-lived process here. Each turn is a job, and this file is
what makes a series of jobs into a conversation — it holds the agent's own
session id, which the next turn hands back to the CLI.

Turns are history and are never rewritten. The session is state and can be
cleared, which is exactly what switching agents does: an id issued by one
CLI means nothing to another.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, store

CONVERSATION_FILE = "conversation.yml"
ROLES = frozenset({"human", "agent"})


class ConversationError(ValueError):
    """A conversation change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CONVERSATION_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(ws: Path) -> dict:
    doc = store.read_yaml(_path(ws), default=None) or {}
    if not isinstance(doc, dict):
        raise ConversationError(f"malformed {CONVERSATION_FILE}: not a mapping")
    return {"agent": str(doc.get("agent") or ""),
            "session": doc.get("session") or None,
            "turns": list(doc.get("turns") or [])}


def set_agent(ws: Path, agent: str, actor: str = "human") -> dict:
    """Choose who is talking. Switching clears the session, because a session
    id is meaningful only to the CLI that issued it."""
    if not agent or not agent.strip():
        raise ConversationError("name an agent")

    def change(doc: dict) -> dict:
        current = dict(doc or {})
        if str(current.get("agent") or "") != agent:
            current["session"] = None
        current["agent"] = agent
        current.setdefault("turns", [])
        return current

    store.update_yaml(_path(ws), change)
    return read(ws)


def record_session(ws: Path, session_id: str | None) -> dict:
    """Remember the id this turn reported.

    `None` leaves the previous id alone: a turn can fail or be cancelled
    before printing one, and the session on the CLI's side is still there.
    """
    if not session_id:
        return read(ws)
    store.update_yaml(_path(ws), lambda doc: {**(doc or {}), "session": str(session_id)})
    return read(ws)


def add_turn(ws: Path, *, role: str, text: str, job_id: str = "",
             actor: str = "human") -> dict:
    if role not in ROLES:
        raise ConversationError(f"unknown turn role {role!r} (one of {', '.join(sorted(ROLES))})")
    turn = {"role": role, "text": text, "job_id": job_id, "ts": _now()}

    def append(doc: dict) -> dict:
        current = dict(doc or {})
        current["turns"] = [*(current.get("turns") or []), dict(turn)]
        return current

    store.update_yaml(_path(ws), append)
    events.emit(ws, "turn.sent" if role == "human" else "turn.received",
                actor, role=role, job=job_id)
    return turn
