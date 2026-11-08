"""Archived runs, core read model (B2:5.4-5.6.1, 5.9, 5.13; T-9 t1).

Pointers in workspace/_archives/ and workspace/<slug>.dvc, the local state of a zip archive, the
liveness of its archive job, the failure classifier and the disk rule.

Nothing here starts a process, opens a socket or writes a file, with one exception:
reconcile_archive_job, the only writer of the `lost` state for archive jobs. It is called only under
the start lock by the service's fetch, restore and cancel paths (t4), never by a read.

Every read is bounded and no-follow (control_read): a pointer, a verification record and a job
record are read through a directory descriptor with at most a stated byte cap.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import threading
import zipfile
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from scieflow.core import control_read as cr
from scieflow.core import jobs, records, workspace
from scieflow.core.data import levels
from scieflow.core.project import ProjectError, clean_slug
from scieflow.core.run import budget as budget_mod

# -- B2:5.13 constants (tests/core/test_b2_constants.py parses the spec table) ------------------
CONSENT_BYTES = 1073741824
DISK_HEADROOM = 1.05
DISK_RESERVE_BYTES = 1073741824
LIST_CAP = 500
PAGE_ENTRIES = 500
MAX_ARCHIVE_ENTRIES = 100000
MAX_CENTRAL_DIRECTORY_BYTES = 67108864
POINTER_MAX_BYTES = 65536
RECORD_MAX_BYTES = 4096
CONTROL_MAX_BYTES = 262144
INLINE_MAX_BYTES = 200000
INDEX_CACHE = 4
DVC_OUTPUT_TAIL = 65536
MD5_CHUNK_BYTES = 1048576
JOB_RECORD_MAX_BYTES = 65536
LOCKED_EXIT = 3

ARCHIVE_STATE = "archives"
JOB_KINDS = ("archive-fetch", "archive-restore")

POINTER_SUFFIX = ".zip.dvc"
LEGACY_SUFFIX = ".dvc"
JOB_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
DIR_MD5_RE = re.compile(r"^[0-9a-f]{32}\.dir$")

# -- B2:5.9 sentences (placeholders <n>, <log path>, <12 chars>) ---------------------------------
SENTENCES = {
    "dvc-missing": "dvc is not installed on this machine (docs/DVC_STORAGE.md §1).",
    "no-remote": "DVC has no usable remote for this archive (see docs/DVC_STORAGE.md §2).",
    "no-credentials": "The DVC remote needs credentials and none were found.",
    "credentials-refused": "The DVC remote refused the credentials.",
    "remote-unreachable": "The DVC remote could not be reached.",
    "missing-on-remote": "The remote does not have this archive (pointer md5 <12 chars>).",
    "disk-full": "The disk filled up during the transfer.",
    "interrupted": "The transfer was interrupted (cancelled or the process stopped).",
    "verify-size": "The downloaded file has the wrong size.",
    "verify-md5": "The downloaded file does not match the pointer's checksum.",
    "locked": "Another archive operation on this run is in progress.",
    "dvc-failed": "dvc failed (exit <n>); its output is in <log path> on this machine.",
}

# B2:5.9 match strings, in table order (first matching row wins; lower-case comparison).
_MATCH_ROWS = (
    ("no-remote", ("no remote specified", "doesn't exist", "config file error")),
    ("no-credentials", ("unable to locate credentials", "nocredentialserror", "no credentials")),
    ("credentials-refused", ("invalidaccesskeyid", "signaturedoesnotmatch", "accessdenied",
                             "access denied", "forbidden", "403", "expiredtoken")),
    ("remote-unreachable", ("could not connect to the endpoint url", "name or service not known",
                            "temporary failure in name resolution", "connection refused",
                            "network is unreachable", "timed out")),
    ("missing-on-remote", ("missing cache files", "do not exist neither locally nor on remote",
                           "not found on remote")),
    ("disk-full", ("no space left on device",)),
)


# -- paths -----------------------------------------------------------------------------------------

def state_dir_for(project) -> Path:
    return Path(project.state_dir) / ARCHIVE_STATE


def lock_path(state: Path, slug: str) -> Path:
    """The operation lock path (T8-D14): `<slug>.lock` is what FileLock takes."""
    return Path(state) / slug


def start_lock_path(state: Path, slug: str) -> Path:
    return Path(state) / f"{slug}.start"


def record_path(state: Path, slug: str) -> Path:
    return Path(state) / f"{slug}.json"


def job_pointer_path(state: Path, slug: str) -> Path:
    return Path(state) / f"{slug}.job"


def disk_needed(received: int) -> int:
    """Free bytes a receiving filesystem needs (T9-D13): exact decimal headroom, then the reserve."""
    return int(math.ceil(Decimal(received) * Decimal(str(DISK_HEADROOM)))) + DISK_RESERVE_BYTES


def sentence(code: str, **values) -> str:
    text = SENTENCES[code]
    if "n" in values:
        text = text.replace("<n>", "unknown" if values["n"] is None else str(values["n"]))
    if "log_path" in values:
        text = text.replace("<log path>", str(values["log_path"]))
    if "md5" in values:
        text = text.replace("<12 chars>", str(values["md5"])[:12])
    return text


# -- descriptor helpers ----------------------------------------------------------------------------

def _open(root: Path, parts: tuple[str, ...]) -> int | None:
    try:
        return cr.open_dir(Path(root), tuple(parts))
    except OSError:
        return None


def _close(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)


def _names(root: Path, parts: tuple[str, ...]) -> list[str]:
    fd = _open(root, parts)
    if fd is None:
        return []
    try:
        names, _more = cr.scan_dir(fd, sys.maxsize)
        return names
    finally:
        os.close(fd)


def _lstat(dir_fd: int | None, name: str):
    if dir_fd is None:
        return None
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None


def _valid_slug(slug: str) -> bool:
    try:
        return clean_slug(slug) == slug
    except ProjectError:
        return False


def _as_bytes(raw) -> bytes:
    """The bytes of a successful cr.read_bytes result (empty for anything else)."""
    data = raw.data
    return data if isinstance(data, bytes) else b""


# -- pointers (B2:5.5) -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Pointer:
    slug: str
    kind: str                 # "zip" | "directory" | "unreadable"
    md5: str | None
    size: int | None
    dvc2: bool
    reason: str | None
    path: str                 # the pointer's name, relative to workspace/
    nfiles: int | None = None


def _int_or_none_ok(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def read_pointer(dir_fd: int, name: str, slug: str, *, legacy: bool = False) -> Pointer:
    """One pointer, read bounded (POINTER_MAX_BYTES) and parsed only after the bound holds."""
    rel = f"{slug}.dvc" if legacy else f"_archives/{slug}.zip.dvc"

    def bad(reason: str) -> Pointer:
        return Pointer(slug, "unreadable", None, None, False, reason, rel)

    raw = cr.read_bytes(dir_fd, name, POINTER_MAX_BYTES)
    if raw.status != "ok":
        return bad(raw.status)
    try:
        doc = yaml.safe_load(_as_bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError, ValueError):
        return bad("yaml")
    if not isinstance(doc, dict):
        return bad("not-mapping")
    outs = doc.get("outs")
    if not isinstance(outs, list) or len(outs) != 1 or not isinstance(outs[0], dict):
        return bad("outs")
    out = outs[0]
    md5 = out.get("md5")
    if not isinstance(md5, str):
        return bad("md5")
    size = out.get("size")
    if not _int_or_none_ok(size):
        return bad("size")
    if legacy:
        if not DIR_MD5_RE.match(md5):
            return bad("md5")
        nfiles = out.get("nfiles")
        if not _int_or_none_ok(nfiles):
            return bad("nfiles")
        return Pointer(slug, "directory", md5, size, False, None, rel, nfiles)
    if md5.endswith(".dir"):
        return bad("directory-hash")
    if not MD5_RE.match(md5):
        return bad("md5")
    if out.get("path") != f"{slug}.zip":
        return bad("path")
    if "hash" in out and out["hash"] != "md5":
        return bad("hash")
    return Pointer(slug, "zip", md5, size, "hash" not in out, None, rel)


# -- discovery (T9-D9) -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Discovery:
    rows: list
    total: int
    shown: int
    skipped_names: int
    cap_note: str | None
    ws_names: list               # the one listing of workspace/ (B2:5.6), reused by every row's state


def discover(project) -> Discovery:
    """Every archive name, sorted by slug; pointers are read for the first LIST_CAP only.

    `<slug>.zip.dvc` in workspace/_archives/ is a zip pointer. `<slug>.dvc` directly in workspace/ is a
    legacy directory pointer, used only when no workspace/<slug> exists (T8-D2); a zip pointer of the
    same slug wins. A name that ends in the pointer suffix but fails clean_slug is counted, not listed.
    """
    root = Path(project.root)
    arch_names = _names(root, ("workspace", "_archives"))
    ws_names = _names(root, ("workspace",))
    ws_set = set(ws_names)
    candidates: dict[str, tuple[str, str]] = {}
    skipped = 0
    for name in arch_names:
        if not name.endswith(POINTER_SUFFIX):
            continue
        slug = name[: -len(POINTER_SUFFIX)]
        if not _valid_slug(slug):
            skipped += 1
            continue
        candidates[slug] = ("zip", name)
    for name in ws_names:
        if not name.endswith(LEGACY_SUFFIX):
            continue
        slug = name[: -len(LEGACY_SUFFIX)]
        if not _valid_slug(slug):
            skipped += 1
            continue
        if slug in ws_set or slug in candidates:
            continue
        candidates[slug] = ("directory", name)
    ordered = sorted(candidates)
    shown = ordered[:LIST_CAP]
    arch_fd = _open(root, ("workspace", "_archives"))
    ws_fd = _open(root, ("workspace",))
    rows = []
    try:
        for slug in shown:
            kind, name = candidates[slug]
            if kind == "zip":
                fd = arch_fd
            else:
                fd = ws_fd
            if fd is None:
                rows.append(Pointer(slug, "unreadable", None, None, False, "missing",
                                    name if kind == "directory" else f"_archives/{name}"))
            else:
                rows.append(read_pointer(fd, name, slug, legacy=(kind == "directory")))
    finally:
        _close(arch_fd)
        _close(ws_fd)
    total = len(ordered)
    note = f"{total - len(rows)} more not shown (cap {LIST_CAP})" if total > len(rows) else None
    return Discovery(rows, total, len(rows), skipped, note, ws_names)


# -- job liveness (B2:5.6.1) -----------------------------------------------------------------------

@dataclass(frozen=True)
class JobView:
    id: str | None
    kind: str | None
    running: bool
    verify_only: bool
    outcome: str | None
    exit_code: int | None
    reason: str | None = None
    dvc_exit: int | None = None     # dvc's own exit number of a failed download, from its child record (5.9)


def _read_json_dict(dir_fd: int | None, name: str, cap: int) -> dict | None:
    if dir_fd is None:
        return None
    raw = cr.read_bytes(dir_fd, name, cap)
    if raw.status != "ok":
        return None
    try:
        doc = json.loads(_as_bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _job_record(project, slug: str) -> tuple[str | None, dict | None, str | None]:
    """(job id, the job record for this slug, or None; and a reason when the pointer is unreadable)."""
    state = Path(project.state_dir)
    arch_fd = _open(state, (ARCHIVE_STATE,))
    try:
        if arch_fd is None:
            return None, None, None
        raw = cr.read_bytes(arch_fd, f"{slug}.job", JOB_RECORD_MAX_BYTES)
    finally:
        _close(arch_fd)
    if raw.status == "missing":
        return None, None, None
    if raw.status != "ok":
        return None, None, "job-pointer-unreadable"
    try:
        text = _as_bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None, None, "job-pointer-unreadable"
    job_id = text[:-1] if text.endswith("\n") else text
    if not JOB_ID_RE.match(job_id):
        return None, None, "job-pointer-unreadable"
    jobs_fd = _open(state, ("jobs",))
    try:
        doc = _read_json_dict(jobs_fd, f"{job_id}.json", JOB_RECORD_MAX_BYTES)
    finally:
        _close(jobs_fd)
    if doc is None or doc.get("kind") not in JOB_KINDS or doc.get("label") != slug:
        return None, None, None
    return job_id, doc, None


def _child_record(project, slug: str) -> dict | None:
    arch_fd = _open(Path(project.state_dir), (ARCHIVE_STATE,))
    try:
        return _read_json_dict(arch_fd, f"{slug}.json", RECORD_MAX_BYTES)
    finally:
        _close(arch_fd)


def _matched_child(child: dict | None, pid) -> dict | None:
    """The fetch or restore subcommand's own record, only when this job's process wrote it (5.6.1)."""
    if child is None or not isinstance(pid, int) or child.get("pid") != pid or child.get("outcome") is None:
        return None
    return child


def _outcome_of(child: dict | None) -> str | None:
    if child is None:
        return None
    outcome = child["outcome"]
    if outcome == "failed":
        return f"failed:{child.get('reason') or 'dvc-failed'}"
    return str(outcome)


def _dvc_exit(child: dict | None) -> int | None:
    """dvc's own exit number from a failed child record (B2:5.9 `dvc failed (exit <n>)`), else None."""
    if child is None or child.get("outcome") != "failed":
        return None
    code = child.get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _pid_matched_outcome(child: dict | None, pid) -> str | None:
    return _outcome_of(_matched_child(child, pid))


