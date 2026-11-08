"""T-9 t1: pointer reads, discovery, bounded control reads, and local states (B2:5.5, T9-D9, T9-D12).

Synthetic temporary projects only. Every test that reads asserts that no subprocess, no socket and no
write happens (the read functions must be pure).
"""

import os
import socket
import subprocess
from pathlib import Path

import pytest

from scieflow.core import archives as a
from scieflow.core import control_read as cr
from tests.fixtures.b2_archives import (MD5, OTHER_MD5, dead_pid, make_project, stat_record,
                                        tree_snapshot, write_job, write_job_pointer, write_legacy_pointer,
                                        write_pointer, write_record, write_zip, zip_path)

pytestmark = pytest.mark.skipif(not cr.supported(), reason="needs O_NOFOLLOW and dir_fd support")


@pytest.fixture
def no_side_effects(monkeypatch):
    """A read must not start a process or open a socket."""
    def boom(*args, **kwargs):
        raise AssertionError("a read started a subprocess or a socket")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def _read_zip_pointer(project, slug, legacy=False):
    if legacy:
        fd = cr.open_dir(project.root, ("workspace",))
        name = f"{slug}.dvc"
    else:
        fd = cr.open_dir(project.root, ("workspace", "_archives"))
        name = f"{slug}.zip.dvc"
    try:
        return a.read_pointer(fd, name, slug, legacy=legacy)
    finally:
        os.close(fd)


# ---- read_bytes (control_read, T9-D12) ------------------------------------------------------

@pytest.fixture
def run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    yield root, fd
    os.close(fd)


def test_read_bytes_returns_the_exact_bytes(run):
    root, fd = run
    (root / "p.zip.dvc").write_bytes(b"\x00raw\xff")
    result = cr.read_bytes(fd, "p.zip.dvc", 100)
    assert result.status == "ok" and result.data == b"\x00raw\xff"


def test_read_bytes_missing_symlink_directory_and_fifo(run, tmp_path):
    root, fd = run
    assert cr.read_bytes(fd, "none", 10).status == "missing"
    (root / "target").write_bytes(b"a")
    (root / "link").symlink_to(root / "target")
    assert cr.read_bytes(fd, "link", 10).status == "symlink"
    (root / "dir").mkdir()
    assert cr.read_bytes(fd, "dir", 10).status == "not-regular"
    os.mkfifo(root / "fifo")
    assert cr.read_bytes(fd, "fifo", 10).status == "not-regular"


def test_read_bytes_cap_is_exact(run):
    root, fd = run
    (root / "p").write_bytes(b"x" * 10)
    assert cr.read_bytes(fd, "p", 10).status == "ok"
    assert cr.read_bytes(fd, "p", 9).status == "oversized"


def test_read_bytes_replacement_during_read_is_changed(run, monkeypatch):
    from types import SimpleNamespace
    root, fd = run
    (root / "p").write_bytes(b"abc")
    real = os.fstat
    calls = []

    def fake(handle):
        st = real(handle)
        calls.append(1)
        if len(calls) < 2:
            return st
        return SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev, st_ino=st.st_ino,
                               st_size=st.st_size, st_mtime_ns=st.st_mtime_ns + 1)

    monkeypatch.setattr(cr.os, "fstat", fake)
    assert cr.read_bytes(fd, "p", 10).status == "changed"


def test_read_bytes_does_not_parse(run):
    root, fd = run
    (root / "p").write_bytes(b"::: not yaml [")
    result = cr.read_bytes(fd, "p", 100)
    assert result.status == "ok" and result.data == b"::: not yaml ["


# ---- pointers (B2:5.5) ----------------------------------------------------------------------

