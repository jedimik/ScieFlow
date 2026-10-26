"""De-identification of a tabular export (M-B Part A 5.9): `scieflow data ingest`.

Tables only (M-B-D11). Identifier columns and `rc` are dropped, free text is dropped unless asked
for, date columns become whole days since an anchor column, and the record id becomes a keyed
pseudonym. The key lives in the user's home folder, outside every run (M-B-D12). The output is
declared L2: it is still record-level data. Messages name paths and columns, never a value.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from scieflow import __version__
from scieflow.core import events
from scieflow.core.data import levels, policy, scan
from scieflow.core.data.release import RELEASED_DIR
from scieflow.core.data.scan import IDENTIFIER_COLUMNS, normalise

SALTS_OVERRIDE: Path | None = None   # test seam, like sandbox.CREDENTIALS_OVERRIDE
SALT_BYTES = 32
PROFILES = ("generic", "redcap")
DROP_ALWAYS = frozenset({"rc", "rodne_cislo"})
REDCAP_SURVEY_ID = "redcap_survey_identifier"
REDCAP_TIMESTAMP_SUFFIX = "_timestamp"
DATE_SHARE = 0.9
HEAD_BYTES = 4096
MANIFEST_SUFFIX = ".ingest.yml"
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
CZ_DATE = re.compile(r"(\d{1,2})\. ?(\d{1,2})\. ?(\d{4})")


class IngestError(Exception):
    """An ingest the rules refuse. The message names a path, column or rule, never a value."""


@dataclass
class _Plan:
    id_idx: int
    anchor_idx: int | None
    dropped: dict = field(default_factory=dict)   # column index -> rule id
    date_cols: list = field(default_factory=list)
    kept: list = field(default_factory=list)


def parse_date(text: str) -> date | None:
    """ISO `YYYY-MM-DD[ HH:MM[:SS]]` or Czech `D. M. YYYY`; anything else is None."""
    value = text.strip()
    m = ISO_DATE.fullmatch(value)
    if m:
        year, month, day = (int(x) for x in m.groups())
    else:
        m = CZ_DATE.fullmatch(value)
        if not m:
            return None
        day, month, year = (int(x) for x in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def salt_path(project_name: str) -> Path:
    base = Path(SALTS_OVERRIDE) if SALTS_OVERRIDE is not None \
        else Path.home() / ".config" / "scieflow" / "salts"
    return base / f"{project_name}.salt"


def load_or_create_salt(project_name: str) -> bytes:
    """The pseudonym key: 32 random bytes, mode 0600, created on first use (M-B-D12)."""
    path = salt_path(project_name)
    base = path.parent
    if base.is_symlink():
        raise IngestError("the salts folder is a symbolic link; the key is not used")
    if not base.exists():
        base.mkdir(parents=True, mode=0o700)
        os.chmod(base, 0o700)
    if path.exists() or path.is_symlink():
        st = os.lstat(path)
        if path.is_symlink() or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
            raise IngestError("the pseudonym key file is not a private regular file (mode 0600)")
        key = path.read_bytes()
        if len(key) != SALT_BYTES:
            raise IngestError("the pseudonym key file has the wrong size")
        return key
    key = secrets.token_bytes(SALT_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    os.chmod(path, 0o600)
    return key


def _pseudonym(value: str, key: bytes) -> str:
    if not value:
        return ""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _target(ws: Path, text) -> tuple[str, Path]:
    raw = Path(str(text))
    if raw.is_absolute():
        try:
            rel = raw.relative_to(ws).as_posix()
        except ValueError:
            raise IngestError(f"--out {raw.name} is outside the run") from None
    else:
        rel = str(text)
    problem = levels.path_problem(rel)
    if problem:
        raise IngestError(f"--out {problem}")
    parts = PurePosixPath(rel).parts
    if parts[0] == RELEASED_DIR:
        raise IngestError("--out must not be under released/")
    cur = ws
    for part in parts[:-1]:
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
            raise IngestError(f"--out {rel}: a folder on the way is not a plain folder")
    target = ws / rel
    manifest = target.with_name(target.name + MANIFEST_SUFFIX)
    if any(p.exists() or p.is_symlink() for p in (target, manifest)):
        raise IngestError(f"--out {rel} already exists")
    return rel, target


def _rows(source: Path, delim: str):
    with open(source, newline="", encoding="utf-8-sig", errors="replace") as fh:
        yield from csv.reader(fh, delimiter=delim)


def _column_stats(source: Path, delim: str):
    rows = _rows(source, delim)
    header = next(rows, None)
    if header is None:
        raise IngestError("the source is empty")
    stats = [{"values": 0, "length": 0, "wordy": 0, "dated": 0} for _ in header]
    rows_in = 0
    for row in rows:
        rows_in += 1
        for i, cell in enumerate(row[:len(header)]):
            v = cell.strip()
            if not v:
                continue
            s = stats[i]
            s["values"] += 1
            s["length"] += len(v)
            s["wordy"] += len(v.split()) >= scan.FREE_TEXT_WORDS
            s["dated"] += parse_date(v) is not None
    return header, rows_in, stats


def _plan(header, stats, pol, profile, id_col, date_anchor, drops, keep_free_text) -> _Plan:
    names = [normalise(h) for h in header]
    wanted_id = normalise(id_col or "record_id")
    if wanted_id not in names:
        raise IngestError(f"the id column {id_col or 'record_id'} is not in the header "
                          "(choose it with --id-col)")
    id_idx = names.index(wanted_id)
    anchor_idx = None
    if date_anchor:
        want = normalise(date_anchor)
        if want not in names:
            raise IngestError(f"the --date-anchor column {date_anchor} is not in the header")
        anchor_idx = names.index(want)
    extra = {normalise(x) for x in pol.extra_drop}
    requested = {normalise(x) for x in drops}
    missing = sorted(r for r in requested if r not in names)
    if missing:
        raise IngestError(f"--drop column(s) not in the header: {', '.join(missing)}")
    if (names[id_idx] in IDENTIFIER_COLUMNS or names[id_idx] in DROP_ALWAYS
            or names[id_idx] in extra):
        raise IngestError(f"the id column {header[id_idx]} is also an identifier column; "
                          "choose another --id-col")
    plan = _Plan(id_idx=id_idx, anchor_idx=anchor_idx)
    for i, nm in enumerate(names):
        if i == id_idx:
            continue
        if nm in DROP_ALWAYS or nm in IDENTIFIER_COLUMNS or nm in extra or (
                profile == "redcap" and nm == REDCAP_SURVEY_ID):
            plan.dropped[i] = "identifier-column"
        elif nm in requested:
            plan.dropped[i] = "requested-drop"
        elif i == anchor_idx:
            plan.dropped[i] = "date-anchor"
    if not keep_free_text:
        for i, s in enumerate(stats):
            if i in plan.dropped or i == id_idx or not s["values"]:
                continue
            if s["values"] >= scan.FREE_TEXT_MIN_VALUES and (
                    s["length"] / s["values"] >= scan.FREE_TEXT_MEAN_LENGTH
                    or s["wordy"] / s["values"] >= scan.FREE_TEXT_WORD_SHARE):
                plan.dropped[i] = "free-text-column"
    for i, nm in enumerate(names):
        if i in plan.dropped or i == id_idx:
            continue
        s = stats[i]
        timestamp = profile == "redcap" and nm.endswith(REDCAP_TIMESTAMP_SUFFIX)
        if timestamp or (s["values"] and s["dated"] / s["values"] >= DATE_SHARE):
            plan.date_cols.append(i)
    if plan.date_cols and anchor_idx is None:
        raise IngestError("date columns need --date-anchor COLUMN: "
                          + ", ".join(header[i] for i in plan.date_cols))
    plan.kept = [i for i in range(len(header)) if i not in plan.dropped]
    return plan


def ingest(project, ws, source, out, *, profile="generic", id_col="record_id", date_anchor=None,
           drops=(), keep_free_text=False) -> dict:
    """De-identify SOURCE into the run at OUT (new file) and declare it L2 (5.9)."""
    ws, source = Path(ws), Path(source)
    if profile not in PROFILES:
        raise IngestError(f"unknown profile {profile!r}")
    try:
        st = os.lstat(source)
    except OSError:
        raise IngestError("the source cannot be read") from None
    if not stat.S_ISREG(st.st_mode):
        raise IngestError("the source must be a regular file")
    pol = policy.load(project.root)
    got = levels.read(ws)
    if isinstance(got, levels.Invalid):
        raise IngestError(got.reason)
    rel, target = _target(ws, out)
    with open(source, "rb") as fh:
        delim = scan._table_dialect(source.name.lower(),
                                    fh.read(HEAD_BYTES).decode("utf-8-sig", errors="replace"))
    try:
        header, rows_in, stats = _column_stats(source, delim)
        plan = _plan(header, stats, pol, profile, id_col, date_anchor, drops, keep_free_text)
        key = load_or_create_salt(project.root.name)
        salt_id = hashlib.sha256(key).hexdigest()[:8]
        rows_out = unparsed = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name("." + target.name + ".part")
        try:
            with open(part, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([header[i] for i in plan.kept])
                rows = _rows(source, delim)
                next(rows)
                for row in rows:
                    row = list(row[:len(header)]) + [""] * (len(header) - len(row))
                    anchor = (parse_date(row[plan.anchor_idx])
                              if plan.anchor_idx is not None else None)
                    out_row = []
                    for i in plan.kept:
                        value = row[i]
                        if i == plan.id_idx:
                            out_row.append(_pseudonym(value.strip(), key))
                        elif i in plan.date_cols:
                            d = parse_date(value) if value.strip() else None
                            if value.strip() and (d is None or anchor is None):
                                unparsed += 1
                            out_row.append("" if d is None or anchor is None
                                           else str((d - anchor).days))
                        else:
                            out_row.append(value)
                    writer.writerow(out_row)
                    rows_out += 1
            os.replace(part, target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    except csv.Error:
        raise IngestError("the source is not a parseable table") from None
    manifest = {
        "source_name": source.name, "source_sha256": _sha(source),
        "source_mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
            timespec="seconds"),
        "scieflow_version": __version__, "rows_in": rows_in, "rows_out": rows_out,
        "columns_kept": [header[i] for i in plan.kept],
        "columns_dropped": [{"name": header[i], "rule": rule}
                            for i, rule in sorted(plan.dropped.items())],
        "dates_converted": [header[i] for i in plan.date_cols],
        "dates_unparsed": unparsed, "id_column": header[plan.id_idx], "salt_id": salt_id,
    }
    target.with_name(target.name + MANIFEST_SUFFIX).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    entry = {"path": rel, "level": "L2", "kind": "tabular", "reason": "ingest output",
             "declared_by": "human", "declared_at": _now()}
    try:
        levels.modify(ws, lambda df: df.levels.append(entry))
    except levels.DataFileError as exc:
        raise IngestError(str(exc)) from exc
    events.emit(ws, "data.ingested", "human", path=rel, rows_in=rows_in, rows_out=rows_out,
                columns_kept=len(plan.kept), columns_dropped=len(plan.dropped),
                dates_converted=len(plan.date_cols), dates_unparsed=unparsed)
    return {**manifest, "out": rel, "manifest": rel + MANIFEST_SUFFIX}