def effective_job(project, slug: str) -> JobView:
    """Is an archive job of this slug running, and what was the last outcome. Reads only (5.6.1)."""
    job_id, doc, reason = _job_record(project, slug)
    if doc is None:
        return JobView(None, None, False, False, None, None, reason)
    argv = doc.get("argv")
    verify_only = isinstance(argv, list) and "--verify-only" in argv
    kind = doc.get("kind")
    pid = doc.get("pid")
    exit_code = doc.get("exit_code")
    state = doc.get("state")
    if state == "running":
        if isinstance(pid, int) and jobs._alive(pid):
            return JobView(job_id, kind, True, verify_only, None, exit_code)
        child = _matched_child(_child_record(project, slug), pid)
        outcome = _outcome_of(child) or "interrupted"
        return JobView(job_id, kind, False, verify_only, outcome, exit_code, dvc_exit=_dvc_exit(child))
    if state in jobs.FINAL:
        child = _matched_child(_child_record(project, slug), pid)
        if state == "done":
            outcome = _outcome_of(child)
        elif state == "failed":
            if exit_code == LOCKED_EXIT:
                outcome = "failed:locked"
            else:
                outcome = _outcome_of(child) or "failed:dvc-failed"
        else:
            outcome = "interrupted"
        return JobView(job_id, kind, False, verify_only, outcome, exit_code, dvc_exit=_dvc_exit(child))
    return JobView(job_id, kind, False, verify_only, None, exit_code)