def test_valid_zip_pointer(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    ptr = _read_zip_pointer(project, "r1")
    assert (ptr.kind, ptr.slug, ptr.md5, ptr.size, ptr.dvc2, ptr.reason) == ("zip", "r1", MD5, 6, False, None)


def test_dvc2_pointer_without_hash_is_read_as_zip(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", hash_key=None)
    ptr = _read_zip_pointer(project, "r1")
    assert ptr.kind == "zip" and ptr.dvc2 is True and ptr.reason is None


def test_optional_size_may_be_absent(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=None)
    ptr = _read_zip_pointer(project, "r1")
    assert ptr.kind == "zip" and ptr.size is None


@pytest.mark.parametrize("text,reason", [
    ("outs: [\n", "yaml"),
    ("- a\n- b\n", "not-mapping"),
    ("outs: []\n", "outs"),
    ("outs:\n  - {md5: '" + MD5 + "', path: r1.zip}\n  - {md5: '" + MD5 + "', path: r1.zip}\n", "outs"),
    ("outs:\n  - {md5: 'ABCDEF0123456789abcdef0123456789', path: r1.zip}\n", "md5"),
    ("outs:\n  - {md5: 'abc', path: r1.zip}\n", "md5"),
    ("outs:\n  - {md5: '" + MD5 + "', path: other.zip}\n", "path"),
    ("outs:\n  - {md5: '" + MD5 + ".dir', path: r1.zip}\n", "directory-hash"),
    ("outs:\n  - {md5: '" + MD5 + "', path: r1.zip, size: -1}\n", "size"),
    ("outs:\n  - {md5: '" + MD5 + "', path: r1.zip, size: '6'}\n", "size"),
    ("outs:\n  - {md5: '" + MD5 + "', path: r1.zip, size: true}\n", "size"),
    ("outs:\n  - {md5: '" + MD5 + "', path: r1.zip, hash: sha256}\n", "hash"),
])
def test_unreadable_pointer_reasons(tmp_path, text, reason):
    project = make_project(tmp_path)
    write_pointer(project, "r1", text=text)
    ptr = _read_zip_pointer(project, "r1")
    assert ptr.kind == "unreadable" and ptr.reason == reason


def test_symlink_pointer_is_unreadable_and_not_followed(tmp_path):
    project = make_project(tmp_path)
    real = tmp_path / "elsewhere.dvc"
    real.write_text("outs:\n  - {md5: '" + MD5 + "', path: r1.zip}\n")
    (project.workspace_root / "_archives" / "r1.zip.dvc").symlink_to(real)
    ptr = _read_zip_pointer(project, "r1")
    assert ptr.kind == "unreadable" and ptr.reason == "symlink"


def test_oversized_pointer_is_unreadable(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", text="#" + "x" * a.POINTER_MAX_BYTES + "\n")
    ptr = _read_zip_pointer(project, "r1")
    assert ptr.kind == "unreadable" and ptr.reason == "oversized"


def test_legacy_directory_pointer_needs_a_dir_md5(tmp_path):
    project = make_project(tmp_path)
    write_legacy_pointer(project, "old", md5=MD5 + ".dir", size=6, nfiles=2)
    ptr = _read_zip_pointer(project, "old", legacy=True)
    assert ptr.kind == "directory" and ptr.size == 6 and ptr.nfiles == 2
    write_legacy_pointer(project, "old2", md5=MD5)
    bad = _read_zip_pointer(project, "old2", legacy=True)
    assert bad.kind == "unreadable" and bad.reason == "md5"


# ---- discovery (T9-D9) -----------------------------------------------------------------------

def test_discovery_sorts_by_slug_and_counts_invalid_pointer_names(tmp_path, no_side_effects):
    project = make_project(tmp_path)
    for slug in ("c", "a", "b"):
        write_pointer(project, slug)
    (project.workspace_root / "_archives" / "Bad*name.zip.dvc").write_text("outs: []\n")
    (project.workspace_root / "_archives" / "note.txt").write_text("not a pointer")
    found = a.discover(project)
    assert [row.slug for row in found.rows] == ["a", "b", "c"]
    assert (found.total, found.shown, found.skipped_names, found.cap_note) == (3, 3, 1, None)


def test_discovery_cap_is_exact_and_only_the_first_pointers_are_opened(tmp_path, monkeypatch,
                                                                       no_side_effects):
    project = make_project(tmp_path)
    for index in range(a.LIST_CAP + 1):
        write_pointer(project, f"s{index:04d}")
    opened = []
    real = a.read_pointer

    def spy(*args, **kwargs):
        opened.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(a, "read_pointer", spy)
    found = a.discover(project)
    assert (found.total, found.shown) == (a.LIST_CAP + 1, a.LIST_CAP)
    assert found.cap_note == "1 more not shown (cap 500)"
    assert len(opened) == a.LIST_CAP
    assert opened == sorted(opened)


def test_legacy_pointer_listed_only_when_no_run_directory_exists(tmp_path, no_side_effects):
    project = make_project(tmp_path)
    write_legacy_pointer(project, "old")
    write_legacy_pointer(project, "shadow")
    (project.workspace_root / "shadow").mkdir()
    found = a.discover(project)
    assert [(row.slug, row.kind) for row in found.rows] == [("old", "directory")]


def test_discovery_writes_nothing(tmp_path, no_side_effects):
    project = make_project(tmp_path)
    write_pointer(project, "r1")
    before = tree_snapshot(tmp_path)
    a.discover(project)
    assert tree_snapshot(tmp_path) == before


# ---- local states (B2:5.5) -------------------------------------------------------------------

def _state(project, slug, ptr=None):
    ptr = ptr or _read_zip_pointer(project, slug)
    return a.local_state(project, ptr)


def test_not_downloaded_without_a_zip(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1")
    st = _state(project, "r1")
    assert (st.state, st.relation) == ("not-downloaded", "archived-only")


def test_size_mismatch_is_incomplete(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_zip(project, "r1", b"abc")
    assert _state(project, "r1").state == "incomplete"


def test_full_size_without_a_record_is_unverified(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_zip(project, "r1", b"abcdef")
    assert _state(project, "r1").state == "unverified"


def test_matching_record_is_ready(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file))
    st = _state(project, "r1")
    assert st.state == "ready"
    assert st.record["outcome"] == "ready"


def test_a_changed_stat_invalidates_ready(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file))
    os.utime(zip_file, ns=(1, os.stat(zip_file).st_mtime_ns + 10**9))
    assert _state(project, "r1").state == "unverified"


def test_record_for_an_older_md5_is_outdated(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, pointer_md5=OTHER_MD5))
    assert _state(project, "r1").state == "outdated"


def test_md5_mismatch_record_is_different(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, outcome="failed", reason="verify-md5"))
    assert _state(project, "r1").state == "different"


def test_failed_redownload_over_an_untouched_ready_copy_stays_ready(tmp_path):
    # B2:5.5: a failed re-download that dvc did not touch keeps the verification of the copy.
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, outcome="failed", reason="remote-unreachable"))
    st = _state(project, "r1")
    assert st.state == "ready"
    assert st.record["outcome"] == "failed"


