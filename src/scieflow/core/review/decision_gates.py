"""Review gates: the user's per-cluster decisions and blockers (spec §5; plan T2.4/T2.5).

`open_gates(project, ws, round)` runs once the coordinator's matrix checks
(`scieflow review matrix --check` calls it; `scieflow review gates` re-runs it):

- one `review-decision` gate per undecided cluster that is not a blocker,
  options `accept | reject | defer`. It is opened in scope — so an autonomous
  coordinator may answer it, with a rationale — only when the run sets
  `review_decisions: coordinator`;
- one `review-blocker` gate (human only, in every mode) per open blocker
  cluster, options `overrule | fixed`, plus one `review.blocker` event.

Every question starts with `[review round N · cluster K · matrix H]` (H = the
first 12 hex digits of the matrix's sha256). Answering such a gate
(`gates.answer`, hence `scieflow gate answer` and the web) first writes the
decision through `decisions.decide()` — the only writer of decisions.json —
and the gate is marked answered only when that succeeded:

- `review-decision`: the answer is the decision; the gate note becomes the
  decision's note and an agent's rationale its rationale. `decide()` refuses
  an agent unless the run sets `review_decisions: coordinator`.
- `review-blocker`: `overrule` needs the user's reason in the note and is
  recorded as `reject` with that `overrule_reason`, which clears the blocker.
  `fixed` records nothing: the blocker stays open until a later round's
  evidence no longer flags the reference.

A gate opened against an earlier matrix is refused; re-running `open_gates`
withdraws it and opens new gates for the current matrix. A review-kind gate
without that question prefix (opened by hand) stays an ordinary gate: its
answer records nothing, and the revision step still needs recorded decisions.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scieflow.core import events, gates, store
from scieflow.core.review import decisions as decisions_mod
from scieflow.core.review import matrix as matrix_mod

DECISION = "review-decision"
BLOCKER = "review-blocker"
KINDS = (DECISION, BLOCKER)
DECISION_OPTIONS = ("accept", "reject", "defer")
OVERRULE, FIXED = "overrule", "fixed"
BLOCKER_OPTIONS = (OVERRULE, FIXED)
_PREFIX = re.compile(r"^\[review round (?P<round>[1-9][0-9]{0,5}) · cluster "
                     r"(?P<cluster>[A-Za-z][A-Za-z0-9_-]{0,63}) · matrix "
                     r"(?P<matrix>[0-9a-f]{12})\] ")


class DecisionGateError(ValueError):
    """The round's gates cannot be opened (no matrix, or one that does not check)."""


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def _prefix(round_: int, cluster: str, matrix_hash: str) -> str:
    return f"[review round {round_} · cluster {cluster} · matrix {matrix_hash[:12]}] "


def parse(gate: dict) -> dict | None:
    """{round, cluster, matrix} of a review gate's question; None for any other gate."""
    if not isinstance(gate, dict) or gate.get("kind") not in KINDS:
        return None
    m = _PREFIX.match(str(gate.get("question") or ""))
    if not m:
        return None
    return {"round": int(m.group("round")), "cluster": m.group("cluster"),
            "matrix": m.group("matrix")}


def _withdraw(ws: Path, gate: dict, reason: str) -> None:
    with store.locked(gates._path(ws, gate["id"])):
        current = gates.get(ws, gate["id"])
        if current["state"] != "open":
            return
        current.update(state="withdrawn",
                       answered=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       answered_by="system", note=reason)
        store._atomic_write(gates._path(ws, gate["id"]), json.dumps(current, indent=2))
    events.emit(ws, "gate.withdrawn", "system", gate=gate["id"], kind=gate["kind"],
                reason=reason)


def _refs(cluster: dict, flags: dict) -> list[dict]:
    out = []
    for fid in cluster.get("flags") or []:
        f = flags.get(fid)
        if f is not None:
            out.append({k: f[k] for k in ("doi", "key", "detail") if k in f})
    return out