def reconcile_archive_job(project, slug: str) -> bool:
    """Mark a dead archive job `lost` (the only writer of `lost` for archive jobs).

    Called only under the start lock by fetch, restore and cancel. A running record whose pid is gone
    becomes `lost` with `finished` set; anything else is left as it is. No signal is ever sent here.
    """
    job_id, doc, _reason = _job_record(project, slug)
    if doc is None or doc.get("state") != "running" or doc.get("id") != job_id:
        return False
    pid = doc.get("pid")
    if not isinstance(pid, int) or jobs._alive(pid):
        return False
    try:
        job = jobs.Job(**doc)
    except (TypeError, ValueError):
        return False
    expected = Path(project.state_dir) / "jobs" / f"{job_id}.json"
    if Path(job.log).with_suffix(".json") != expected:
        return False
    job.state = "lost"
    job.finished = jobs._now()
    try:
        jobs.save(job)
    except (records.JobInvalid, ValueError):
        return False
    return True


# -- local state of a zip archive (B2:5.5) ---------------------------------------------------------

@dataclass(frozen=True)
class LocalState:
    state: str | None
    relation: str
    zip_stat: dict | None
    record: dict | None
    job: JobView
    leftovers: list
    symlink_note: str | None


def _stat_key(st) -> dict:
    return {"size": st.st_size, "st_dev": st.st_dev, "st_ino": st.st_ino,
            "st_mtime_ns": st.st_mtime_ns}