def test_any_failed_reason_over_an_untouched_copy_stays_ready(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, outcome="failed", reason="disk-full"))
    assert _state(project, "r1").state == "ready"


def test_a_restored_outcome_keeps_the_copy_ready(tmp_path):
    # restore merges {outcome: restored} into the same record (plan 5.8); the verification still holds.
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, outcome="restored"))
    assert _state(project, "r1").state == "ready"


def test_failed_outcome_with_a_changed_stat_is_unverified(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", stat_record(zip_file, outcome="failed", reason="remote-unreachable"))
    os.utime(zip_file, ns=(1, os.stat(zip_file).st_mtime_ns + 10**9))
    assert _state(project, "r1").state == "unverified"


def test_failed_outcome_without_verification_fields_is_unverified(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", {"outcome": "failed", "reason": "remote-unreachable", "exit_code": 1, "pid": 1})
    assert _state(project, "r1").state == "unverified"


def test_matching_fields_without_a_verified_at_marker_are_unverified(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    zip_file = write_zip(project, "r1", b"abcdef")
    record = stat_record(zip_file, outcome="failed", reason="remote-unreachable")
    record["verified_at"] = None
    write_record(project, "r1", record)
    assert _state(project, "r1").state == "unverified"


def test_oversized_or_broken_record_counts_as_no_record(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_zip(project, "r1", b"abcdef")
    write_record(project, "r1", "{" + " " * a.RECORD_MAX_BYTES + "}")
    assert _state(project, "r1").state == "unverified"
    write_record(project, "r1", "{not json")
    assert _state(project, "r1").state == "unverified"


def test_running_jobs_win_over_file_states(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_job(project, "r1", kind="archive-fetch", state="running", argv=["x", "fetch", "r1"])
    assert _state(project, "r1").state == "downloading"
    write_job(project, "r1", kind="archive-fetch", state="running",
              argv=["x", "fetch", "r1", "--verify-only"])
    assert _state(project, "r1").state == "verifying"
    write_job(project, "r1", kind="archive-restore", state="running", argv=["x", "restore", "r1"])
    assert _state(project, "r1").state == "restoring"


def test_a_dead_job_does_not_change_the_file_state(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    write_job(project, "r1", kind="archive-fetch", state="running", pid=dead_pid())
    st = _state(project, "r1")
    assert st.state == "not-downloaded"
    assert st.job.running is False and st.job.outcome == "interrupted"


def test_symlink_inside_the_dvc_cache_is_followed_once_and_ready(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    cache_file = tmp_path / ".dvc" / "cache" / "01" / "23456789"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"abcdef")
    zip_path(project, "r1").symlink_to(cache_file)
    write_record(project, "r1", stat_record(cache_file))
    st = _state(project, "r1")
    assert st.state == "ready" and st.symlink_note is None


def test_symlink_outside_the_dvc_cache_is_incomplete_and_never_opened(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    write_pointer(project, "r1", size=6)
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"abcdef")
    zip_path(project, "r1").symlink_to(outside)
    real_open = os.open

    def no_open(path, *args, **kwargs):
        if str(path).endswith("r1.zip") or str(path) == str(outside):
            raise AssertionError("the outside link was opened")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(a.os, "open", no_open, raising=False)
    st = _state(project, "r1")
    assert st.state == "incomplete"
    assert st.symlink_note == "the local zip is a link outside the DVC cache"


@pytest.mark.parametrize("make,relation", [
    (lambda p: None, "archived-only"),
    (lambda p: (p.workspace_root / "r1").mkdir(), "also-local"),
    (lambda p: (p.workspace_root / "r1").symlink_to(p.workspace_root / "elsewhere"), "alias"),
])
def test_run_relation(tmp_path, make, relation):
    project = make_project(tmp_path)
    write_pointer(project, "r1")
    (project.workspace_root / "elsewhere").mkdir()
    make(project)
    assert _state(project, "r1").relation == relation


def test_leftovers_are_listed_by_name_only(tmp_path):
    project = make_project(tmp_path)
    write_pointer(project, "r1")
    arch = project.workspace_root / "_archives"
    (arch / "r1.zip.partial").write_bytes(b"x")
    (arch / "r1.zip.link").write_bytes(b"x")
    (project.workspace_root / ".r1.extract-abc").mkdir()
    (project.workspace_root / ".r1.replaced-def").mkdir()
    (project.workspace_root / ".r1.other").mkdir()
    st = _state(project, "r1")
    assert sorted(st.leftovers) == sorted([
        "_archives/r1.zip.partial", "_archives/r1.zip.link",
        ".r1.extract-abc", ".r1.replaced-def"])
