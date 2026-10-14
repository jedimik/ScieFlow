"""Per-cluster review decisions: review/round-N/decisions.json.

The file is written only by `decide()` (called by `service.decide`, the CLI
`scieflow review decide` and, later, the review-decision gate). Each write is
atomic and followed, under the same lock, by a `review.decided` event that
records the sha256 of the bytes written. `authentic()` trusts a decisions
file only when its hash equals the latest `review.decided` event of that
round: a hand-written or edited file is ignored (Review Focus #7).

Rules (spec §5, decision Q9/Q14/Q17):

- Cluster ids must exist in the round's matrix (which must validate).
- The event carries the whole decisions payload; earlier decisions are
  carried over from the event log, so a lost event or an edited file never
  loses or adds a decision.
- A human (`actor="human"`) may decide any cluster. A coordinator agent
  (`actor="agent"`) may decide only when the run's `review_decisions` is
  `coordinator`, must give a rationale, may never decide a blocker and may
  never change a cluster a human decided.
- Any decision on a blocker cluster needs a human `overrule_reason`. Only a
  `reject` with that reason overrules (clears) the blocker; `accept` or
  `defer` keep it open until a later round's evidence no longer flags it.
- `overrule_reason` on a cluster that is not a blocker is refused.
- Decisions accumulate per round; earlier decisions are kept only while the
  matrix is unchanged (same `matrix_hash`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from scieflow.core import events, store

VERSION = 1
DECISIONS = ("accept", "reject", "defer")
ACTORS = ("human", "agent")
FIELDS = ("decision", "note", "overrule_reason", "rationale")
EVENT = "review.decided"

SCHEMAS = Path(__file__).resolve().parents[2] / "research" / "schemas"


class DecisionError(ValueError):
    """A refused or invalid decision; nothing was written."""


@cache
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text()))


def schema_error(name: str, data) -> str | None:
    errors = sorted(validator(name).iter_errors(data), key=lambda e: e.json_path)
    return f"{errors[0].json_path}: {errors[0].message}" if errors else None


def round_dir(ws: Path, round_: int) -> Path:
    return Path(ws) / "review" / f"round-{round_}"


def path(ws: Path, round_: int) -> Path:
    return round_dir(ws, round_) / "decisions.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _matrix_hash(ws: Path, round_: int) -> str | None:
    try:
        return sha256((round_dir(ws, round_) / "matrix.json").read_bytes())
    except OSError:
        return None


def _decided_events(ws: Path, round_: int) -> list[dict]:
    """Well-formed review.decided events of a round, in log order.

    Read straight from the log so that a malformed line (not an object, or
    with data that is not one) is skipped instead of breaking every reader.
    """
    out = []
    for event in store.read_jsonl(Path(ws) / events.EVENTS_FILE):
        if not isinstance(event, dict) or event.get("type") != EVENT:
            continue
        data = event.get("data")
        if (not isinstance(data, dict) or event.get("actor") not in ACTORS
                or isinstance(data.get("round"), bool) or data.get("round") != round_
                or not isinstance(data.get("decisions_hash"), str)
                or not isinstance(data.get("matrix_hash"), str)
                or not isinstance(data.get("clusters"), list)
                or not isinstance(data.get("decisions"), dict)):
            continue
        out.append(event)
    return out


def _state(ws: Path, round_: int, matrix_hash: str | None) -> dict | None:
    """The latest review.decided event of the round, when it is consistent.

    Consistent: it was made against the current matrix (same matrix_hash),
    and every recorded entry carries the actor of the latest event (against
    this matrix) that decided its cluster — so an appended event cannot pass
    off an agent's entry, or an entry nobody decided, as a human decision.
    """
    evs = _decided_events(ws, round_)
    if not evs or matrix_hash is None:
        return None
    latest = evs[-1]["data"]
    if latest["matrix_hash"] != matrix_hash:
        return None                      # decided against an earlier matrix: stale
    actor_of: dict[str, str] = {}
    for event in evs:
        if event["data"]["matrix_hash"] == matrix_hash:
            for cid in event["data"]["clusters"]:
                if isinstance(cid, str):
                    actor_of[cid] = event["actor"]
    for cid, entry in latest["decisions"].items():
        if not isinstance(entry, dict) or entry.get("actor") != actor_of.get(cid):
            return None
        if entry.get("overrule_reason") and entry.get("actor") != "human":
            return None
    return latest


def authentic(ws: Path, round_: int) -> dict | None:
    """decisions.json of a round when it is exactly what decide() last wrote
    against the current matrix; None otherwise (absent, hand-written, edited,
    or stale because the matrix changed since)."""
    matrix_hash = _matrix_hash(ws, round_)
    state = _state(ws, round_, matrix_hash)
    if state is None:
        return None
    try:
        raw = path(ws, round_).read_bytes()
    except OSError:
        return None
    if sha256(raw) != state["decisions_hash"]:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if (not isinstance(doc, dict) or doc.get("matrix_hash") != matrix_hash
            or doc.get("decisions") != state["decisions"]):
        return None
    return doc


def _text(cid: str, key: str, value, *, blank_ok: bool) -> str:
    if not isinstance(value, str) or (not blank_ok and not value.strip()):
        raise DecisionError(f"{cid}: {key} must be a non-empty string" if not blank_ok
                            else f"{cid}: {key} must be a string")
    return value


def _check(cid, entry, cluster: dict | None, actor: str) -> dict:
    if not isinstance(cid, str) or cluster is None:
        raise DecisionError(f"unknown cluster {cid!r} (not in this round's matrix)")
    if not isinstance(entry, dict):
        raise DecisionError(f"{cid}: expected a mapping with keys {', '.join(FIELDS)}, "
                            f"got {entry!r}")
    extra = sorted(set(entry) - set(FIELDS))
    if extra:
        raise DecisionError(f"{cid}: unknown field {extra[0]!r} (fields: {', '.join(FIELDS)})")
    if entry.get("decision") not in DECISIONS:
        raise DecisionError(f"{cid}: decision {entry.get('decision')!r} is not one of "
                            f"{', '.join(DECISIONS)}")
    out = {"decision": entry["decision"]}
    if "note" in entry:
        out["note"] = _text(cid, "note", entry["note"], blank_ok=True)
    if "rationale" in entry:
        out["rationale"] = _text(cid, "rationale", entry["rationale"], blank_ok=False)
    if "overrule_reason" in entry:
        out["overrule_reason"] = _text(cid, "overrule_reason", entry["overrule_reason"],
                                       blank_ok=False)
    if cluster.get("blocker"):
        if actor != "human":
            raise DecisionError(f"{cid} is a blocker ({cluster.get('blocker_reason')}): only "
                                "a human may decide it")
        if "overrule_reason" not in out:
            raise DecisionError(f"{cid} is a blocker ({cluster.get('blocker_reason')}): a "
                                "decision needs the user's overrule_reason")
    elif "overrule_reason" in out:
        raise DecisionError(f"{cid}: overrule_reason given, but {cid} is not a blocker")
    if actor == "agent" and "rationale" not in out:
        raise DecisionError(f"{cid}: an agent decision needs a rationale")
    return out


def decide(ws: Path, round_: int, decisions: dict, actor: str, settings: dict,
           now: str | None = None) -> dict:
    """Validate, merge and write decisions for one round; emits review.decided."""
    if actor not in ACTORS:
        raise DecisionError(f"actor {actor!r} is not one of {', '.join(ACTORS)}")
    if actor == "agent" and settings.get("review_decisions") != "coordinator":
        raise DecisionError("only the user decides review comments in this run "
                            "(review_decisions: human); an agent may decide only with "
                            "review_decisions: coordinator")
    if not isinstance(decisions, dict):
        raise DecisionError(f"decisions: expected a mapping cluster -> decision, "
                            f"got {decisions!r}")
    if not decisions:
        raise DecisionError("decisions: give at least one cluster")
    matrix_path = round_dir(ws, round_) / "matrix.json"
    target = path(ws, round_)
    if not matrix_path.is_file():
        # Checked before locking too: the lock would create the round directory.
        raise DecisionError(f"round {round_} has no matrix yet: nothing to decide")
    with store.locked(target):
        try:
            matrix_raw = matrix_path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            raise DecisionError(f"round {round_} has no matrix yet: nothing to decide") from None
        try:
            matrix = json.loads(matrix_raw)
        except ValueError as exc:
            raise DecisionError(f"round {round_}: matrix.json is not valid JSON: {exc}") from exc
        problem = schema_error("review-matrix", matrix)
        if problem:
            raise DecisionError(f"round {round_}: matrix.json does not match the "
                                f"review-matrix schema: {problem}")
        clusters = {c["cluster"]: c for c in matrix["clusters"]}
        checked = {cid: _check(cid, entry, clusters.get(cid) if isinstance(cid, str) else None,
                               actor)
                   for cid, entry in decisions.items()}
        matrix_hash = sha256(matrix_raw)
        # Carry earlier decisions over from the event log, not the file: a
        # file left without its event (crash, failed emit) or hand-edited must
        # neither lose nor smuggle in decisions.
        previous = _state(ws, round_, matrix_hash)
        kept = {}
        if previous:
            kept = {k: v for k, v in previous["decisions"].items() if k in clusters}
        if actor == "agent":
            for cid in checked:
                if (kept.get(cid) or {}).get("actor") == "human":
                    raise DecisionError(f"{cid} was decided by the user (human); an agent may "
                                        "not change a human decision")
        stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        for cid, entry in checked.items():
            kept[cid] = {**entry, "actor": actor, "decided": stamp}
        data = {"version": VERSION, "round": round_, "matrix_hash": matrix_hash,
                "decisions": {cid: kept[cid] for cid in clusters if cid in kept}}
        problem = schema_error("review-decisions", data)
        if problem:
            raise DecisionError(f"decisions do not match the review-decisions schema: {problem}")
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        store._atomic_write(target, text)
        events.emit(ws, EVENT, actor=actor, round=round_,
                    clusters=[cid for cid in clusters if cid in checked],
                    decisions_hash=sha256(text.encode()), matrix_hash=matrix_hash,
                    decisions=data["decisions"])
    return data