def _record_matches(record: dict, zip_stat: dict) -> bool:
    return all(record.get(key) == zip_stat[key] for key in ("st_dev", "st_ino", "st_mtime_ns")) \
        and record.get("size") == zip_stat["size"]


def _relation(project, slug: str) -> str:
    try:
        st = os.lstat(Path(project.workspace_root) / slug)
    except OSError:
        return "archived-only"
    return "alias" if stat.S_ISLNK(st.st_mode) else "also-local"


def _leftovers(arch_fd: int | None, slug: str, ws_names: list[str]) -> list[str]:
    out = []
    for name in (f"{slug}.zip.partial", f"{slug}.zip.link"):
        if _lstat(arch_fd, name) is not None:
            out.append(f"_archives/{name}")
    for name in ws_names:
        if name.startswith(f".{slug}.extract-") or name.startswith(f".{slug}.replaced-"):
            out.append(name)
    return sorted(out)


def _file_state(ptr: Pointer, job: JobView, present: bool, zip_stat: dict | None,
                bad_link: bool, record: dict | None) -> str:
    if job.running:
        if job.kind == "archive-restore":
            return "restoring"
        return "verifying" if job.verify_only else "downloading"
    if not present:
        return "not-downloaded"
    if bad_link or zip_stat is None:
        return "incomplete"
    if ptr.size is not None and zip_stat["size"] != ptr.size:
        return "incomplete"
    if record is not None:
        record_md5 = record.get("pointer_md5")
        if record_md5 is not None and record_md5 != ptr.md5:
            return "outdated"
        if record_md5 == ptr.md5 and _record_matches(record, zip_stat):
            if record.get("outcome") == "failed" and record.get("reason") == "verify-md5":
                return "different"
            # B2:5.5: the verification decides ready, not the last outcome. A failed re-download
            # that dvc did not touch keeps the verified fields, so the copy stays ready.
            if record.get("verified_at") is not None:
                return "ready"
    return "unverified"


def _verification_record(project, slug: str) -> dict | None:
    arch_fd = _open(Path(project.state_dir), (ARCHIVE_STATE,))
    try:
        return _read_json_dict(arch_fd, f"{slug}.json", RECORD_MAX_BYTES)
    finally:
        _close(arch_fd)


