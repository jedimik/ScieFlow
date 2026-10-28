"""Study design lock, amendments and the lock ledger (M-D1 plan 5.5, 5.9).

`design lock` and `design amend` never write the ledger. They check the design, write a pending
record and open a `design-lock` gate that only a human answers. The answer hook, `on_answer`, is the
only writer of design/lock.jsonl and design/locked/: gates.answer calls it before the gate is marked
answered, and it refuses any non-human actor itself. A ledger that does not verify is `ledger-invalid`
and refuses every campaign (campaign_verdict, M-D1 t5).

The rules follow researcher's protocol.py (R:core/researcher_core/protocol.py, model.py; MIT). They are
re-implemented on a ScieFlow ledger file, not vendored (M-D1-D2): lock once; amend only with a summary
and a rationale, recording the superseded hash; the version is derived from the record count; the hash
is SHA-256 of canonical JSON (keys sorted, no NaN).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scieflow.core import gates, store
from scieflow.core.design import model, reporting, render
from scieflow.core.design import schema as design_schema
from scieflow.core.design.model import Absent, Invalid, Problem, Report
from scieflow.core.run import charter

LEDGER = "design/lock.jsonl"
LOCKED = "design/locked"
PENDING = "design/pending.json"
AMENDMENTS = "design/amendments"
OPTIONS = ["approve", "reject"]
RECORD_KEYS = frozenset({
    "seq", "kind", "version", "content_hash", "previous_hash", "previous_version", "summary",
    "rationale", "gate", "approved_by", "approved_at", "charter_version", "copy"})
HEX64 = re.compile(r"[0-9a-f]{64}")
GATE_ID = re.compile(r"[0-9A-Za-z]{1,64}")
CHANGED = ("the design changed since the gate opened; reject this gate and run `scieflow design lock` "
           "or `scieflow design amend` again")


class LockError(ValueError):
    """A lock or amendment the rules do not allow. The message names the rule."""


@dataclass(frozen=True)
class Ledger:
    records: tuple
    problem: str | None


@dataclass
class State:
    status: str
    version: str | None = None
    in_force_hash: str | None = None
    current_hash: str | None = None
    design_hash: str | None = None
    study_type: object = None
    report: Report | None = None
    pending_gate: str | None = None
    sap_metrics: list = field(default_factory=list)
    trail: list = field(default_factory=list)
    problem: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(obj) -> str | None:
    """The canonical hash, or None when the value is not plain JSON (NaN, dates, ...)."""
    try:
        return model.content_hash(obj)
    except (TypeError, ValueError):
        return None


def locked_content(ws, design: dict) -> dict:
    """What a lock covers: the design file and the power file it names (plan 5.2)."""
    return {"design": design, "power": model.power_document(ws, design)}


def _current_hash(ws, design: dict) -> str | None:
    return _hash(locked_content(ws, design))


def _report(project, ws, got) -> Report:
    if isinstance(got, Invalid):
        return Report(errors=[Problem("invalid", "design", got.reason)])
    try:
        return model.check(project, ws, got)
    except design_schema.SchemaError as exc:
        return Report(errors=[Problem("invalid", "schemas/design.yml", str(exc))])


def _listing(head: str, problems) -> str:
    return "; ".join([head] + [str(p) for p in problems])


def _loaded(ws) -> dict:
    got = model.load(ws)
    if isinstance(got, Absent):
        raise LockError("no design/design.yml; create one with: scieflow design init <slug> --type <type>")
    if isinstance(got, Invalid):
        raise LockError(got.reason)
    return got


# --- the ledger ----------------------------------------------------------------------------------

def read_ledger(ws) -> Ledger:
    """The records in order and a problem (or None). Fail closed: any problem refuses campaigns."""
    path = Path(ws) / LEDGER
    if path.is_symlink():
        return Ledger((), "design/lock.jsonl is a symlink")
    if not path.exists():
        return Ledger((), _unrecorded_problem(Path(ws), []))
    if not path.is_file():
        return Ledger((), "design/lock.jsonl is not a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Ledger((), "design/lock.jsonl cannot be read")
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            return Ledger(tuple(records), f"line {number} is not JSON")
        if not isinstance(rec, dict) or set(rec) != RECORD_KEYS:
            return Ledger(tuple(records), f"line {number} does not hold exactly the record keys")
        records.append(rec)
    if not records:
        # An existing but empty ledger lost its records: fail closed, never read it as "no lock".
        return Ledger((), "design/lock.jsonl holds no records")
    problem = _chain_problem(Path(ws), records) or _unrecorded_problem(Path(ws), records)
    return Ledger(tuple(records), problem)


def _unrecorded_problem(ws: Path, records: list) -> str | None:
    """A design-lock gate a human approved that no ledger record names: a removed or deleted record.

    The answer hook appends the record before the gate is marked answered, so an approved gate always
    has its record; the crash window (record present, gate still open) is the reverse case.
    """
    named = {rec.get("gate") for rec in records}
    directory = ws / gates.GATES_DIR
    if directory.is_symlink() or not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        try:
            gate = json.loads(raw)
        except (ValueError, RecursionError):
            gate = None
        if not isinstance(gate, dict):
            if b"design-lock" in raw and path.stem not in named:
                return f"gate file {path.name} is unreadable and may be a design-lock approval"
            continue
        approved = (gate.get("kind") == gates.DESIGN_LOCK_KIND and gate.get("state") == "answered"
                    and gate.get("answer") == "approve" and gate.get("answered_by") == "human")
        if approved and path.stem not in named:
            return f"gate {path.stem} was approved by a human but no ledger record names it"
    return None


def _tree_is_plain(ws: Path) -> bool:
    """False when design/ or design/locked is a symlink or not a directory: nothing is read or written through it."""
    design = ws / model.DESIGN_DIR
    locked = ws / LOCKED
    if design.is_symlink() or not design.is_dir():
        return False
    return not (locked.is_symlink() or (locked.exists() and not locked.is_dir()))


def _chain_problem(ws: Path, records: list) -> str | None:
    for index, rec in enumerate(records):
        seq = index + 1
        where = f"record {seq}"
        if type(rec["seq"]) is not int or rec["seq"] != seq:
            return f"{where}: seq is not {seq}"
        if rec["version"] != f"{seq}.0":
            return f"{where}: version is not {seq}.0"
        if rec["kind"] != ("lock" if seq == 1 else "amendment"):
            return f"{where}: kind is wrong for its position"
        if not isinstance(rec["content_hash"], str) or not HEX64.fullmatch(rec["content_hash"]):
            return f"{where}: content_hash is not a SHA-256"
        if seq == 1:
            expected = ("", "")
        else:
            prior = records[index - 1]
            expected = (prior["content_hash"], prior["version"])
        if (rec["previous_hash"], rec["previous_version"]) != expected:
            return f"{where}: the previous hash or version does not chain"
        if rec["approved_by"] != "human":
            return f"{where}: not approved by a human"
        if rec["copy"] != f"{LOCKED}/v{seq}":
            return f"{where}: copy is not {LOCKED}/v{seq}"
        problem = _gate_problem(ws, rec)
        if problem:
            return f"{where}: {problem}"
        copy = _copy_content(ws, rec["copy"])
        if copy is None:
            return f"{where}: the locked copy is missing or unreadable"
        if _hash(copy) != rec["content_hash"]:
            return f"{where}: the locked copy does not match its hash"
    return None


def _gate_problem(ws: Path, rec: dict) -> str | None:
    gate_id = rec["gate"]
    if not isinstance(gate_id, str) or not GATE_ID.fullmatch(gate_id):
        return "the gate id is malformed"
    path = ws / gates.GATES_DIR / f"{gate_id}.json"
    if path.is_symlink() or not path.is_file():
        return "its gate file is missing"
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return "its gate file is unreadable"
    if not isinstance(gate, dict):
        return "its gate file is unreadable"
    if gate.get("kind") != gates.DESIGN_LOCK_KIND:
        return "its gate is not a design-lock gate"
    if gate.get("state") != "answered" or gate.get("answer") != "approve":
        return "its gate was not approved"
    if gate.get("answered_by") != "human":
        return "its gate was not answered by a human"
    return None


def _copy_content(ws: Path, rel: str) -> dict | None:
    """The locked copy as the hash covers it: {design, power}; None when missing or unreadable."""
    folder = ws / rel
    if not _tree_is_plain(ws) or folder.is_symlink() or not folder.is_dir():
        return None
    design_path = folder / "design.yml"
    if design_path.is_symlink() or not design_path.is_file():
        return None
    try:
        design = yaml.safe_load(design_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError):
        return None
    if not isinstance(design, dict):
        return None
    power = None
    power_path = folder / "power.json"
    if power_path.exists() or power_path.is_symlink():
        if power_path.is_symlink() or not power_path.is_file():
            return None
        try:
            power = json.loads(power_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return None
        if not isinstance(power, dict):
            return None
    return {"design": design, "power": power}


def _trail_entry(rec: dict) -> dict:
    return {key: rec[key] for key in ("seq", "kind", "version", "content_hash", "gate",
                                      "approved_at", "summary", "rationale")}


def verify_ledger(ws) -> str | None:
    """None when the ledger verifies, else the problem (plan 5.5 `verify_ledger`)."""
    return read_ledger(ws).problem


# --- pending gates -------------------------------------------------------------------------------

def _read_pending(ws: Path) -> dict | None:
    path = ws / PENDING
    if path.is_symlink() or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _open_pending_id(ws: Path) -> str | None:
    """The pending change's gate id while that gate is still open (read-only)."""
    pending = _read_pending(ws)
    gate_id = pending.get("gate") if pending else None
    if not isinstance(gate_id, str) or not GATE_ID.fullmatch(gate_id):
        return None
    try:
        gate = gates.get(ws, gate_id)
    except (gates.GateError, OSError, ValueError):
        return None
    if not isinstance(gate, dict):
        return None
    return gate_id if gate.get("state") == "open" else None


