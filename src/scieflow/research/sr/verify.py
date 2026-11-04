"""`sr verify` (M-H plan 5.8, 5.16): the read-only integrity check of a run's systematic-review ledger.

Fail closed. Every SR write command and `sr prisma` runs preflight() first and refuses with
"sr-ledger-invalid: <problem>" until a human repairs the run. verify() writes nothing and does not raise for
a ledger problem: it returns errors (the CLI exits 1) and warnings (uncommitted detail events; an open gate
that already has its commit, the crash window). Messages name the rule and a path or an id, never record
text, a query or a screener's free text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from scieflow.core import events, gates
from scieflow.core.design import lock
from scieflow.research.sr.errors import SRError

COMMITTED = "sr.committed"
PREFIX = "sr-ledger-invalid"
SEARCH, SCREENING, ADJUDICATION = "search", "screening", "adjudication"
DETAIL = frozenset({"sr.retrieval", "sr.dedup_decision", "sr.screening_decision", "sr.adjudication",
                    "sr.appraisal"})
GATE_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
BATCH_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
STAGE_NAMES = frozenset({"title-abstract", "full-text"})  # screening.STAGES; screening imports this module
PROTOCOL_KINDS = frozenset({SEARCH, SCREENING, ADJUDICATION})
ADJUDICATION_KIND = "sr-adjudication"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(ws: Path, rel) -> Path | None:
    """The file at a run-relative path, or None when it is missing, linked or outside the run."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    current = ws
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return None
    return current if current.is_file() else None


def _json(ws: Path, rel: str, errors: list[str]):
    path = _plain(ws, rel)
    if path is None:
        errors.append(f"{rel} is missing or not a plain file")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{rel} is not valid JSON")
        return None


def _jsonl(ws: Path, rel: str, errors: list[str]) -> list | None:
    path = _plain(ws, rel)
    if path is None:
        errors.append(f"{rel} is missing or not a plain file")
        return None
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{rel} is not valid JSON lines")
        return None


def _check_files(ws: Path, op, data: dict, errors: list[str]) -> None:
    files = data.get("files") or {}
    if not isinstance(files, dict):
        errors.append(f"commit {op} holds no file table")
        return
    for rel, digest in sorted(files.items()):
        path = _plain(ws, rel)
        if path is None:
            errors.append(f"{rel} is missing or not a plain file")
        elif _sha256(path) != digest:
            errors.append(f"{rel} differs from its committed sha256")


def _check_protocol(op, kind, data: dict, lock_records: list[dict], errors: list[str]) -> None:
    version, digest = data.get("protocol_version"), data.get("protocol_hash")
    if version is None and digest is None:
        if kind in PROTOCOL_KINDS:  # every writer of these kinds stamps both (plan 5.3): absence is tampering
            errors.append(f"commit {op} names no protocol version or hash")
        return
    if not any(rec.get("seq") == version and rec.get("content_hash") == digest for rec in lock_records):
        errors.append(f"commit {op} names a protocol that matches no design ledger record")


