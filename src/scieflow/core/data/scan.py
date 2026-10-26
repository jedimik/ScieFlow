"""Scan a run for undeclared data that looks like patient data (M-B Part A 5.6, D10).

Warnings only. A finding names a run-relative path, a rule id and a detail (a column name or a
byte offset). Nothing a finding, an event or a cache file holds is a value read from a data file:
the scan keeps values in memory, compares them, and drops them (M-B-D9).
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

RULES = ("redcap-export", "identifier-column", "identifier-values", "free-text-column",
         "dicom-header", "imaging-file", "eeg-header", "binary-tabular", "unreadable")

# Normalised names (see normalise). English and Czech (5.6).
IDENTIFIER_COLUMNS = frozenset({
    "name", "first", "last", "surname", "jmeno", "prijmeni", "rc", "rodne_cislo",
    "birth_number", "dob", "date_of_birth", "birth_date", "datum_narozeni", "address", "adresa",
    "ulice", "zip", "postcode", "postal_code", "psc", "phone", "telefon", "tel", "mobile",
    "mobil", "email", "e_mail", "mail", "insurance_number", "cislo_pojistence", "mrn",
    "medical_record_number", "ssn", "national_id", "passport", "ip_address",
})
REDCAP_KEY = "record_id"
REDCAP_MARKERS = ("redcap_event_name", "redcap_repeat_instrument")
TABLE_EXTENSIONS = (".csv", ".tsv")
BINARY_TABULAR = (".xlsx", ".xls", ".sav", ".dta", ".sas7bdat", ".rds", ".feather", ".parquet")
IMAGING = (".nii", ".nii.gz", ".mgz", ".img", ".hdr")
EEG = (".edf", ".bdf", ".rec")
DICOM_OFFSET = 128
EDF_PATIENT = (8, 88)
ROWS_SCANNED = 200
FREE_TEXT_MIN_VALUES = 20
FREE_TEXT_MEAN_LENGTH = 40
FREE_TEXT_WORDS = 5
FREE_TEXT_WORD_SHARE = 0.3

_BIRTH_NUMBER = re.compile(r"\d{6}/?\d{3,4}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+\d{1,3}[ \d]{8,14}")

# Directories and files never scanned (5.6). Top-level names are relative to the run.
_SKIP_TOP_DIRS = ("logs", "gates", "scite")
_SKIP_ANY_DIRS = (".uv-cache", ".git", "provenance.git")
_SKIP_TOP_FILES = ("events.jsonl",)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    detail: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple
    files_seen: int
    truncated: bool


def normalise(name: str) -> str:
    """NFKD, accents stripped, lower case, non-alphanumerics to '_' (5.6)."""
    decomposed = unicodedata.normalize("NFKD", str(name))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def _skip_dir(rel: Path) -> bool:
    if rel.parts and rel.parts[0] in _SKIP_TOP_DIRS:
        return True
    if rel.parts[:2] == ("scratch", "agent-home"):
        return True
    return any(part in _SKIP_ANY_DIRS for part in rel.parts)


def _candidates(ws: Path) -> list:
    """Run-relative file paths to inspect, sorted. Symbolic links are listed, never followed."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ws, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(ws)
        keep = []
        for name in sorted(dirnames):
            rel = rel_dir / name
            if _skip_dir(rel):
                continue
            if (here / name).is_symlink():
                found.append((rel).as_posix())   # reported as unreadable, not followed
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            rel = rel_dir / name
            if rel_dir == Path(".") and name in _SKIP_TOP_FILES:
                continue
            if _skip_dir(rel_dir):
                continue
            found.append(rel.as_posix())
    return sorted(found)


def _read_bytes(path: Path, limit: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit)


