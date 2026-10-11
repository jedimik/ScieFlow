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
import re
from collections.abc import Mapping

import yaml

from scieflow.core import events, store

CONVERSATION_FILE = "conversation.yml"
ROLES = frozenset({"human", "agent"})
FINAL_STATES = frozenset({'done', 'failed', 'timeout', 'cancelled', 'lost'})
AUDIT_FIELDS = frozenset({'schema_version', 'turn_id', 'input_kind', 'composition_order',
                        'selected_manager', 'family', 'policy_revision', 'message_sha256',
                        'charter_version', 'charter_sha256', 'composed_artifact', 'job_id',
                        'parse_complete', 'reason', 'accepted_revision', 'final_revision'})
_ID = re.compile(r'[0-7][0-9A-HJKMNP-TV-Z]{25}')
_REVISION = re.compile(r'(?:0|[1-9][0-9]*)')


class ConversationError(ValueError):
    """A conversation change that cannot be made; nothing was written."""


def _path(ws: Path) -> Path:
    return Path(ws) / CONVERSATION_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _guard_dict(doc) -> dict:
    """Ensure doc is a dict, raising ConversationError if not."""
    if not isinstance(doc, dict):
        raise ConversationError(f"malformed {CONVERSATION_FILE}: not a mapping")
    return doc


def _validate_state(doc):
    _guard_dict(doc)
    generation = doc.get('selection_generation', 0)
    if type(generation) is not int or generation < 0:
        raise ConversationError('malformed selection_generation: expected nonnegative integer')
    if type(doc.get('recovery_required', False)) is not bool:
        raise ConversationError('malformed recovery_required')
    pending = doc.get('pending_turn_id')
    if pending is not None and (type(pending) is not str or not _ID.fullmatch(pending)):
        raise ConversationError('malformed pending_turn_id')
    if not isinstance(doc.get('turns', []), list):
        raise ConversationError('malformed conversation turns')
    return doc


def selection_revision(doc: dict) -> str:
    return str(_validate_state(doc).get('selection_generation', 0))


def check_revision(doc: dict, expected_revision, *, required=False) -> str:
    current = selection_revision(doc)
    if expected_revision is None:
        if required:
            raise ConversationError('current selection revision is required')
    elif (type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision)):
        raise ConversationError('selection revision must be canonical ASCII decimal text')
    elif expected_revision != current:
        raise ConversationError('stale selection revision')
    return current


def _bump(doc):
    doc['selection_generation'] = _validate_state(doc).get('selection_generation', 0) + 1
    return doc


def validate_audit(audit):
    """Finite scalar diagnostic schema; never an executable intent record."""
    if audit is None:
        return None
    if not isinstance(audit, Mapping) or set(audit) != AUDIT_FIELDS:
        raise ConversationError('invalid turn audit fields')
    a = dict(audit)
    if any(type(v) not in (str, int, bool, type(None)) for v in a.values()):
        raise ConversationError('turn audit values must be scalars')
    if type(a['schema_version']) is not int or a['schema_version'] != 1:
        raise ConversationError('unsupported turn audit schema')
    if type(a['turn_id']) is not str or not _ID.fullmatch(a['turn_id']):
        raise ConversationError('invalid audit turn id')
    if a['input_kind'] not in ('chat', 'harness-command') or a['composition_order'] != (
            'command-first' if a['input_kind'] == 'harness-command' else 'charter-first'):
        raise ConversationError('invalid audit composition')
    for name in ('selected_manager', 'family', 'policy_revision', 'composed_artifact', 'job_id', 'reason'):
        if type(a[name]) is not str:
            raise ConversationError('invalid audit text shape')
    if len(a['reason']) > 160 or '\n' in a['reason'] or '\0' in a['reason']:
        raise ConversationError('invalid audit reason')
    if a['job_id'] and not _ID.fullmatch(a['job_id']):
        raise ConversationError('invalid audit job id')
    if a['composed_artifact'] != f"logs/turn-{a['turn_id']}.composed.md":
        raise ConversationError('invalid audit artifact')
    for name in ('message_sha256', 'charter_sha256'):
        if type(a[name]) is not str or re.fullmatch('[0-9a-f]{64}', a[name]) is None:
            raise ConversationError('invalid audit digest')
    if type(a['charter_version']) is not int or a['charter_version'] < 0:
        raise ConversationError('invalid audit charter version')
    if a['parse_complete'] is not None and type(a['parse_complete']) is not bool:
        raise ConversationError('invalid audit parse status')
    for name in ('accepted_revision', 'final_revision'):
        v = a[name]
        if v is None and name == 'final_revision':
            continue
        if type(v) is not str or not _REVISION.fullmatch(v):
            raise ConversationError('invalid audit revision')
    return a