def _clear_stale(ws: Path) -> None:
    """A pending record whose gate is answered, withdrawn or missing is stale: remove it."""
    if (ws / PENDING).exists() and _open_pending_id(ws) is None:
        (ws / PENDING).unlink(missing_ok=True)


def _write_pending(ws: Path, doc: dict) -> None:
    store.write_text(ws / PENDING, json.dumps(doc, indent=2, sort_keys=True))


def _recorded(ws: Path, gate_id: str) -> bool:
    """A ledger record already names this gate (the crash window between append and answer)."""
    path = ws / LEDGER
    if path.is_symlink() or not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("gate") == gate_id:
            return True
    return False


# --- rendering and file lists --------------------------------------------------------------------

def _guidelines(project, got: dict) -> dict:
    wanted = got.get("reporting_guidelines")
    if not isinstance(wanted, list):
        return {}
    try:
        known = {c.id: c.verification.get("status", "unknown") for c in reporting.registry(project.root)}
    except reporting.ReportingError:
        return {}
    return {g: known[g] for g in wanted if isinstance(g, str) and g in known}


def _render(project, ws: Path, got: dict, report: Report) -> None:
    text = render.render(got, report, model.content_hash(got), power=model.power_document(ws, got),
                         guidelines=_guidelines(project, got))
    store.write_text(ws / model.DESIGN_DIR / "design.md", text)


