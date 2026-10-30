"""Identity blockers (M-C2 t5, plan 5.5; decision D18).

`scieflow research verify-bib <run> --round N` records its report under
`review/round-N/identity.json` and emits one `review.identity_checked` event. The event, not the
file, is authoritative: its `refusal` list holds the references the check refuses (`mismatch` or
`unresolvable`; `inconclusive` never blocks). Each such reference has one human-only
`review-blocker` gate whose question carries the check's identity.

- An overrule (`overrule` with a note, a human answer) emits `review.identity_overruled`; it holds
  for the same reference and verdict in later rounds.
- `fixed` records nothing: the blocker stays until a later check no longer refuses the reference.
- A gate core did not open, or one of an older check, is refused by `on_answer`; agents never
  answer (`requires_human`, enforced in core/gates.py).

Messages, events, gates and blocker entries carry a reference's key only as its safe token
(`_token`: the bib key when it is letters, digits and `_.:/+-`, else `k-<16 hex>`), plus its DOI and
verdict: never a raw key with spaces, line breaks or an e-mail address, a title, a query or a body.
`identity.json` keeps the raw report (plan 5.4).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scieflow.core import events, gates, store

CHECKED = "review.identity_checked"
OVERRULED = "review.identity_overruled"
BLOCKER = "review-blocker"
OVERRULE, FIXED = "overrule", "fixed"
BLOCKER_OPTIONS = (OVERRULE, FIXED)
# The verdicts that block; equal to researcher's REFUSAL_GRADE (pinned by a test).
REFUSAL_VERDICTS = frozenset({"mismatch", "unresolvable"})
REASON_PREFIX = "identity-"
FILE = "identity.json"
DEFAULT_BIB = "manuscript/references.bib"

_PREFIX = re.compile(
    r"^\[identity round (?P<round>[1-9][0-9]{0,5}) · ref (?P<ref>[A-Za-z0-9_.:/+-]{1,200}) · "
    r"check (?P<check>[0-9a-f]{12})\] "
)
_SAFE_KEY = re.compile(r"[A-Za-z0-9_.:/+-]{1,200}")


class IdentityError(ValueError):
    """The identity check cannot be recorded (no review round directory, a bad document)."""


def _token(key: str) -> str:
    """The reference's token in a gate question: its bib key, or a hash for an unusual key."""
    key = str(key)
    if _SAFE_KEY.fullmatch(key):
        return key
    return "k-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _ident(verdict: str, doi, key: str) -> tuple[str, str]:
    """A reference's identity across rounds: (verdict, DOI in lower case, else the bib key)."""
    return str(verdict), (str(doi).lower() if doi else str(key))


def _refusals(doc: dict) -> list[dict]:
    out = []
    report = doc.get("report") if isinstance(doc, dict) else None
    for entry in (report or {}).get("entries") or []:
        verdict = entry.get("verdict")
        if verdict not in REFUSAL_VERDICTS:
            continue
        item = {"key": _token(entry.get("key") or ""), "verdict": verdict}
        doi = (entry.get("reference") or {}).get("doi")
        if doi:
            item["doi"] = str(doi)
        out.append(item)
    return out