def _round_number(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _op(row: dict) -> str | None:
    op = (row.get("data") or {}).get("op")
    return op if isinstance(op, str) else None


def _check_search(ws: Path, op, number, data: dict, rows: list[dict], errors: list[str]) -> None:
    identified = 0
    losers = 0
    for row in rows:
        detail = row.get("data") or {}
        if detail.get("op") != op:
            continue
        if row.get("type") == "sr.retrieval":
            identified += len(detail.get("record_ids") or [])
        elif row.get("type") == "sr.dedup_decision":
            losers += len(detail.get("losers") or [])
    new_rows = _jsonl(ws, f"sr/rounds/{number}/records.jsonl", errors)
    new_records = len(new_rows) if new_rows is not None else None
    derived = {"identified": identified, "duplicates": losers, "new_records": new_records}
    for key, actual in derived.items():
        if key in data and actual is not None and data[key] != actual:
            errors.append(f"round {number}: committed {key} {data[key]} differs from the round's events ({actual})")
    summary_rel = f"sr/rounds/{number}/round.json"
    if summary_rel in (data.get("files") or {}):
        summary = _json(ws, summary_rel, errors)
        if isinstance(summary, dict):
            for key, actual in derived.items():
                if actual is not None and summary.get(key) != actual:
                    errors.append(f"{summary_rel}: {key} {summary.get(key)} disagrees with the round's "
                                  f"events ({actual})")


def _check_screening(ws: Path, op, data: dict, rows: list[dict], errors: list[str]) -> None:
    stage, batch = data.get("stage"), data.get("batch")
    base = f"sr/screening/{stage}/{batch}"
    doc = _json(ws, f"{base}/batch.json", errors)
    if not isinstance(doc, dict):
        return
    expected: Counter = Counter()
    for agent in doc.get("screeners") or []:
        out = _json(ws, f"{base}/{agent}.json", errors)
        if not isinstance(out, dict):
            continue
        for row in out.get("decisions") or []:
            if isinstance(row, dict):
                reason = str(row.get("reason") or "").strip()
                expected[(agent, row.get("record_id"), stage, row.get("decision"), reason)] += 1
    actual: Counter = Counter()
    for row in rows:
        detail = row.get("data") or {}
        if row.get("type") == "sr.screening_decision" and detail.get("op") == op:
            actual[(detail.get("screener_id"), detail.get("record_id"), detail.get("stage"),
                    detail.get("decision"), detail.get("reason") or "")] += 1
    if actual != expected or sum(actual.values()) != data.get("decisions"):
        errors.append(f"committed screening decisions of batch {batch} differ from its output files")


def _check_adjudication(ws: Path, op, data: dict, rows: list[dict], decided: set[str], errors: list[str],
                        warnings: list[str]) -> None:
    gate_id = data.get("gate")
    dec_rel = next((rel for rel in (data.get("files") or {}) if str(rel).endswith("/decisions.json")), None)
    if dec_rel is None:
        errors.append(f"adjudication commit {op} names no decisions file")
        return
    doc = _json(ws, dec_rel, errors)
    if not isinstance(doc, dict) or not isinstance(doc.get("decisions"), list):
        return
    expected = Counter((row.get("record_id"), row.get("decision"), str(row.get("reason") or ""),
                        row.get("rationale")) for row in doc["decisions"] if isinstance(row, dict))
    actual: Counter = Counter()
    dangling = False
    for row in rows:
        detail = row.get("data") or {}
        if row.get("type") == "sr.adjudication" and detail.get("op") == op:
            actual[(detail.get("record_id"), detail.get("decision"), str(detail.get("reason") or ""),
                    detail.get("rationale"))] += 1
            resolves = detail.get("resolves")
            if not isinstance(resolves, list) or not resolves or any(str(e) not in decided for e in resolves):
                dangling = True
    if actual != expected:
        errors.append(f"committed adjudications of gate {gate_id} differ from decisions.json")
    if dangling:  # removing the screening commit a resolution rests on must not publish a changed flow silently
        errors.append(f"adjudication commit {op} resolves screening decisions that are not committed")
    if not isinstance(gate_id, str) or not GATE_RE.match(gate_id):
        errors.append(f"commit {op} names no gate id")
        return
    try:
        gate = gates.get(ws, gate_id)
    except gates.GateError:
        errors.append(f"gate {gate_id} is missing")
        return
    if gate.get("kind") != ADJUDICATION_KIND:
        errors.append(f"gate {gate_id} is not an {ADJUDICATION_KIND} gate")
    elif gate.get("state") == "answered" and gate.get("answer") == "resolved" and gate.get("answered_by") == "human":
        return
    elif gate.get("state") == "open":
        warnings.append(f"gate {gate_id} is open although its adjudication is committed (crash window; reported)")
    else:
        errors.append(f"gate {gate_id} is not answered resolved by a human")


def _lock_records(ws: Path, errors: list[str]) -> list[dict]:
    state = lock.read_ledger(ws)
    if state.problem:
        errors.append(f"the design lock ledger does not verify: {state.problem}")
        return []
    return list(state.records)


def _check_commit(ws: Path, data: dict, rows: list[dict], lock_records: list[dict], decided: set[str],
                  errors: list[str], warnings: list[str]) -> None:
    op, kind = data.get("op"), data.get("kind")
    _check_files(ws, op, data, errors)
    _check_protocol(op, kind, data, lock_records, errors)
    if kind == SEARCH:
        number = _round_number(data.get("round"))
        if number is None:
            errors.append(f"commit {op} names no valid round number")
            return
        _check_search(ws, op, number, data, rows, errors)
    elif kind == SCREENING:
        if data.get("stage") not in STAGE_NAMES or not isinstance(data.get("batch"), str) \
                or not BATCH_RE.match(data["batch"]):
            errors.append(f"commit {op} names no valid stage or batch")
            return
        _check_screening(ws, op, data, rows, errors)
    elif kind == ADJUDICATION:
        _check_adjudication(ws, op, data, rows, decided, errors, warnings)


def verify(project, ws) -> dict:
    """Every committed operation of the run, checked against its files, its events and the design ledger."""
    ws = Path(ws)
    errors: list[str] = []
    warnings: list[str] = []
    rows = events.read(ws)
    commits: dict = {}
    ordered: list[dict] = []
    for row in rows:
        if row.get("type") != COMMITTED:
            continue
        data = dict(row.get("data") or {})
        op = data.get("op")
        if not isinstance(op, str):
            errors.append("a commit names no operation id")
            continue
        if op in commits:
            errors.append(f"duplicate sr.committed for op {op}: the sr ledger is invalid")
            continue
        commits[op] = data
        ordered.append(data)
    screening_ops = {op for op, data in commits.items() if data.get("kind") == SCREENING}
    decided = {str(row.get("id")) for row in rows
               if row.get("type") == "sr.screening_decision" and _op(row) in screening_ops}
    lock_records = _lock_records(ws, errors)
    for index, data in enumerate(ordered, start=1):
        try:
            _check_commit(ws, data, rows, lock_records, decided, errors, warnings)
        except (TypeError, ValueError, AttributeError, KeyError):  # fail closed, never a traceback
            errors.append(f"commit number {index} holds data that cannot be checked")
    rounds = sorted(n for n in (_round_number(d.get("round")) for d in ordered if d.get("kind") == SEARCH)
                    if n is not None)
    ids: list[str] = []
    for number in rounds:
        new_rows = _jsonl(ws, f"sr/rounds/{number}/records.jsonl", errors)
        ids.extend(str(row.get("id")) for row in (new_rows or []) if isinstance(row, dict))
    if ids != [f"R{i:05d}" for i in range(1, len(ids) + 1)]:
        errors.append(f"corpus ids are not a gapless sequence over the committed rounds ({len(ids)} records)")
    pending = sorted({str(_op(row)) for row in rows
                      if row.get("type") in DETAIL and _op(row) not in commits})
    for op in pending:
        warnings.append(f"uncommitted detail events for operation {op} (not read)")
    counts = Counter(str(d.get("kind")) for d in ordered)
    return {"run": ws.name, "errors": errors, "warnings": warnings, "committed": dict(sorted(counts.items())),
            "ok": not errors}


def preflight(project, ws) -> dict:
    """Refuse a write or a PRISMA derivation while the run's sr ledger does not verify (plan 5.8).

    Returns the verify document, warnings included, when the ledger verifies."""
    doc = verify(project, ws)
    if doc["errors"]:
        raise SRError(f"{PREFIX}: {doc['errors'][0]}")
    return doc