def _files(ws: Path, got: dict) -> list:
    files = [f"{model.DESIGN_DIR}/{model.DESIGN_FILE}", f"{model.DESIGN_DIR}/design.md"]
    sample = got.get("sample_size")
    if isinstance(sample, dict) and sample.get("method") == "power" and isinstance(sample.get("power_file"), str):
        files.append(sample["power_file"])
    return files


def _dump(obj) -> list:
    return yaml.safe_dump(obj, sort_keys=True, allow_unicode=True, default_flow_style=False).splitlines()


def _diff(old: dict, new: dict) -> str:
    lines = []
    for label, before, after in (("design.yml", old["design"], new["design"]),
                                 ("power.json", old["power"], new["power"])):
        if before != after:
            lines += difflib.unified_diff(_dump(before), _dump(after),
                                          f"locked/{label}", f"current/{label}", lineterm="")
    if not lines:
        lines = ["the canonical JSON differs in number type only"]
    return "\n".join(lines) + "\n"


# --- lock and amend (CLI-facing, open a gate as the agent) ---------------------------------------

def lock(project, ws) -> dict:
    """Open the design-lock gate for the current design. Returns the gate."""
    ws = Path(ws)
    ledger = read_ledger(ws)
    if ledger.problem:
        raise LockError(f"the lock ledger does not verify ({ledger.problem}); it cannot be locked again")
    if ledger.records:
        raise LockError(f"already locked (version {ledger.records[-1]['version']}): "
                        "use `scieflow design amend`")
    got = _loaded(ws)
    _clear_stale(ws)
    open_id = _open_pending_id(ws)
    if open_id:
        raise LockError(f"answer or reject gate {open_id} first")
    report = _report(project, ws, got)
    if report.errors:
        raise LockError(_listing("cannot lock a design with errors:", report.errors))
    digest = _current_hash(ws, got)
    if digest is None:
        raise LockError("the design holds a value that is not plain JSON (rule json-value)")
    _render(project, ws, got, report)
    gate_id = store.new_id()
    _write_pending(ws, {"gate": gate_id, "kind": "lock", "content_hash": digest, "opened_at": _now()})
    question = f"Lock the study design of {ws.name} (sha256 {digest[:12]}, {len(report.warnings)} warnings)?"
    return gates.open_gate(project, ws, gates.DESIGN_LOCK_KIND, question, options=OPTIONS,
                           files=_files(ws, got), in_scope=False, actor="agent", gate_id=gate_id)