def local_state(project, ptr: Pointer, *, ws_names: list[str] | None = None) -> LocalState:
    """The local state of one zip pointer, from files only (plus the job's liveness)."""
    if ptr.kind != "zip":
        return LocalState(None, _relation(project, ptr.slug), None, None,
                          effective_job(project, ptr.slug), [], None)
    slug = ptr.slug
    root = Path(project.root)
    job = effective_job(project, slug)
    if ws_names is None:
        ws_names = _names(root, ("workspace",))
    arch_fd = _open(root, ("workspace", "_archives"))
    try:
        zip_name = f"{slug}.zip"
        zst = _lstat(arch_fd, zip_name)
        present = zst is not None
        bad_link = False
        symlink_note = None
        zip_stat = None
        if zst is not None and stat.S_ISLNK(zst.st_mode):
            target = os.path.realpath(root / "workspace" / "_archives" / zip_name)
            cache = os.path.realpath(root / ".dvc" / "cache")
            if target.startswith(cache + os.sep) and os.path.isfile(target):
                zip_stat = _stat_key(os.stat(target))
            else:
                bad_link = True
                symlink_note = "the local zip is a link outside the DVC cache"
        elif zst is not None and stat.S_ISREG(zst.st_mode):
            zip_stat = _stat_key(zst)
        elif zst is not None:
            bad_link = True
        record = _verification_record(project, slug)
        leftovers = _leftovers(arch_fd, slug, ws_names)
    finally:
        _close(arch_fd)
    state = _file_state(ptr, job, present, zip_stat, bad_link, record)
    return LocalState(state, _relation(project, slug), zip_stat, record, job, leftovers,
                      symlink_note)


# -- zip-level classifier and disk rule (B2:5.9, T9-D13) -------------------------------------------

def classify(exit_code: int | None, tail: bytes) -> str:
    """The failure code of one dvc run: None or a signal is interrupted; the rest by B2:5.9 order."""
    if exit_code is None or exit_code < 0:
        return "interrupted"
    if exit_code == 127:
        return "dvc-missing"
    text = tail[-DVC_OUTPUT_TAIL:].decode("utf-8", "replace").lower()
    for code, needles in _MATCH_ROWS:
        if any(needle in text for needle in needles):
            return code
    return "dvc-failed"


@dataclass(frozen=True)
class DiskCheck:
    ok: bool
    groups: list


def _existing(path) -> Path:
    here = Path(path)
    while not here.exists() and here != here.parent:
        here = here.parent
    return here


def _device(path) -> int:
    return os.stat(path).st_dev


def disk_check(root, receivers) -> DiskCheck:
    """Receivers are (path, bytes). They are grouped by filesystem; each group needs disk_needed()."""
    groups: dict[int, dict] = {}
    for path, nbytes in receivers:
        where = _existing(path)
        dev = _device(where)
        group = groups.setdefault(dev, {"path": str(where), "received": 0})
        group["received"] += nbytes
    out = []
    ok = True
    for group in groups.values():
        needed = disk_needed(group["received"])
        free = shutil.disk_usage(group["path"]).free
        if free < needed:
            ok = False
        out.append({"path": group["path"], "received": group["received"], "needed": needed,
                    "free": free})
    return DiskCheck(ok, out)


# -- zip reading (B2:5.4, 5.6; T9-D6, T9-D11, T9-D12) -----------------------------------------------

CHUNK_BYTES = 65536                        # core/turn_output.py CHUNK_BYTES (tests pin equality)
EOCD_FIXED = 22                            # end-of-central-directory record, without its comment
EOCD_SEARCH_BYTES = EOCD_FIXED + 65535     # the record plus the longest possible comment
ZIP64_EOCD_FIXED = 56
ZIP64_LOCATOR = 20
MEMBER_TARGET_MAX = 4096                   # a link's target text is read up to this many bytes
ZIP_FLAG_ENCRYPTED = 0x1
CONTROL_MEMBERS = ("status.yml", "config.yml", "budget.yml", "data.yml")
UPDATED_MEMBERS = ("status.yml", "log.md", "notebook.md")
UPDATED_AT_MEMBERS = ("status.yml", "log.md", "notebook.md", "events.jsonl")
ZIP_LABEL = "packed (approximate, the packing machine's local date)"
NOT_IN_ARCHIVE = "not in this archive"
TOO_LARGE = "too large to read here"
UNREADABLE = "unreadable"
_EOCD_SIG = b"PK\x05\x06"
_ZIP64_SIG = b"PK\x06\x06"
_ZIP64_LOC_SIG = b"PK\x06\x07"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ZIP_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
              | getattr(os, "O_CLOEXEC", 0))
_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, OSError,
                RuntimeError, ValueError)


