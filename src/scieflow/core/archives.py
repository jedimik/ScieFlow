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

import json
import math
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from scieflow.core import control_read as cr
from scieflow.core import jobs, records
from scieflow.core.project import ProjectError, clean_slug

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
        text = text.replace("<n>", str(values["n"]))
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
    return Discovery(rows, total, len(rows), skipped, note)


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


def _pid_matched_outcome(child: dict | None, pid) -> str | None:
    """The fetch or restore subcommand's own outcome, when it was written by this job's process."""
    if child is None or not isinstance(pid, int) or child.get("pid") != pid:
        return None
    outcome = child.get("outcome")
    if outcome is None:
        return None
    if outcome == "failed":
        return f"failed:{child.get('reason') or 'dvc-failed'}"
    return str(outcome)


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
        outcome = _pid_matched_outcome(_child_record(project, slug), pid) or "interrupted"
        return JobView(job_id, kind, False, verify_only, outcome, exit_code)
    if state in jobs.FINAL:
        child = _child_record(project, slug)
        if state == "done":
            outcome = _pid_matched_outcome(child, pid)
        elif state == "failed":
            if exit_code == LOCKED_EXIT:
                outcome = "failed:locked"
            else:
                outcome = _pid_matched_outcome(child, pid) or "failed:dvc-failed"
        else:
            outcome = "interrupted"
        return JobView(job_id, kind, False, verify_only, outcome, exit_code)
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
            if record.get("outcome") == "ready":
                return "ready"
            if record.get("outcome") == "failed" and record.get("reason") == "verify-md5":
                return "different"
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
    if exit_code == LOCKED_EXIT:
        return "locked"
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