def amend(project, ws, summary: str, rationale: str) -> dict:
    """Open the design-lock gate for an amendment of the in-force version. Returns the gate."""
    ws = Path(ws)
    ledger = read_ledger(ws)
    if ledger.problem:
        raise LockError(f"the lock ledger does not verify ({ledger.problem}); it cannot be amended")
    if not ledger.records:
        raise LockError("the design is not locked yet (nothing to amend): use `scieflow design lock`")
    got = _loaded(ws)
    report = _report(project, ws, got)
    if report.errors:
        raise LockError(_listing("cannot amend a design with errors:", report.errors))
    digest = _current_hash(ws, got)
    if digest is None:
        raise LockError("the design holds a value that is not plain JSON (rule json-value)")
    last = ledger.records[-1]
    if digest == last["content_hash"]:
        raise LockError(f"nothing to amend: the design matches version {last['version']}")
    if not summary.strip() or not rationale.strip():
        raise LockError("an amendment needs a summary and a rationale")
    _clear_stale(ws)
    open_id = _open_pending_id(ws)
    if open_id:
        raise LockError(f"answer or reject gate {open_id} first")
    old = _copy_content(ws, last["copy"])
    if old is None:
        raise LockError("the in-force locked copy is missing or unreadable")
    seq = len(ledger.records) + 1
    store.write_text(ws / AMENDMENTS / f"v{seq}.diff", _diff(old, locked_content(ws, got)))
    _render(project, ws, got, report)
    gate_id = store.new_id()
    _write_pending(ws, {"gate": gate_id, "kind": "amendment", "content_hash": digest,
                        "summary": summary.strip(), "rationale": rationale.strip(), "opened_at": _now()})
    question = (f"Amend the locked study design of {ws.name} to version {seq}.0 "
                f"(sha256 {digest[:12]}): {summary.strip()}?")
    files = _files(ws, got) + [f"{AMENDMENTS}/v{seq}.diff"]
    return gates.open_gate(project, ws, gates.DESIGN_LOCK_KIND, question, options=OPTIONS,
                           files=files, in_scope=False, actor="agent", gate_id=gate_id)