class ZipRefused(Exception):
    """A zip cannot be read, or one member is refused. `reason` is a fixed word, never member data."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def precheck_central_directory(fd: int, size: int) -> tuple[int, int]:
    """(entries, central-directory bytes) from the end records alone (B2:5.6).

    Reads at most the last EOCD_SEARCH_BYTES (and the zip64 record when present). Refuses
    `too-many-entries` before anything is parsed, `unreadable-zip` when no record is found.
    """
    span = min(size, EOCD_SEARCH_BYTES)
    tail = os.pread(fd, span, size - span)
    pos = tail.rfind(_EOCD_SIG)
    if pos < 0 or pos + EOCD_FIXED > len(tail):
        raise ZipRefused("unreadable-zip")
    entries = struct.unpack_from("<H", tail, pos + 10)[0]
    cd_size = struct.unpack_from("<I", tail, pos + 12)[0]
    cd_offset = struct.unpack_from("<I", tail, pos + 16)[0]
    if entries == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        if _has_zip64_locator(tail, pos):
            entries, cd_size, cd_offset = _zip64_end(fd, size, tail, pos)
        elif cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
            raise ZipRefused("unreadable-zip")
        # else: zipfile writes the 0xFFFF count with no zip64 record at exactly 65,535 entries;
        # the end record's own values are the archive's, as zipfile reads them.
    if entries > MAX_ARCHIVE_ENTRIES or cd_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise ZipRefused("too-many-entries")
    if cd_offset + cd_size > size:
        raise ZipRefused("unreadable-zip")
    return entries, cd_size


def _has_zip64_locator(tail: bytes, pos: int) -> bool:
    loc = pos - ZIP64_LOCATOR
    return loc >= 0 and tail[loc:loc + 4] == _ZIP64_LOC_SIG


def _zip64_end(fd: int, size: int, tail: bytes, pos: int) -> tuple[int, int, int]:
    loc = pos - ZIP64_LOCATOR
    rec_at = struct.unpack_from("<Q", tail, loc + 8)[0]
    if rec_at + ZIP64_EOCD_FIXED > size:
        raise ZipRefused("unreadable-zip")
    rec = os.pread(fd, ZIP64_EOCD_FIXED, rec_at)
    if len(rec) != ZIP64_EOCD_FIXED or rec[:4] != _ZIP64_SIG:
        raise ZipRefused("unreadable-zip")
    return (struct.unpack_from("<Q", rec, 32)[0],
            struct.unpack_from("<Q", rec, 40)[0],
            struct.unpack_from("<Q", rec, 48)[0])


def open_zip_fd(project, slug: str) -> int:
    """A read-only descriptor for workspace/_archives/<slug>.zip (B2:5.5).

    No-follow, except a link whose real target lies inside the DVC cache (the accepted case).
    """
    root = Path(project.root)
    arch = _open(root, ("workspace", "_archives"))
    if arch is None:
        raise ZipRefused("missing")
    try:
        return os.open(f"{slug}.zip", _ZIP_FLAGS, dir_fd=arch)
    except FileNotFoundError:
        raise ZipRefused("missing") from None
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise ZipRefused("unreadable") from exc
        return _open_cache_link(root, slug)
    finally:
        os.close(arch)


def _open_cache_link(root: Path, slug: str) -> int:
    link = root / "workspace" / "_archives" / f"{slug}.zip"
    target = os.path.realpath(link)
    cache = os.path.realpath(root / ".dvc" / "cache")
    if not (target.startswith(cache + os.sep) and os.path.isfile(target)):
        raise ZipRefused("link-outside-cache")
    try:
        return os.open(target, _ZIP_FLAGS)
    except OSError as exc:
        raise ZipRefused("unreadable") from exc


def _unsafe_name(name: str) -> bool:
    """A member name no extraction or listing may use: absolute, drive-relative, backslashed,
    empty or dot segments, or any `..` (stricter than the extraction rule, so `a/../b` is refused)."""
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or _DRIVE_RE.match(name):
        return True
    stripped = name[:-1] if name.endswith("/") else name
    if not stripped:
        return True
    return any(part in ("", ".", "..") for part in stripped.split("/"))


def _member_kind(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if _unsafe_name(name):
        return "unsafe"
    if name.endswith("/"):
        return "dir"
    if info.flag_bits & ZIP_FLAG_ENCRYPTED:
        return "encrypted"
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        return "unsupported"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "link"
    return "file"


@dataclass(frozen=True)
class Member:
    name: str                  # the central-directory name, exactly
    kind: str                  # file | dir | link | unsafe | encrypted | unsupported
    size: int
    info: zipfile.ZipInfo


@dataclass
class ZipIndex:
    slug: str
    key: tuple                 # (st_dev, st_ino, st_size, st_mtime_ns) of the descriptor read
    fobj: object               # the file object the ZipFile reads; closed when the index is dropped
    zf: zipfile.ZipFile
    members: dict              # name -> Member (a duplicate name keeps its last entry, as zipfile does)
    dirs: frozenset            # every directory path, implied and explicit, without a trailing slash
    unsafe_count: int
    totals: dict


_INDEX_LOCK = threading.Lock()
_INDEX_LRU: "OrderedDict[tuple, ZipIndex]" = OrderedDict()


def _build_index(slug: str, key: tuple, fobj, zf: zipfile.ZipFile) -> ZipIndex:
    members: dict = {}
    dirs: set = set()
    totals = {"members": 0, "uncompressed_bytes": 0, "unsafe": 0, "encrypted": 0,
              "unsupported": 0, "links": 0}
    for info in zf.infolist():
        kind = _member_kind(info)
        members[info.filename] = Member(info.filename, kind, info.file_size, info)
        totals["members"] += 1
        if kind != "dir":
            totals["uncompressed_bytes"] += info.file_size
        if kind in ("unsafe", "encrypted", "unsupported"):
            totals[kind] += 1
        elif kind == "link":
            totals["links"] += 1
        if kind == "unsafe":
            continue
        parts = info.filename.rstrip("/").split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
        if kind == "dir":
            dirs.add("/".join(parts))
    return ZipIndex(slug, key, fobj, zf, members, frozenset(dirs), totals["unsafe"], totals)


def index(project, slug: str) -> ZipIndex:
    """The parsed index of one archive, cached per stat key in a process-wide LRU (B2:5.6).

    Callers must have checked that the archive is `ready` (the service does; T-9 t4). Nothing is
    written to disk. A cache entry is dropped by the LRU; a reader still using it keeps it alive.
    """
    fd = open_zip_fd(project, slug)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ZipRefused("not-regular")
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        cache_key = (slug, *key)
        with _INDEX_LOCK:
            hit = _INDEX_LRU.get(cache_key)
            if hit is not None:
                _INDEX_LRU.move_to_end(cache_key)
                return hit
            precheck_central_directory(fd, st.st_size)
            fobj = os.fdopen(fd, "rb")
            fd = -1
            try:
                zf = zipfile.ZipFile(fobj)
            except (zipfile.BadZipFile, OSError, ValueError, EOFError, struct.error) as exc:
                fobj.close()
                raise ZipRefused("unreadable-zip") from exc
            # zipfile parses the directory by its byte size, not the claimed count: the real count
            # is bounded here, and a refused archive is not cached (t2r-r1 finding 2).
            if len(zf.infolist()) > MAX_ARCHIVE_ENTRIES:
                fobj.close()
                raise ZipRefused("too-many-entries")
            idx = _build_index(slug, key, fobj, zf)
            _INDEX_LRU[cache_key] = idx
            while len(_INDEX_LRU) > INDEX_CACHE:
                _INDEX_LRU.popitem(last=False)
            return idx
    finally:
        if fd >= 0:
            os.close(fd)


def _link_target(idx: ZipIndex, name: str) -> str:
    member = idx.members[name]
    if member.size > MEMBER_TARGET_MAX:
        return TOO_LARGE
    try:
        with idx.zf.open(member.info) as src:
            data = src.read(MEMBER_TARGET_MAX + 1)
    except _READ_ERRORS:
        return UNREADABLE
    if len(data) > MEMBER_TARGET_MAX:
        return TOO_LARGE
    return data.decode("utf-8", "replace")


def entries(idx: ZipIndex, path: str = "", page: int = 1) -> dict:
    """One directory level of the archive, from member names alone (B2:5.6).

    Directories first, then the rest, each by name; `.lock` names are hidden; unsafe names are
    counted (archive-wide) and never listed; PAGE_ENTRIES per page. Nothing is followed.
    """
    base = path.strip("/")
    if base and base not in idx.dirs:
        return {"path": base, "page": page, "pages": 1, "total": 0, "entries": [],
                "hidden_locks": 0, "unsafe_count": idx.unsafe_count, "note": "no such directory"}
    prefix = f"{base}/" if base else ""
    children: dict[str, dict] = {}
    hidden: set[str] = set()
    for member in idx.members.values():
        if member.kind == "unsafe":
            continue
        stripped = member.name.rstrip("/")
        if not stripped.startswith(prefix) or stripped == base:
            continue
        rest = stripped[len(prefix):]
        if not rest:
            continue
        if "/" in rest:
            name, kind, size, target = rest.split("/", 1)[0], "dir", None, None
        else:
            name, kind = rest, member.kind
            size = None if kind == "dir" else member.size
            target = member.name if kind == "link" else None
        if name.endswith(".lock"):
            hidden.add(name)
            continue
        current = children.get(name)
        if current is not None and (current["kind"] == "dir" or kind != "dir"):
            continue
        children[name] = {"kind": kind, "size": size, "member": target}
    ordered = sorted(children.items(), key=lambda item: (item[1]["kind"] != "dir", item[0]))
    total = len(ordered)
    pages = max(1, math.ceil(total / PAGE_ENTRIES))
    if page < 1 or page > pages:
        window, note = [], f"page {page} is out of range (pages 1-{pages})"
    else:
        start = (page - 1) * PAGE_ENTRIES
        window = ordered[start:start + PAGE_ENTRIES]
        note = f"entries {start + 1}-{start + len(window)} of {total}" if total else None
    rows = []
    for name, child in window:
        row = {"name": name, "path": f"{prefix}{name}", "kind": child["kind"], "size": child["size"]}
        if child["kind"] == "link":
            row["target"] = _link_target(idx, child["member"])
        rows.append(row)
    return {"path": base, "page": page, "pages": pages, "total": total, "entries": rows,
            "hidden_locks": len(hidden), "unsafe_count": idx.unsafe_count, "note": note}


def open_member(idx: ZipIndex, path: str):
    """(ZipInfo, chunks) for one file member, matched by its exact central-directory name.

    The chunks are CHUNK_BYTES blocks read up to the declared size. A corrupt member raises
    ZipRefused("corrupt-member") from the generator, after the bytes read so far.
    """
    member = idx.members.get(path)
    if member is None:
        raise ZipRefused("no-such-member")
    if member.kind != "file":
        raise ZipRefused(member.kind)
    return member.info, _chunks(idx.zf, member.info)


def _chunks(zf: zipfile.ZipFile, info: zipfile.ZipInfo):
    left = info.file_size
    try:
        with zf.open(info) as src:
            while left > 0:
                block = src.read(min(CHUNK_BYTES, left))
                if not block:
                    break
                left -= len(block)
                yield block
            src.read()                     # reaching the end runs the CRC check
    except _READ_ERRORS as exc:
        raise ZipRefused("corrupt-member") from exc


def _control(idx: ZipIndex, name: str, *, parse: bool) -> tuple[str, Any]:
    """(word, value): word is "ok" or the fixed reason; value is the parsed mapping (parse) or bytes."""
    member = idx.members.get(name)
    if member is None:
        return NOT_IN_ARCHIVE, None
    if member.kind != "file":
        return UNREADABLE, None
    if member.size > CONTROL_MAX_BYTES:
        return TOO_LARGE, None
    try:
        with idx.zf.open(member.info) as src:
            data = src.read(CONTROL_MAX_BYTES + 1)
    except _READ_ERRORS:
        return UNREADABLE, None
    if len(data) > CONTROL_MAX_BYTES:
        return TOO_LARGE, None
    if not parse:
        return "ok", data
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError, ValueError):
        return UNREADABLE, None
    if not isinstance(doc, dict):
        return UNREADABLE, None
    return "ok", doc


def _member_when(idx: ZipIndex, name: str):
    member = idx.members.get(name)
    if member is None or member.kind != "file":
        return None
    try:
        return datetime(*member.info.date_time)
    except (TypeError, ValueError):
        return None


def _zip_stamps(idx: ZipIndex) -> tuple[str | None, str | None]:
    """updated and updated_at from the members' packed dates, never read (B2:5.6)."""
    updated = [t for t in (_member_when(idx, n) for n in UPDATED_MEMBERS) if t is not None]
    stamped = [t for t in (_member_when(idx, n) for n in UPDATED_AT_MEMBERS) if t is not None]
    return (max(updated).date().isoformat() if updated else None,
            max(stamped).isoformat(timespec="seconds") if stamped else None)