def _table_dialect(name: str, text: str) -> str:
    """Tab for .tsv; otherwise the delimiter a sample suggests (Czech exports use ';')."""
    if name.endswith(".tsv"):
        return "\t"
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def _table(rel: str, head: bytes, cut: bool, extra_drop: frozenset) -> list:
    text = head.decode("utf-8-sig", errors="replace")
    delim = _table_dialect(rel.lower(), text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if cut and rows:
        rows = rows[:-1]            # the byte limit may have cut the last record
    if not rows:
        return []
    header = rows[0]
    names = [normalise(h) for h in header]
    data = rows[1:1 + ROWS_SCANNED]
    out: list[Finding] = []

    if REDCAP_KEY in names:
        markers = [n for n in names if n in REDCAP_MARKERS or n.endswith("_complete")]
        if markers:
            out.append(Finding(rel, "redcap-export", ", ".join([REDCAP_KEY, *markers])))

    for index, raw in enumerate(header):
        column = names[index]
        if column in IDENTIFIER_COLUMNS or column in extra_drop:
            out.append(Finding(rel, "identifier-column", raw.strip()))

    for index, raw in enumerate(header):
        values = [row[index].strip() for row in data if index < len(row) and row[index].strip()]
        if any(_BIRTH_NUMBER.fullmatch(v) or _EMAIL.fullmatch(v) or _PHONE.fullmatch(v)
               for v in values):
            out.append(Finding(rel, "identifier-values", raw.strip()))
        if len(values) >= FREE_TEXT_MIN_VALUES:
            mean = sum(len(v) for v in values) / len(values)
            wordy = sum(1 for v in values if len(v.split()) >= FREE_TEXT_WORDS)
            if mean >= FREE_TEXT_MEAN_LENGTH or wordy / len(values) >= FREE_TEXT_WORD_SHARE:
                out.append(Finding(rel, "free-text-column", raw.strip()))
    return out


def _inspect(rel: str, full: Path, pol_extra: frozenset, read_limit: int) -> list:
    name = full.name.lower()
    try:
        if full.is_symlink():
            return [Finding(rel, "unreadable", "symbolic link not followed")]
        head = _read_bytes(full, read_limit)
    except OSError as exc:
        return [Finding(rel, "unreadable", type(exc).__name__)]

    out: list[Finding] = []
    if head[DICOM_OFFSET:DICOM_OFFSET + 4] == b"DICM":
        out.append(Finding(rel, "dicom-header", f"byte offset {DICOM_OFFSET}"))
    elif name.endswith(".dcm"):
        out.append(Finding(rel, "dicom-header", "extension .dcm"))

    if any(name.endswith(ext) for ext in IMAGING):
        out.append(Finding(rel, "imaging-file", "imaging extension"))

    edf_patient = head[EDF_PATIENT[0]:EDF_PATIENT[1]].strip()
    if (name.endswith(EEG) or head.startswith(b"\xffBIOSEMI")
            or (head[:8] == b"0".ljust(8) and edf_patient not in (b"", b"X X X X"))):
        out.append(Finding(rel, "eeg-header", "EEG header or extension"))

    if name.endswith(BINARY_TABULAR):
        ext = "." + name.rsplit(".", 1)[-1]
        out.append(Finding(rel, "binary-tabular", f"extension {ext} cannot be inspected"))

    if name.endswith(TABLE_EXTENSIONS):
        cut = len(head) >= read_limit
        out.extend(_table(rel, head, cut, pol_extra))
    return out


def scan(ws, pol, *, clock=time.monotonic) -> ScanResult:
    """Bounded, deterministic scan of one run (5.6). Values never leave this function."""
    ws = Path(ws)
    extra = frozenset(normalise(x) for x in pol.extra_drop)
    names = _candidates(ws)
    start = clock()
    findings: list[Finding] = []
    seen = 0
    truncated = False
    for rel in names:
        if seen >= pol.scan_max_files or clock() - start > pol.scan_max_seconds:
            truncated = True
            break
        findings.extend(_inspect(rel, ws / rel, extra, int(pol.scan_max_bytes_per_file)))
        seen += 1
    ordered = tuple(sorted(findings, key=lambda f: (f.path, f.rule, f.detail)))
    return ScanResult(findings=ordered, files_seen=seen, truncated=truncated)