def _correlated(doc, turn_id):
    matches = [t for t in doc.get('turns', []) if isinstance(t, dict) and
               t.get('role') == 'human' and t.get('turn_id') == turn_id]
    if len(matches) != 1:
        raise ConversationError('unknown or ambiguous human turn id')
    return matches[0]


def read(ws: Path) -> dict:
    try:
        doc = store.read_yaml(_path(ws), default=None)
        if doc is None:
            doc = {}
    except yaml.YAMLError as exc:
        raise ConversationError(f"malformed {CONVERSATION_FILE}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConversationError(f"malformed {CONVERSATION_FILE}: not a mapping")
    _validate_state(doc)
    return {"agent": str(doc.get("agent") or ""),
            "session": doc.get("session") or None,
            "turns": list(doc.get("turns") or []),
            'selection_generation': doc.get('selection_generation', 0),
            'recovery_required': doc.get('recovery_required', False),
            'pending_turn_id': doc.get('pending_turn_id')}


def set_agent(ws: Path, agent: str, actor: str = "human", *,
              expected_revision=None, reset_session=False) -> dict:
    """Choose who is talking. Switching clears the session, because a session
    id is meaningful only to the CLI that issued it."""
    if not agent or not agent.strip():
        raise ConversationError("name an agent")
    if actor not in events.ACTORS:
        raise ConversationError(f"unknown actor {actor!r} (one of {', '.join(sorted(events.ACTORS))})")

    if type(reset_session) is not bool:
        raise ConversationError('reset_session must be bool')
    def change(doc: dict) -> dict:
        doc = _validate_state(doc)
        current = dict(doc)
        uncertain = bool(current.get('recovery_required') or current.get('pending_turn_id'))
        check_revision(current, expected_revision, required=reset_session or uncertain)
        if (reset_session or uncertain) and actor != 'human':
            raise ConversationError('deliberate human recovery is required')
        if str(current.get('agent') or '') != agent or reset_session:
            current['session'] = None
            current['recovery_required'] = False
            current['pending_turn_id'] = None
        current['agent'] = agent
        current.setdefault('turns', [])
        return _bump(current)

    try:
        store.update_yaml(_path(ws), change)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConversationError(f"failed to set agent: {exc}") from exc
    return read(ws)


def record_session(ws: Path, session_id: str | None) -> dict:
    """Remember the id this turn reported.

    `None` leaves the previous id alone: a turn can fail or be cancelled
    before printing one, and the session on the CLI's side is still there.
    """
    if not session_id:
        return read(ws)

    def update_session(doc: dict) -> dict:
        doc = _validate_state(doc)
        return _bump({**doc, "session": str(session_id)})

    try:
        store.update_yaml(_path(ws), update_session)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConversationError(f"failed to record session: {exc}") from exc
    return read(ws)


def add_turn(ws: Path, *, role: str, text: str, job_id: str = "",
             actor: str = "human", state: str = "", turn_id: str = '', audit=None) -> dict:
    """`state` is the dispatching job's final state (`done`, `failed`,
    `timeout`, `cancelled`) — recorded only when given, so a turn with none
    (every human turn, and every agent turn from before this field existed)
    doesn't carry a misleading empty value."""
    if role not in ROLES:
        raise ConversationError(f"unknown turn role {role!r} (one of {', '.join(sorted(ROLES))})")
    if actor not in events.ACTORS:
        raise ConversationError(f"unknown actor {actor!r} (one of {', '.join(sorted(events.ACTORS))})")

    turn = {"role": role, "text": text, "job_id": job_id, "ts": _now()}
    if state:
        turn["state"] = state

    captured = validate_audit(audit)
    if turn_id:
        if type(turn_id) is not str or not _ID.fullmatch(turn_id):
            raise ConversationError('invalid turn id')
        turn['turn_id'] = turn_id
    if captured is not None:
        if captured['turn_id'] != turn_id or captured['job_id'] != job_id:
            raise ConversationError('audit correlation mismatch')
        turn['audit'] = captured
    marked = role == 'human' and captured is not None and captured['input_kind'] == 'harness-command'
    if marked:
        turn['finalized'] = False

    def append(doc: dict) -> dict:
        doc = _validate_state(doc)
        current = dict(doc)
        if turn_id and any(t.get('turn_id') == turn_id for t in current.get('turns', []) if isinstance(t, dict)):
            raise ConversationError('duplicate turn id')
        if role == 'human' and captured is not None:
            check_revision(current, captured['accepted_revision'], required=True)
        if marked:
            if current.get('pending_turn_id') or current.get('recovery_required'):
                raise ConversationError('conversation requires deliberate recovery')
            current['pending_turn_id'] = turn_id
        current["turns"] = [*(current.get("turns") or []), dict(turn)]
        return _bump(current)

    try:
        store.update_yaml(_path(ws), append)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConversationError(f"failed to add turn: {exc}") from exc

    events.emit(ws, "turn.sent" if role == "human" else "turn.received",
                actor, role=role, job=job_id)
    return turn


def annotate_turn(ws: Path, turn_id: str, *, job_id: str, audit) -> dict:
    captured = validate_audit(audit)
    if captured is None or captured['turn_id'] != turn_id or captured['job_id'] != job_id:
        raise ConversationError('audit correlation mismatch')
    def annotate(doc):
        _validate_state(doc)
        turn = _correlated(doc, turn_id)
        if turn.get('finalized') is True or (turn.get('job_id') and turn['job_id'] != job_id):
            raise ConversationError('turn correlation already finalized or changed')
        turn.update(job_id=job_id, audit=dict(captured))
        return doc
    try:
        store.update_yaml(_path(ws), annotate)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConversationError(f'failed to annotate turn: {exc}') from exc
    return read(ws)


def finalize_turn(ws: Path, turn_id: str, *, job_id: str, session_id: str | None,
                  reply: str, state: str, audit, recovery_required: bool) -> dict:
    captured = validate_audit(audit)
    if (captured is None or captured['turn_id'] != turn_id or captured['job_id'] != job_id or
            captured['input_kind'] != 'harness-command' or state not in FINAL_STATES or
            type(reply) is not str or type(recovery_required) is not bool):
        raise ConversationError('invalid final turn correlation/state')
    if not recovery_required and (type(session_id) is not str or not session_id):
        raise ConversationError('usable completion requires a session')
    result = {}
    def finalize(doc):
        _validate_state(doc)
        human = _correlated(doc, turn_id)
        if (doc.get('pending_turn_id') != turn_id or human.get('finalized') is not False or
                (human.get('job_id') and human['job_id'] != job_id)):
            raise ConversationError('turn is not the unfinalized pending attempt')
        _bump(doc)
        final_audit = {**captured, 'final_revision': selection_revision(doc)}
        human.update(job_id=job_id, audit=dict(final_audit), finalized=True)
        result.update(role='agent', text=reply, job_id=job_id, ts=_now(), state=state,
                      turn_id=turn_id, audit=dict(final_audit))
        doc['turns'] = [*doc.get('turns', []), dict(result)]
        doc.update(session=None if recovery_required else session_id,
                   recovery_required=recovery_required, pending_turn_id=None)
        return doc
    try:
        store.update_yaml(_path(ws), finalize)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConversationError(f'failed to finalize turn: {exc}') from exc
    events.emit(ws, 'turn.received', 'agent', role='agent', job=job_id)
    return result