def summary(idx: ZipIndex) -> dict:
    """The run summary from the control members, the budget, the data level and the totals (B2:5.6).

    Each control is read only when its size is within CONTROL_MAX_BYTES. A control that is missing,
    too large, unreadable or of the wrong shape gives its fixed word; nothing is extracted.
    """
    controls: dict[str, str] = {}
    docs: dict[str, Any] = {}
    for name in ("status.yml", "config.yml", "budget.yml"):
        word, doc = _control(idx, name, parse=True)
        controls[name], docs[name] = word, doc
    dword, data = _control(idx, "data.yml", parse=False)
    controls["data.yml"] = dword

    status_word = controls["status.yml"]
    run, run_reason = None, None
    if status_word in (NOT_IN_ARCHIVE, "ok"):
        status = docs["status.yml"] if status_word == "ok" else {}
        config = docs["config.yml"] or {}
        updated, updated_at = _zip_stamps(idx)
        run = dataclasses.asdict(workspace.describe_from(idx.slug, status, config, updated, updated_at))
    else:
        run_reason = status_word

    bword, remaining = controls["budget.yml"], None
    if bword == "ok":
        try:
            remaining = budget_mod.remaining_fraction(docs["budget.yml"])
        except (KeyError, TypeError, ValueError, AttributeError):
            bword = UNREADABLE

    if dword == NOT_IN_ARCHIVE:
        level_info = levels.level_of(levels.DataFile(present=False))
        reason = "undeclared"
    else:
        if dword == TOO_LARGE:
            got = levels.Invalid(TOO_LARGE)
        elif dword == UNREADABLE:
            got = levels.Invalid("data.yml: cannot be read")
        else:
            try:
                got = levels.parse_text(data.decode("utf-8"))
            except UnicodeDecodeError:
                got = levels.Invalid("data.yml: cannot be read")
        level_info = levels.level_of(got)
        reason = level_info.reason
    return {
        "controls": controls,
        "run": run,
        "run_reason": run_reason,
        "budget": {"status": bword, "remaining": remaining},
        "data_level": {"level": level_info.level, "reason": reason},
        "totals": dict(idx.totals),
        "updated_label": ZIP_LABEL,
    }