# --- the answer hook (called by gates.answer before the gate is marked answered) ----------------

def on_answer(project, ws, gate: dict, answer: str, actor: str) -> None:
    if actor != "human":
        raise gates.GateError("design-lock gates need a human answer, in every mode")
    ws = Path(ws)
    pending = _read_pending(ws) or {}
    names_gate = pending.get("gate") == gate["id"]
    if answer == "reject":
        if names_gate:
            (ws / PENDING).unlink(missing_ok=True)
        return
    if answer != "approve":
        raise gates.GateError(f"answer must be one of: {', '.join(OPTIONS)}")
    if _recorded(ws, gate["id"]):
        # Idempotent completion: the record is already in the ledger (an interrupted approval).
        if names_gate:
            (ws / PENDING).unlink(missing_ok=True)
        return
    ledger = read_ledger(ws)
    if ledger.problem:
        raise gates.GateError(f"the lock ledger does not verify ({ledger.problem}); nothing was locked")
    if not _tree_is_plain(ws):
        raise gates.GateError(f"{model.DESIGN_DIR}/ or {LOCKED} is a symlink or not a directory; nothing was locked")
    if not names_gate:
        raise gates.GateError(f"no pending design lock for gate {gate['id']}; reject this gate and "
                              "run `scieflow design lock` or `scieflow design amend` again")
    got = model.load(ws)
    if not isinstance(got, dict):
        raise gates.GateError(CHANGED)
    report = _report(project, ws, got)
    digest = _current_hash(ws, got)
    if (report.errors or digest is None or digest != pending.get("content_hash")
            or digest[:12] not in gate["question"]):
        raise gates.GateError(CHANGED)
    kind = pending.get("kind")
    if kind not in ("lock", "amendment"):
        raise gates.GateError("the pending record is neither a lock nor an amendment")
    records = list(ledger.records)
    if kind == "lock" and records:
        raise gates.GateError("the design is already locked")
    if kind == "amendment" and not records:
        raise gates.GateError("the design is not locked yet; an amendment needs a lock")
    seq = len(records) + 1
    previous = records[-1] if records else None
    copy = ws / LOCKED / f"v{seq}"
    if copy.is_symlink() or (copy.exists() and not copy.is_dir()):
        raise gates.GateError(f"{LOCKED}/v{seq} is not a plain folder; refusing to write the locked copy")
    # Stage the copy in a fresh folder and swap it in, so no file from an earlier attempt survives.
    locked_dir = ws / LOCKED
    locked_dir.mkdir(parents=True, exist_ok=True)
    staging = locked_dir / f".v{seq}.staging"
    if staging.is_symlink() or staging.is_file():
        staging.unlink()
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        (staging / model.DESIGN_FILE).write_bytes((ws / model.DESIGN_DIR / model.DESIGN_FILE).read_bytes())
        power = model.power_document(ws, got)
        if power is not None:
            (staging / "power.json").write_bytes((ws / got["sample_size"]["power_file"]).read_bytes())
        if copy.exists():
            shutil.rmtree(copy)
        os.replace(staging, copy)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    record = {
        "seq": seq, "kind": kind, "version": f"{seq}.0", "content_hash": digest,
        "previous_hash": previous["content_hash"] if previous else "",
        "previous_version": previous["version"] if previous else "",
        "summary": pending.get("summary", "") if kind == "amendment" else "",
        "rationale": pending.get("rationale", "") if kind == "amendment" else "",
        "gate": gate["id"], "approved_by": "human", "approved_at": _now(),
        "charter_version": _charter_version(ws), "copy": f"{LOCKED}/v{seq}",
    }
    store.append_jsonl(ws / LEDGER, record)
    (ws / PENDING).unlink(missing_ok=True)


def _charter_version(ws: Path):
    try:
        return charter.read(ws)["current"]
    except charter.CharterError:
        return None


# --- state (read-only) ---------------------------------------------------------------------------

