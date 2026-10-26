"""Release and import (M-B Part A 5.8): the only way a result leaves an L2 run.

`request` checks one run file and opens a human-only `data-release` gate, with a pending
entry in data.yml. `on_answer` is the gate's answer hook: gates.answer calls it before the
gate is marked answered, and only a human `release` copies the file (re-checked) into
released/<id>/ and records it; `reject` drops the pending entry. `import_release` copies a
released file into another run, where it is L1.5. Messages name paths, checks and columns
by position, never a value read from a data file (M-B-D9).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from scieflow.core import events, gates, store
from scieflow.core.data import levels, policy, scan
from scieflow.core.data.scan import IDENTIFIER_COLUMNS, normalise

GATE_KIND = "data-release"
RELEASED_DIR = "released"
ANSWERS = ("release", "reject")
IMAGE_EXTENSIONS = (".png", ".svg", ".pdf", ".jpg", ".jpeg")
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RECORD_IDS = frozenset({"record_id", "id", "subject", "subject_id", "patient", "patient_id",
                        "pid", "participant", "participant_id"})
COUNT_NAMES = frozenset({"n", "count", "freq"})
PATH_VALUE = re.compile(r"~/\S*|/[^/\s]+/\S*|[A-Za-z]:\\\S*")
HEADER_RULES = frozenset({"dicom-header", "eeg-header", "binary-tabular", "imaging-file",
                          "unreadable"})
HEADER_CHECKS = tuple(sorted(HEADER_RULES))
TABLE_CHECKS = ("counts-unknown", "small-cell", "record-level", "identifier-column",
                "identifier-values", "free-text-column", "path-values")
HEAD_BYTES = 4096


class ReleaseError(Exception):
    """A release or import the rules refuse. `report` holds the checks when there are any."""

    def __init__(self, message: str, report: Report | None = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class Problem:
    check: str
    detail: str


@dataclass(frozen=True)
class Report:
    problems: tuple = ()
    notes: tuple = ()
    applied: tuple = ()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _locate(ws: Path, rel: str) -> Path:
    """The regular file at a run-relative path. Every component is checked, none is followed."""
    problem = levels.path_problem(rel)
    if problem:
        raise ReleaseError(f"path {rel!r} {problem}")
    cur = Path(ws)
    for part in PurePosixPath(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ReleaseError(f"{rel}: a symbolic link is not followed")
    if not _regular(cur):
        raise ReleaseError(f"{rel}: not a regular file in the run")
    return cur


def _artifact(ws: Path, text) -> tuple[str, Path]:
    raw = Path(str(text))
    if raw.is_absolute():
        try:
            rel = raw.relative_to(ws).as_posix()
        except ValueError:
            raise ReleaseError(f"{raw.name}: the artifact is outside the run") from None
    else:
        rel = str(text)
    if PurePosixPath(rel).parts[:1] == (RELEASED_DIR,):
        raise ReleaseError(f"{rel}: a released file is not a source")
    return rel, _locate(Path(ws), rel)


def _report(problems, notes, applied) -> Report:
    ordered = tuple(sorted(set(problems), key=lambda p: (p.check, p.detail)))
    return Report(problems=ordered, notes=tuple(notes), applied=tuple(applied))


def _header_problems(path: Path, extra: frozenset) -> list[Problem]:
    found = scan._inspect(path.name, path, extra, HEAD_BYTES)
    return [Problem(f.rule, f.detail) for f in found if f.rule in HEADER_RULES]


def _count_columns(names: list, counts, no_counts: bool):
    """(indexes of count columns, problems). A count column is named, or recognised (5.8)."""
    if no_counts:
        return set(), []
    if counts:
        wanted = [normalise(c) for c in counts]
        found = {i for i, nm in enumerate(names) if nm in wanted}
        missing = [w for w in wanted if w not in names]
        return found, [Problem("counts-unknown", f"column {w} is not in the header")
                       for w in missing]
    found = {i for i, nm in enumerate(names)
             if nm in COUNT_NAMES or nm.startswith("n_") or nm.endswith(("_n", "_count"))}
    if not found:
        return set(), [Problem("counts-unknown",
                               "no count column: name one with --counts or pass --no-counts")]
    return found, []


def _table(path: Path, pol, counts, no_counts: bool) -> list[Problem]:
    """Every row of a CSV/TSV (not a sample): identifiers, paths, small cells, free text."""
    with open(path, "rb") as fh:
        sample = fh.read(HEAD_BYTES).decode("utf-8-sig", errors="replace")
    delim = scan._table_dialect(path.name.lower(), sample)
    extra = frozenset(normalise(x) for x in pol.extra_drop)
    flagged: set = set()
    stats: dict = {}
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rows = csv.reader(fh, delimiter=delim)
            header = next(rows, None)
            if header is None:
                return []
            names = [normalise(h) for h in header]
            count_cols, out = _count_columns(names, counts, no_counts)
            for i, nm in enumerate(names):
                if nm in RECORD_IDS or nm.endswith("_id"):
                    out.append(Problem("record-level", f"column {i + 1}"))
                if nm in IDENTIFIER_COLUMNS or nm in extra:
                    out.append(Problem("identifier-column", nm))

            def cells(row, is_data: bool) -> None:
                for i, cell in enumerate(row):
                    v = cell.strip()
                    if not v:
                        continue
                    if (scan._BIRTH_NUMBER.fullmatch(v) or scan._EMAIL.fullmatch(v)
                            or scan._PHONE.fullmatch(v)):
                        flagged.add(("identifier-values", i))
                    if PATH_VALUE.fullmatch(v):
                        flagged.add(("path-values", i))
                    if not is_data:
                        continue
                    stat_ = stats.setdefault(i, [0, 0, 0])
                    stat_[0] += 1
                    stat_[1] += len(v)
                    stat_[2] += len(v.split()) >= scan.FREE_TEXT_WORDS
                    if i in count_cols:
                        try:
                            value = int(v)
                        except ValueError:
                            continue
                        if 0 < value < pol.min_cell:
                            flagged.add(("small-cell", i))

            cells(header, False)
            for row in rows:
                cells(row, True)
    except csv.Error:
        return [Problem("unreadable", "table not parseable")]
    for i, (n, total, wordy) in stats.items():
        if n >= scan.FREE_TEXT_MIN_VALUES and (total / n >= scan.FREE_TEXT_MEAN_LENGTH
                                               or wordy / n >= scan.FREE_TEXT_WORD_SHARE):
            flagged.add(("free-text-column", i))
    out.extend(Problem(check, f"column {i + 1}") for check, i in flagged)
    return out


def _json(path: Path) -> list[Problem]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return [Problem("unreadable", "json not parseable")]
    strings: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)

    walk(doc)
    out: list[Problem] = []
    for index, text in enumerate(strings, 1):
        v = text.strip()
        if PATH_VALUE.fullmatch(v):
            out.append(Problem("path-values", f"string {index}"))
        if scan._BIRTH_NUMBER.fullmatch(v) or scan._EMAIL.fullmatch(v) or scan._PHONE.fullmatch(v):
            out.append(Problem("identifier-values", f"string {index}"))
    return out


def _text(path: Path) -> list[Problem]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [Problem("not-inspectable", "binary content of an unlisted type")]
    except OSError:
        return [Problem("unreadable", "cannot be read")]
    out: list[Problem] = []
    for number, line in enumerate(text.splitlines(), 1):
        for token in line.split():
            t = token.strip(".,;:()[]{}\"'")
            if PATH_VALUE.fullmatch(t):
                out.append(Problem("path-values", f"line {number}"))
            if scan._BIRTH_NUMBER.fullmatch(t) or scan._EMAIL.fullmatch(t):
                out.append(Problem("identifier-values", f"line {number}"))
    return out


def check_file(path, pol, counts=(), no_counts=False) -> Report:
    """Every release check of 5.8 that applies to this file. Refuses nothing by itself."""
    path = Path(path)
    if not _regular(path):
        return Report(problems=(Problem("unreadable", "not a regular file"),),
                      applied=HEADER_CHECKS)
    name = path.name.lower()
    extra = frozenset(normalise(x) for x in pol.extra_drop)
    header = _header_problems(path, extra)
    if name.endswith(IMAGE_EXTENSIONS):
        return _report(header, [Problem("not-inspectable", "human review only")],
                       HEADER_CHECKS + ("not-inspectable",))
    if header:
        return _report(header, (), HEADER_CHECKS)
    if name.endswith(scan.TABLE_EXTENSIONS):
        return _report(_table(path, pol, counts, no_counts), (), TABLE_CHECKS + HEADER_CHECKS)
    if name.endswith(".json"):
        return _report(_json(path), (), ("identifier-values", "path-values", "unreadable")
                       + HEADER_CHECKS)
    return _report(_text(path), (), ("identifier-values", "path-values", "not-inspectable",
                                     "unreadable") + HEADER_CHECKS)


def _refusal(rel: str, report: Report) -> str:
    shown = "; ".join(f"{p.check} ({p.detail})" for p in report.problems[:10])
    more = f" and {len(report.problems) - 10} more" if len(report.problems) > 10 else ""
    return f"{rel} fails {len(report.problems)} check(s): {shown}{more}"


def _question(slug: str, rel: str, digest: str, report: Report) -> str:
    notes = ", ".join(sorted({n.check for n in report.notes})) or "none"
    return (f"Release {rel} from run {slug} to the released folder? sha256 {digest}. "
            f"Checks run: {', '.join(report.applied)}. Notes: {notes}. A human confirms the file "
            "holds only aggregates that may leave the run; the copy is checked again before it "
            "is written.")


def _modify(ws: Path, change, error) -> None:
    try:
        levels.modify(ws, change)
    except levels.DataFileError as exc:
        raise error(str(exc)) from exc


def request(project, ws, artifact, *, rid=None, counts=(), no_counts=False, reason="") -> dict:
    """Check a run file, open the human-only data-release gate and record the pending entry."""
    ws = Path(ws)
    pol = policy.load(project.root)
    rel, path = _artifact(ws, artifact)
    rid = rid or store.new_id()
    if not ID_PATTERN.fullmatch(rid):
        raise ReleaseError("a release id is letters, digits, '.', '_' or '-' (at most 64 characters)")
    got = levels.read(ws)
    if isinstance(got, levels.Invalid):
        raise ReleaseError(got.reason)
    taken = {e.get("id") for e in [*got.pending_releases, *got.released]}
    if rid in taken or (ws / RELEASED_DIR / rid).exists():
        raise ReleaseError(f"release id {rid} is already used in run {ws.name}")
    report = check_file(path, pol, tuple(counts), no_counts)
    if report.problems:
        raise ReleaseError(_refusal(rel, report), report=report)
    digest = _sha(path)
    gate = gates.open_gate(project, ws, GATE_KIND, _question(ws.name, rel, digest, report),
                           options=list(ANSWERS), files=[rel], in_scope=False, actor="agent")
    entry = {"id": rid, "source": rel, "sha256": digest, "checks": list(report.applied),
             "gate": gate["id"], "requested_at": _now(), "counts": list(counts),
             "no_counts": bool(no_counts), "reason": str(reason).strip()}
    _modify(ws, lambda df: df.pending_releases.append(entry), ReleaseError)
    return gate


def _settle(df, gate_id: str, record=None) -> None:
    if not any(e.get("gate") == gate_id for e in df.pending_releases):
        raise levels.DataFileError(f"no pending release for gate {gate_id}")
    df.pending_releases = [e for e in df.pending_releases if e.get("gate") != gate_id]
    if record is not None:
        df.released.append(record)


def _release(project, ws: Path, entry: dict, gate: dict) -> None:
    try:
        pol = policy.load(project.root)
        rel, src = _artifact(ws, entry["source"])
    except (policy.PolicyInvalid, ReleaseError) as exc:
        raise gates.GateError(str(exc)) from exc
    if _sha(src) != entry["sha256"]:
        raise gates.GateError(f"{rel} changed since the gate was opened; the answer is refused "
                              "and the gate stays open")
    if (ws / RELEASED_DIR).is_symlink():
        raise gates.GateError("released/ is a symbolic link; the answer is refused")
    folder = ws / RELEASED_DIR / entry["id"]
    if folder.exists() or folder.is_symlink():
        raise gates.GateError(f"released/{entry['id']} already exists")
    folder.mkdir(parents=True)
    copy = folder / src.name
    try:
        shutil.copyfile(src, copy)
        if _sha(copy) != entry["sha256"]:
            raise gates.GateError(f"{rel} changed while it was copied; the gate stays open")
        report = check_file(copy, pol, tuple(entry.get("counts") or ()),
                            bool(entry.get("no_counts")))
        if report.problems:
            raise gates.GateError(f"the copy of {_refusal(rel, report)}; the gate stays open")
        record = {"id": entry["id"], "file": f"{RELEASED_DIR}/{entry['id']}/{src.name}",
                  "sha256": entry["sha256"], "source": rel, "gate": gate["id"],
                  "approved_by": "human", "approved_at": _now(),
                  "checks": list(entry.get("checks") or [])}
        _modify(ws, lambda df: _settle(df, gate["id"], record), gates.GateError)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def on_answer(project, ws, gate, answer, actor) -> None:
    """The data-release answer hook (gates.answer calls it before the gate is marked answered)."""
    if actor != "human":
        raise gates.GateError("data-release gates need a human answer, in every mode")
    ws = Path(ws)
    got = levels.read(ws)
    if isinstance(got, levels.Invalid):
        raise gates.GateError(got.reason)
    entry = next((e for e in got.pending_releases if e.get("gate") == gate["id"]), None)
    if entry is None:
        raise gates.GateError(f"no pending release for gate {gate['id']}")
    if answer == "reject":
        _modify(ws, lambda df: _settle(df, gate["id"]), gates.GateError)
        events.emit(ws, "data.released", "human", id=entry["id"], sha256=entry["sha256"],
                    outcome="rejected")
        return
    if answer != "release":
        raise gates.GateError(f"answer must be one of: {', '.join(ANSWERS)}")
    _release(project, ws, entry, gate)
    events.emit(ws, "data.released", "human", id=entry["id"], sha256=entry["sha256"],
                outcome="released")


def _verify(ws: Path, rec: dict) -> dict:
    file = str(rec.get("file", ""))
    try:
        ok = _sha(_locate(ws, file)) == rec.get("sha256")
        reason = "" if ok else "sha256 differs from the record"
    except ReleaseError as exc:
        ok, reason = False, str(exc)
    return {"id": rec.get("id"), "file": file, "ok": ok, "reason": reason}


def import_release(project, dest_ws, src_ws, release_id) -> dict:
    """Copy a released file into DEST as an L1.5 import (5.8). Nothing is copied on a mismatch."""
    dest_ws, src_ws = Path(dest_ws), Path(src_ws)
    if dest_ws.resolve() == src_ws.resolve():
        raise ReleaseError("a run cannot import its own release")
    got = levels.read(src_ws)
    if isinstance(got, levels.Invalid):
        raise ReleaseError(f"run {src_ws.name} data.yml is invalid: {got.reason}")
    rec = next((r for r in got.released if r.get("id") == release_id), None)
    if rec is None:
        raise ReleaseError(f"no released record {release_id} in run {src_ws.name}")
    src_file = _locate(src_ws, str(rec["file"]))
    if _sha(src_file) != rec["sha256"]:
        raise ReleaseError(f"released file {rec['file']} does not match its recorded sha256; "
                           "nothing was imported")
    dest_ws_inputs = dest_ws / "inputs"
    if dest_ws_inputs.is_symlink() or (dest_ws_inputs / RELEASED_DIR).is_symlink():
        raise ReleaseError("inputs/ is a symbolic link; the import is refused")
    folder_name = f"{src_ws.name}-{release_id}"
    folder = dest_ws_inputs / RELEASED_DIR / folder_name
    if folder.exists() or folder.is_symlink():
        raise ReleaseError(f"release {release_id} is already imported into run {dest_ws.name}")
    folder.mkdir(parents=True)
    copy = folder / src_file.name
    try:
        shutil.copyfile(src_file, copy)
        if _sha(copy) != rec["sha256"]:
            raise ReleaseError("the copy does not match the released file; nothing was imported")
        imp = {"id": store.new_id(),
               "file": f"inputs/{RELEASED_DIR}/{folder_name}/{src_file.name}",
               "sha256": rec["sha256"], "from": src_ws.name, "release": release_id,
               "imported_at": _now()}
        _modify(dest_ws, lambda df: df.imports.append(imp), ReleaseError)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    events.emit(dest_ws, "data.imported", "human", import_id=imp["id"], release=release_id,
                sha256=imp["sha256"], source_run=src_ws.name)
    return imp


def audit(ws) -> dict:
    """Declarations, pending, released and imported files, each re-hashed (5.10)."""
    ws = Path(ws)
    got = levels.read(ws)
    refused = sum(1 for e in events.read(ws, types=("job.refused",))
                  if (e.get("data") or {}).get("reason") == "data-level")
    warnings = len(events.read(ws, types=("data.warning",)))
    if isinstance(got, levels.Invalid):
        return {"invalid": got.reason, "declarations": [], "pending": [], "released": [],
                "imports": [], "refused": refused, "warnings": warnings, "mismatches": 1}
    released = [_verify(ws, r) for r in got.released]
    imports = [_verify(ws, r) for r in got.imports]
    return {"invalid": None, "declarations": list(got.levels),
            "pending": [e.get("id") for e in got.pending_releases], "released": released,
            "imports": imports, "refused": refused, "warnings": warnings,
            "mismatches": sum(not row["ok"] for row in released + imports)}