def _verdict_counts(doc: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    report = doc.get("report") if isinstance(doc, dict) else None
    for entry in (report or {}).get("entries") or []:
        verdict = str(entry.get("verdict") or "")
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def _events_of(ws: Path, *types: str) -> list[dict]:
    """Event objects of the given types whose `data` is an object. Malformed lines are skipped.

    Local to this module on purpose: events.read() assumes every line is an object, and the
    readers of the review layer must tolerate a damaged events.jsonl (test_review_service).
    """
    out = []
    for e in store.read_jsonl(Path(ws) / events.EVENTS_FILE):
        if not isinstance(e, dict) or e.get("type") not in types:
            continue
        data = e.get("data")
        if isinstance(data, dict):
            out.append({**e, "data": data})
    return out


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _items(data: dict) -> list[dict]:
    """The well-formed refusal items of a check event: {key, verdict, doi?} with string values."""
    raw = data.get("refusal")
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not (isinstance(r, dict) and isinstance(r.get("key"), str)
                and r.get("verdict") in REFUSAL_VERDICTS):
            continue
        item = {"key": _token(r["key"]), "verdict": r["verdict"]}
        if isinstance(r.get("doi"), str) and r["doi"]:
            item["doi"] = r["doi"]
        out.append(item)
    return out


def _checks(ws: Path) -> list[dict]:
    out = []
    for e in _events_of(ws, CHECKED):
        if e.get("actor") != "system":          # plan 5.5: only core writes a check
            continue
        data = e["data"]
        sha = data.get("report_sha256")
        if _is_int(data.get("round")) and isinstance(sha, str) and len(sha) >= 12:
            out.append(data)
    return out


def _current(ws: Path):
    """(event data, round, 12-hex check) of the newest check in the run, or None."""
    found = _checks(ws)
    if not found:
        return None
    data = found[-1]
    return data, data["round"], str(data["report_sha256"])[:12]


def _core_opened(ws: Path) -> set[str]:
    ids = {e["data"].get("gate") for e in _events_of(ws, "gate.opened") if e.get("actor") == "system"}
    return {str(i) for i in ids if i}


def _overruled(ws: Path, n: int) -> set[tuple[str, str]]:
    """References a human overruled in round n or an earlier one (same verdict and identity)."""
    out: set[tuple[str, str]] = set()
    for e in _events_of(ws, OVERRULED):
        data = e["data"]
        if e.get("actor") != "human" or not _is_int(data.get("round")) or data["round"] > n:
            continue
        out.add(_ident(str(data.get("verdict") or ""), data.get("doi"),
                       _token(data.get("key") or "")))
    return out


def parse(gate: dict) -> dict | None:
    """{round, token, check} of an identity gate's question; None for any other gate."""
    if not isinstance(gate, dict) or gate.get("kind") != BLOCKER:
        return None
    m = _PREFIX.match(str(gate.get("question") or ""))
    if not m:
        return None
    return {"round": int(m["round"]), "token": m["ref"], "check": m["check"]}


def _question(n: int, check: str, item: dict) -> str:
    token = _token(item["key"])
    what = f"{token} ({item['doi']})" if item.get("doi") else f"{token} (no DOI)"
    return (f"[identity round {n} · ref {token} · check {check}] {what} blocks "
            f"merge: verify-bib says {item['verdict']}. Answer '{OVERRULE}' with your reason in "
            f"the note to accept the risk, or '{FIXED}' once the reference is corrected (it stops "
            "blocking when a later check no longer flags it).")


def _pending(ws: Path):
    """(round, [(refusal item, its open gate or None)]) for the newest check, or None.

    An overruled reference is left out. Only gates core opened count as a reference's gate.
    """
    cur = _current(ws)
    if cur is None:
        return None
    data, n, check = cur
    overruled = _overruled(ws, n)
    core = _core_opened(ws)
    open_gates: dict[str, dict] = {}
    for g in gates.list_gates(ws, "open"):
        p = parse(g)
        if p and g["id"] in core and p["round"] == n and p["check"] == check:
            open_gates[p["token"]] = g
    items = []
    for item in _items(data):
        if _ident(item["verdict"], item.get("doi"), item["key"]) in overruled:
            continue
        items.append((item, open_gates.get(_token(item["key"]))))
    return n, items


def record(project, ws: Path, n: int, doc: dict) -> dict:
    """Write review/round-N/identity.json, emit `review.identity_checked`, sync the gates.

    `doc` is the verify-bib document (version, bib, bib_sha256, sources, report). Returns the
    event data.
    """
    ws = Path(ws)
    rd = ws / "review" / f"round-{n}"
    if not rd.is_dir():
        raise IdentityError(f"round {n} has no review directory")
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    store.write_text(rd / FILE, text)
    data = {
        "round": n,
        "report_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bib": str(doc.get("bib") or DEFAULT_BIB),
        "bib_sha256": str(doc.get("bib_sha256") or ""),
        "sources": [{"source": str(s.get("source")), "status": str(s.get("status"))}
                    for s in doc.get("sources") or []],
        "summary": _verdict_counts(doc),
        "refusal": _refusals(doc),
    }
    events.emit(ws, CHECKED, "system", **data)
    sync(project, ws)
    return data


def sync(project, ws: Path) -> None:
    """Withdraw open identity gates of older checks; open a gate for each open blocker lacking one."""
    ws = Path(ws)
    cur = _current(ws)
    for g in gates.list_gates(ws, "open"):
        p = parse(g)
        if p is None:
            continue
        if cur is None or (p["round"], p["check"]) != (cur[1], cur[2]):
            _withdraw(ws, g, "a newer identity check replaces this gate")
    pending = _pending(ws)
    if pending is None or cur is None:
        return
    n, items = pending
    check = cur[2]
    rel = f"review/round-{n}/{FILE}"
    for item, gate in items:
        if gate is None:
            gates.open_gate(project, ws, BLOCKER, _question(n, check, item),
                            options=BLOCKER_OPTIONS, files=[rel], in_scope=False, actor="system")


def _withdraw(ws: Path, gate: dict, reason: str) -> None:
    path = gates._path(ws, gate["id"])
    with store.locked(path):
        current = gates.get(ws, gate["id"])
        if current["state"] != "open":
            return
        current.update(state="withdrawn", answered=gates._now(), answered_by="system", note=reason)
        store._atomic_write(path, json.dumps(current, indent=2))     # already under the lock
    events.emit(ws, "gate.withdrawn", "system", gate=gate["id"], kind=gate["kind"], reason=reason)


def blockers(project, slug: str) -> list[dict]:
    """Open identity blockers of the newest check, in the shape of service.blockers()."""
    ws = project.run_dir(slug)
    if not ws.is_dir():
        return []
    pending = _pending(ws)
    if pending is None:
        return []
    n, items = pending
    out = []
    for item, gate in items:
        refs = {"key": item["key"]}
        if item.get("doi"):
            refs["doi"] = item["doi"]
        refs["detail"] = f"{item['verdict']} (verify-bib)"
        out.append({"round": n, "cluster": None, "reason": REASON_PREFIX + item["verdict"],
                    "title": None, "flags": [], "references": [refs], "decision": None,
                    "gate": gate["id"] if gate else None})
    return out


def stale(ws: Path) -> dict | None:
    """{round} when the bibliography changed since the newest check, else None."""
    cur = _current(Path(ws))
    if cur is None:
        return None
    data, n, _check = cur
    path = Path(ws) / str(data.get("bib") or DEFAULT_BIB)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if digest == data.get("bib_sha256"):
        return None
    return {"round": n}


def on_answer(project, ws: Path, gate: dict, answer: str, actor: str, rationale: str,
              note: str) -> None:
    """Record an identity gate's answer before the gate is marked answered.

    Raises gates.GateError (the gate stays open) for a gate core did not open, a gate of an older
    check, or an overrule without a note. Other gates pass through.
    """
    p = parse(gate)
    if p is None:
        return
    ws = Path(ws)
    if gate["id"] not in _core_opened(ws):
        raise gates.GateError("this identity gate was not opened by the identity check; refused")
    cur = _current(ws)
    if cur is None or (p["round"], p["check"]) != (cur[1], cur[2]):
        raise gates.GateError(f"this identity gate belongs to an older check: re-run verify-bib "
                              f"--round {p['round']} and answer the new gate")
    if answer != OVERRULE:
        return                                   # `fixed` records nothing
    if not str(note or "").strip():
        raise gates.GateError("overrule needs your reason in the note (--note)")
    item = next((r for r in _items(cur[0]) if _token(r["key"]) == p["token"]), None)
    if item is None:
        raise gates.GateError("the latest identity check does not refuse this reference")
    events.emit(ws, OVERRULED, "human", round=p["round"], key=item["key"],
                doi=item.get("doi"), verdict=item["verdict"], reason=str(note).strip())