def state(project, ws) -> State:
    """The design's lock state (plan 5.5): none, invalid, draft, pending, locked, modified or
    ledger-invalid. Read-only: it never removes a stale pending record."""
    ws = Path(ws)
    ledger = read_ledger(ws)
    got = model.load(ws)
    records = list(ledger.records)
    last = records[-1] if records else None
    out = State(status="none", version=last["version"] if last else None,
                in_force_hash=last["content_hash"] if last else None,
                pending_gate=_open_pending_id(ws), problem=ledger.problem,
                trail=[_trail_entry(r) for r in records])
    if isinstance(got, dict):
        out.report = _report(project, ws, got)
        out.study_type = got.get("study_type")
        out.sap_metrics = model.sap_metrics(got)
        out.design_hash = _hash(got)
        out.current_hash = _current_hash(ws, got)
    if ledger.problem:
        out.status = "ledger-invalid"
    elif last is None:
        if isinstance(got, Invalid) or (isinstance(got, dict) and out.current_hash is None):
            out.status = "invalid"
        elif isinstance(got, dict):
            out.status = "pending" if out.pending_gate else "draft"
        else:
            out.status = "none"
    else:
        out.status = "locked" if out.current_hash == last["content_hash"] else "modified"
    return out


# --- campaign verdict (plan 5.6) -----------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """Whether a campaign may run under the run's design. `warnings` are advice (computational
    designs only); a refusal names its rule and stops the campaign before anything runs."""

    refused: bool = False
    rule: str | None = None
    message: str = ""
    warnings: tuple = ()


def _refused(rule: str, message: str) -> Verdict:
    return Verdict(refused=True, rule=rule, message=message)


def campaign_verdict(project, ws, metrics) -> Verdict:
    """The rules of plan 5.6, first match wins. A run with no `design/` directory is allowed unless
    a human-approved design-lock gate exists that no ledger record names: a deleted `design/` after
    a lock must not reopen the guard (M-D1 t5 review r1, finding 1). Computational designs only
    warn, except for the integrity rules (`design-ledger-invalid`, `design-modified`, M-D1-D6). A
    computational study type is read from the current file; when that file is unreadable the state
    is already a refusal, so the in-force copy would not change the outcome."""
    ws = Path(ws)
    if not os.path.lexists(ws / "design"):
        problem = _unrecorded_problem(ws, [])
        if problem is None:
            return Verdict()
        return _refused("design-ledger-invalid", f"the design ledger does not verify ({problem}); "
                        "campaigns are refused until a human repairs it")
    st = state(project, ws)
    if st.status == "none":
        return Verdict()
    if st.status == "ledger-invalid":
        return _refused("design-ledger-invalid", f"the design ledger does not verify ({st.problem}); "
                        "campaigns are refused until a human repairs it")
    if st.status == "modified":
        return _refused("design-modified", f"design.yml differs from its lock (version {st.version}); "
                        "amend it with `scieflow design amend` or restore it")
    if st.status == "invalid":
        return _refused("design-invalid", "design/design.yml cannot be read as a design; "
                        "run `scieflow design check` to see why")
    computational = st.study_type == "computational"
    if st.status in ("draft", "pending"):
        if st.report is not None and st.report.errors:
            rule = "design-invalid"
            message = f"design.yml has {len(st.report.errors)} error(s); run `scieflow design check`"
        else:
            rule = "design-unlocked"
            message = "the design is not locked yet; lock it first: `scieflow design lock <slug>`"
        return _advice_or_refusal(rule, message, computational)
    missing = [m for m in metrics if m not in st.sap_metrics]
    if missing:
        message = (f"campaign metric(s) not named by any SAP outcome: {', '.join(missing)}; "
                   "add them to the SAP with `scieflow design amend`")
        return _advice_or_refusal("out-of-sap", message, computational)
    return Verdict()


def _advice_or_refusal(rule: str, message: str, computational: bool) -> Verdict:
    if computational:
        return Verdict(warnings=(model.Problem(rule, "design", message),))
    return _refused(rule, message)