def _decision_question(prefix: str, c: dict) -> str:
    title = _one_line(c.get("title")) or "(untitled)"
    severity = f" ({c['severity']})" if c.get("severity") else ""
    return (f"{prefix}Accept, reject or defer {c['cluster']}{severity}: {title}. "
            f"Coordinator recommends {c['recommendation']}: {_one_line(c['rationale'])}")


def _blocker_question(prefix: str, c: dict, refs: list[dict]) -> str:
    which = "; ".join(" ".join(x for x in (r.get("doi"), f"({r['key']})" if r.get("key")
                                           else None) if x) for r in refs) or "a reference"
    return (f"{prefix}{c['cluster']} blocks merge: {c['blocker_reason']} reference {which}. "
            f"Answer '{OVERRULE}' with your reason in the note to accept the risk, or "
            f"'{FIXED}' once the citation is replaced (it stops blocking when a later "
            "round's evidence no longer flags it).")


def _opened_by_core(ws: Path, gate_id: str) -> bool:
    """True when the run's event log says core (actor system) opened this gate."""
    for e in events.read(ws, types=("gate.opened",)):
        if isinstance(e, dict) and (e.get("data") or {}).get("gate") == gate_id:
            return e.get("actor") == "system"
    return False


def _expected(n: int, matrix_hash: str, c: dict, flags: dict) -> tuple[str, str, list[str]]:
    """(kind, question, options) core opens for cluster `c` of this matrix."""
    prefix = _prefix(n, c["cluster"], matrix_hash)
    if c["blocker"]:
        return BLOCKER, _blocker_question(prefix, c, _refs(c, flags)), list(BLOCKER_OPTIONS)
    return DECISION, _decision_question(prefix, c), list(DECISION_OPTIONS)


def _genuine(ws: Path, gate: dict, expected) -> bool:
    """A gate core opened for the cluster as it stands: kind, question and options
    exactly as core writes them, and a `gate.opened` event by core. An agent can
    open a gate of a review kind with a look-alike question; it never counts."""
    return (expected is not None and gate.get("kind") == expected[0]
            and gate.get("question") == expected[1]
            and list(gate.get("options") or []) == expected[2]
            and _opened_by_core(ws, gate["id"]))


def _flags(ws: Path, n: int) -> dict:
    loaded = matrix_mod._bundle(ws, n)
    return {f["id"]: f for f in (loaded[1]["flags"] if loaded else [])}


def open_gates(project, ws: Path, round_: int) -> dict:
    """Open the round's missing review gates; withdraw open ones that no longer apply.

    Withdrawn: gates of an earlier matrix, look-alike gates core did not open,
    and decision gates of clusters decided meanwhile (e.g. with `review decide`).
    Serialised per round, so concurrent callers never open a gate twice.
    Returns {"opened": [gate], "existing": [gate id], "withdrawn": [gate id]}.
    """
    ws = Path(ws)
    try:
        n = matrix_mod.passes._round(round_)
    except ValueError as exc:
        raise DecisionGateError(str(exc)) from exc
    lock = matrix_mod.passes.round_dir(ws, n) / "gates"
    if not lock.parent.is_dir():
        raise DecisionGateError(f"round {n} has no review directory")
    with store.locked(lock):
        return _open_gates(project, ws, n)


def _open_gates(project, ws: Path, n: int) -> dict:
    from scieflow.core.review import service
    from scieflow.core.review import settings as settings_mod

    try:
        data = matrix_mod.check(ws, n)
    except matrix_mod.MatrixError as exc:
        raise DecisionGateError(f"round {n}: the matrix does not check, no gates "
                                "opened: " + "; ".join(exc.problems)) from exc
    matrix_hash = decisions_mod._matrix_hash(ws, n)
    if matrix_hash is None:
        raise DecisionGateError(f"round {n} has no matrix yet")
    slug = ws.name
    coordinator = settings_mod.load(project, slug)["review_decisions"] == "coordinator"
    decided = (decisions_mod.authentic(ws, n) or {}).get("decisions", {})
    open_blockers = {b["cluster"] for b in service.blockers(project, slug)
                     if b["round"] == n and b["cluster"]}
    flags = _flags(ws, n)
    clusters = {c["cluster"]: c for c in data["clusters"]}
    expected = {cid: _expected(n, matrix_hash, c, flags) for cid, c in clusters.items()}

    report: dict = {"opened": [], "existing": [], "withdrawn": []}
    have: set[str] = set()
    for g in gates.list_gates(ws):
        p = parse(g)
        if p is None or p["round"] != n:
            continue
        cid = p["cluster"]
        why = None
        if p["matrix"] != matrix_hash[:12]:
            why = "the review matrix changed; a new gate replaces this one"
        elif not _genuine(ws, g, expected.get(cid)):
            why = "not a review gate core opened"
        elif g["kind"] == DECISION and cid in decided and g["state"] == "open":
            why = f"{cid} was decided meanwhile"
        if why:
            if g["state"] == "open":
                _withdraw(ws, g, why)
                report["withdrawn"].append(g["id"])
            continue
        if g["state"] in ("open", "answered"):
            have.add(cid)
            report["existing"].append(g["id"])

    files = [f"review/round-{n}/{matrix_mod.MARKDOWN}", f"review/round-{n}/{matrix_mod.MATRIX}"]
    for cid, c in clusters.items():
        if cid in have:
            continue
        kind, question, options = expected[cid]
        if c["blocker"]:
            if cid not in open_blockers:
                continue
            g = gates.open_gate(project, ws, kind, question, options, files, in_scope=False,
                                actor="system")
            events.emit(ws, "review.blocker", "system", round=n, cluster=cid,
                        reason=c["blocker_reason"], references=_refs(c, flags), gate=g["id"])
        else:
            if cid in decided:
                continue
            g = gates.open_gate(project, ws, kind, question, options, files,
                                in_scope=coordinator, actor="system")
        report["opened"].append(g)
    return report


def on_answer(project, ws: Path, gate: dict, answer: str, actor: str, rationale: str,
              note: str) -> None:
    """Record a review gate's answer through decide() before the gate is answered.

    Called by `gates.answer` (under the gate's lock, after the option and agent
    checks); raises GateError — and the gate stays open — when the decision is
    refused. Other gate kinds pass through.
    """
    from scieflow.core.review import settings as settings_mod

    if gate.get("kind") not in KINDS:
        return
    p = parse(gate)
    if p is None:
        # A free-form gate of a review kind (opened by hand or by a protocol, as in
        # M0) stays an ordinary gate: its answer records no decision.
        return
    ws = Path(ws)
    current = decisions_mod._matrix_hash(ws, p["round"])
    if current is None or current[:12] != p["matrix"]:
        raise gates.GateError(f"the review matrix changed since gate {gate['id']} was opened; "
                              f"run `scieflow review gates {ws.name} --round {p['round']}` and "
                              "answer the new gate")
    cid = p["cluster"]
    try:
        data = matrix_mod._load_matrix(ws, p["round"])
    except matrix_mod.MatrixError as exc:
        raise gates.GateError(f"decision not recorded: {exc}") from exc
    cluster = next((c for c in data["clusters"] if c["cluster"] == cid), None)
    expected = (_expected(p["round"], current, cluster, _flags(ws, p["round"]))
                if cluster else None)
    if not _genuine(ws, gate, expected):
        raise gates.GateError(f"gate {gate['id']} is not a review gate core opened for {cid} "
                              "(look-alike or edited question, kind or options); open the "
                              f"real ones with `scieflow review gates {ws.name} --round "
                              f"{p['round']}`")
    if gate["kind"] == BLOCKER:
        if answer == FIXED:
            return
        if not str(note or "").strip():
            raise gates.GateError(f"overruling blocker {cid} needs the user's reason in the "
                                  "note (--note)")
        entry = {"decision": "reject", "overrule_reason": note}
    else:
        entry = {"decision": answer}
        if note:
            entry["note"] = note
        if str(rationale or "").strip():
            entry["rationale"] = rationale
    try:
        settings = settings_mod.load(project, ws.name)
        decisions_mod.decide(ws, p["round"], {cid: entry}, actor, settings)
    except (decisions_mod.DecisionError, ValueError) as exc:
        raise gates.GateError(f"decision not recorded: {exc}") from exc
