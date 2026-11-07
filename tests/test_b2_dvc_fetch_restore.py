"""T-9 t3: dvc_sync fetch and restore, the verification record, the restore re-checks, md5 and record writing.

Synthetic zips, temporary projects, SCIEFLOW_STATE_DIR isolated by tests/conftest.py, and a fake `dvc`
(tests/fixtures/b2_fake_dvc.py) placed first on PATH. The real dvc is never run; no network.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
import yaml

import dvc_sync
from scieflow.core import archives as arch
from scieflow.core import control_read as cr
from scieflow.core.project import Project
from sflib import archive
from tests.fixtures import b2_fake_dvc as fake

SLUG = "r1"


def md5_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def zip_bytes(tmp: Path, members: dict[str, bytes] | None = None) -> bytes:
    members = members or {"status.yml": b"phase: done\n", "logs/agent.log": b"transcript\n"}
    path = tmp / "synthetic.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path.read_bytes()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "workspace" / "_archives").mkdir(parents=True)
    monkeypatch.setenv("SCIEFLOW_STATE_DIR", str(tmp_path / "state"))
    return root


@pytest.fixture
def state(proj):
    return Path(os.environ["SCIEFLOW_STATE_DIR"])


@pytest.fixture
def dvc_bin(tmp_path, monkeypatch):
    bin_dir = fake.install(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def tmp_data(tmp_path):
    return zip_bytes(tmp_path)


def write_pointer(root: Path, slug: str, data: bytes, *, md5: str | None = None,
                  size: int | None = None) -> Path:
    doc = {"outs": [{"md5": md5 or md5_of(data), "size": len(data) if size is None else size,
                     "path": f"{slug}.zip", "hash": "md5"}]}
    path = root / "workspace" / "_archives" / f"{slug}.zip.dvc"
    path.write_text(yaml.safe_dump(doc))
    return path


def zip_path(root: Path, slug: str = SLUG) -> Path:
    return root / "workspace" / "_archives" / f"{slug}.zip"


def read_record(state: Path, slug: str = SLUG) -> dict:
    return json.loads((state / "archives" / f"{slug}.json").read_text())


def fetch(root: Path, *, slug: str = SLUG, state: Path, **kw) -> int:
    return dvc_sync.cmd_fetch(slug, root, root / "workspace", state, **kw)


def restore(root: Path, *, slug: str = SLUG, state: Path) -> int:
    return dvc_sync.cmd_restore(slug, root, root / "workspace", state)


def ready_copy(root: Path, state: Path, dvc_bin: Path, data: bytes, slug: str = SLUG) -> bytes:
    write_pointer(root, slug, data)
    fake.set_script(dvc_bin, content=data)
    assert fetch(root, slug=slug, state=state) == 0
    return data


def ptr_of(root: Path, slug: str = SLUG):
    fd = cr.open_dir(root, ("workspace", "_archives"))
    try:
        return arch.read_pointer(fd, f"{slug}.zip.dvc", slug)
    finally:
        os.close(fd)


def leftovers(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "workspace").iterdir() if p.name.startswith(f".{SLUG}."))


# -- fetch: happy path and the record ------------------------------------------------------------

def test_fetch_downloads_verifies_and_records_the_copy(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state) == 0

    zpath = zip_path(proj)
    assert zpath.read_bytes() == tmp_data
    st = zpath.stat()
    rec = read_record(state)
    assert rec["outcome"] == "ready" and rec["reason"] is None and rec["exit_code"] == 0
    assert rec["pointer_md5"] == md5_of(tmp_data) and rec["size"] == len(tmp_data)
    assert (rec["st_dev"], rec["st_ino"], rec["st_mtime_ns"]) == (st.st_dev, st.st_ino, st.st_mtime_ns)
    assert rec["pid"] == os.getpid() and rec["linked"] is False and rec["verified_at"]
    assert fake.argv(dvc_bin) == [["pull", "workspace/_archives/r1.zip.dvc"]]
    assert not (proj / "workspace" / SLUG).exists()  # nothing is unpacked


def test_fetch_links_the_zip_to_the_cache_object_before_recording(proj, state, dvc_bin, tmp_data):
    digest = md5_of(tmp_data)
    obj = proj / ".dvc" / "cache" / "files" / "md5" / digest[:2] / digest[2:]
    obj.parent.mkdir(parents=True)
    obj.write_bytes(tmp_data)
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state) == 0

    rec = read_record(state)
    assert rec["linked"] is True
    assert os.path.samefile(zip_path(proj), obj)
    assert rec["st_ino"] == obj.stat().st_ino  # the record holds the final stat, after the link


# -- fetch: failures become the classifier's code, the job output keeps dvc's text ----------------

CLASSIFY_ROWS = [
    ("no-remote", "ERROR: no remote specified in this project"),
    ("no-credentials", "Unable to locate credentials"),
    ("credentials-refused", "An error occurred (InvalidAccessKeyId)"),
    ("remote-unreachable", "Could not connect to the endpoint URL"),
    ("missing-on-remote", "ERROR: missing cache files"),
    ("disk-full", "[Errno 28] No space left on device"),
    ("dvc-failed", "something unexpected happened"),
]


@pytest.mark.parametrize("code,line", CLASSIFY_ROWS)
def test_a_dvc_failure_is_recorded_as_its_code_only(proj, state, dvc_bin, tmp_data, capsys,
                                                    code, line):
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, exit_code=1, stderr=[line])

    assert fetch(proj, state=state) == 1

    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == code and rec["exit_code"] == 1
    assert line in capsys.readouterr().out  # the job log keeps dvc's full output
    assert line not in (state / "archives" / f"{SLUG}.json").read_text()
    assert not zip_path(proj).exists()


def test_missing_dvc_is_dvc_missing(proj, state, tmp_data, tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    write_pointer(proj, SLUG, tmp_data)

    assert fetch(proj, state=state) == 1

    rec = read_record(state)
    assert rec["reason"] == "dvc-missing" and rec["exit_code"] == 127


def test_wrong_size_after_the_pull_is_verify_size(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data, size=len(tmp_data) + 5)
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state) == 1

    assert read_record(state)["reason"] == "verify-size"


def test_wrong_checksum_is_verify_md5_and_the_run_reads_as_different(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data, md5="0" * 32)
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state) == 1

    assert read_record(state)["reason"] == "verify-md5"
    assert arch.local_state(Project(proj), ptr_of(proj)).state == "different"


# -- fetch: verify-only, skip-pull, replace ----------------------------------------------------

def test_verify_only_never_runs_dvc(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    zip_path(proj).write_bytes(tmp_data)
    fake.set_script(dvc_bin, content=b"must not be written")

    assert fetch(proj, state=state, verify_only=True) == 0

    assert fake.argv(dvc_bin) == []
    assert zip_path(proj).read_bytes() == tmp_data
    assert read_record(state)["outcome"] == "ready"


def test_verify_only_without_a_zip_fails_verify_size_and_runs_nothing(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)

    assert fetch(proj, state=state, verify_only=True) == 1

    assert fake.argv(dvc_bin) == []
    assert read_record(state)["reason"] == "verify-size"


def test_matching_size_skips_the_pull_unless_replace(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    zip_path(proj).write_bytes(tmp_data)

    assert fetch(proj, state=state) == 0

    assert fake.argv(dvc_bin) == []
    assert read_record(state)["outcome"] == "ready"


def test_replace_pulls_with_force(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    zip_path(proj).write_bytes(b"x" * len(tmp_data))  # same size, other bytes
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state, replace=True) == 0

    assert fake.argv(dvc_bin) == [["pull", "--force", "workspace/_archives/r1.zip.dvc"]]
    assert zip_path(proj).read_bytes() == tmp_data


def test_remote_name_is_passed_as_dash_r(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, content=tmp_data)

    assert fetch(proj, state=state, remote="cesnet") == 0

    assert fake.argv(dvc_bin) == [["pull", "-r", "cesnet", "workspace/_archives/r1.zip.dvc"]]


def test_failed_redownload_over_an_untouched_ready_copy_stays_ready(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)
    fake.set_script(dvc_bin, exit_code=1, stderr=["Could not connect to the endpoint URL"])

    assert fetch(proj, state=state, replace=True) == 1

    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "remote-unreachable"
    assert arch.local_state(Project(proj), ptr_of(proj)).state == "ready"


def test_failed_redownload_over_a_changed_file_is_not_ready(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)
    zip_path(proj).write_bytes(b"other, and a different size")
    fake.set_script(dvc_bin, exit_code=1, stderr=["Could not connect to the endpoint URL"])

    assert fetch(proj, state=state, replace=True) == 1

    assert "verified_at" not in read_record(state)
    assert arch.local_state(Project(proj), ptr_of(proj)).state != "ready"


# -- restore -------------------------------------------------------------------------------------

def test_restore_unpacks_a_ready_copy_into_an_absent_run(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)

    assert restore(proj, state=state) == 0

    run = proj / "workspace" / SLUG
    assert (run / "status.yml").read_text() == "phase: done\n"
    assert zip_path(proj).read_bytes() == tmp_data  # the zip is kept
    rec = read_record(state)
    assert rec["outcome"] == "restored" and rec["reason"] is None and rec["exit_code"] == 0
    assert rec["pid"] == os.getpid()
    assert leftovers(proj) == []


def test_restore_into_an_empty_run_directory(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)
    (proj / "workspace" / SLUG).mkdir()

    assert restore(proj, state=state) == 0

    assert (proj / "workspace" / SLUG / "status.yml").exists()


def test_restore_refuses_a_non_empty_run_and_leaves_it_untouched(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)
    run = proj / "workspace" / SLUG
    run.mkdir()
    (run / "keep.txt").write_text("mine")

    assert restore(proj, state=state) == 1

    assert sorted(os.listdir(run)) == ["keep.txt"]
    assert (run / "keep.txt").read_text() == "mine"
    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "run-exists"


def test_restore_without_a_verification_record_is_not_ready(proj, state, dvc_bin, tmp_data):
    write_pointer(proj, SLUG, tmp_data)
    zip_path(proj).write_bytes(tmp_data)

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    assert not (state / "archives" / f"{SLUG}.json").exists()


def test_restore_after_the_zip_changed_is_not_ready_and_writes_nothing(proj, state, dvc_bin, tmp_data):
    ready_copy(proj, state, dvc_bin, tmp_data)
    before = (state / "archives" / f"{SLUG}.json").read_text()
    st = zip_path(proj).stat()
    os.utime(zip_path(proj), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    assert (state / "archives" / f"{SLUG}.json").read_text() == before


def test_restore_of_a_corrupt_member_fails_and_leaves_nothing(proj, state, dvc_bin, tmp_path):
    data = bytearray(zip_bytes(tmp_path))
    data[data.find(b"phase: done")] = ord("q")  # stored member: CRC in the header no longer matches
    ready_copy(proj, state, dvc_bin, bytes(data))

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    assert leftovers(proj) == []
    assert read_record(state)["reason"] == "corrupt-member"


def test_restore_of_an_unreadable_zip_is_unreadable_zip(proj, state, dvc_bin):
    ready_copy(proj, state, dvc_bin, b"this is not a zip file at all")

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    assert leftovers(proj) == []
    assert read_record(state)["reason"] == "unreadable-zip"


# -- the subprocess runner, the parser, the helpers ------------------------------------------------

def test_run_cmd_tail_copies_all_output_and_keeps_only_the_tail(tmp_path, capsys, dvc_bin):
    fake.set_script(dvc_bin, exit_code=2, stdout=["line-a"], stderr=["line-b"])

    rc, tail = dvc_sync.run_cmd_tail(["dvc", "pull", "x"], cwd=tmp_path, tail=8)

    assert rc == 2
    assert tail == b"\nline-b\n"
    out = capsys.readouterr().out
    assert "line-a" in out and "line-b" in out


def test_run_cmd_tail_reports_a_missing_program_as_127(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    rc, tail = dvc_sync.run_cmd_tail(["dvc", "pull"], cwd=tmp_path, tail=64)
    assert (rc, tail) == (127, b"")


def test_parser_accepts_fetch_and_restore_options(tmp_path):
    ap = dvc_sync.build_parser(tmp_path / ".env")
    a = ap.parse_args(["fetch", "r1", "--replace", "--verify-only", "--remote", "s3x",
                       "--state-dir", str(tmp_path)])
    assert (a.command, a.slug, a.replace, a.verify_only, a.remote, a.state_dir) == (
        "fetch", "r1", True, True, "s3x", tmp_path)
    b = ap.parse_args(["restore", "r1", "--state-dir", str(tmp_path)])
    assert (b.command, b.slug, b.state_dir) == ("restore", "r1", tmp_path)
    with pytest.raises(SystemExit):
        ap.parse_args(["fetch"])


def test_md5_file_streams_in_chunks(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc" * 100_000)
    assert archive.md5_file(p, 1000) == hashlib.md5(b"abc" * 100_000).hexdigest()


def test_write_record_is_atomic_and_creates_its_directory(tmp_path):
    target = tmp_path / "a" / "b" / "r1.json"
    archive.write_record(target, {"outcome": "ready"})
    assert json.loads(target.read_text()) == {"outcome": "ready"}
    assert [p.name for p in target.parent.iterdir()] == ["r1.json"]


# -- T-9 t3 fix: a foreign link under --replace; OS errors become failed records --------------------

def foreign_link(root: Path, tmp_path: Path, data: bytes) -> Path:
    target = tmp_path / "elsewhere.zip"
    target.write_bytes(data)
    zip_path(root).symlink_to(target)
    return target


def test_replace_pulls_over_a_foreign_link_and_accepts_the_result(proj, state, dvc_bin, tmp_data,
                                                                  tmp_path):
    write_pointer(proj, SLUG, tmp_data)
    foreign_link(proj, tmp_path, b"x" * len(tmp_data))
    fake.set_script(dvc_bin, content=tmp_data, unlink=True)

    assert fetch(proj, state=state, replace=True) == 0

    assert fake.argv(dvc_bin) == [["pull", "--force", "workspace/_archives/r1.zip.dvc"]]
    assert not zip_path(proj).is_symlink()
    assert zip_path(proj).read_bytes() == tmp_data
    assert read_record(state)["outcome"] == "ready"


def test_replace_refuses_a_link_that_survives_the_pull_and_never_opens_it(proj, state, dvc_bin,
                                                                          tmp_data, tmp_path, capsys):
    write_pointer(proj, SLUG, tmp_data)
    other = b"x" * len(tmp_data)
    target = foreign_link(proj, tmp_path, other)
    fake.set_script(dvc_bin)  # dvc succeeds and changes nothing

    assert fetch(proj, state=state, replace=True) == 1

    assert fake.argv(dvc_bin) == [["pull", "--force", "workspace/_archives/r1.zip.dvc"]]
    assert zip_path(proj).is_symlink()
    assert target.read_bytes() == other
    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "dvc-failed" and rec["exit_code"] == 0
    assert "link outside the DVC cache" in capsys.readouterr().out


def test_a_foreign_link_without_replace_is_refused_before_dvc_runs(proj, state, dvc_bin, tmp_data,
                                                                    tmp_path):
    write_pointer(proj, SLUG, tmp_data)
    foreign_link(proj, tmp_path, tmp_data)

    assert fetch(proj, state=state) == 1
    assert fetch(proj, state=state, verify_only=True) == 1

    assert fake.argv(dvc_bin) == []


def test_disk_full_while_checksumming_is_a_disk_full_record(proj, state, dvc_bin, tmp_data,
                                                             monkeypatch, capsys):
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, content=tmp_data)

    def full(path, chunk):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(archive, "md5_file", full)

    assert fetch(proj, state=state) == 1

    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "disk-full"
    assert arch.sentence("disk-full") in capsys.readouterr().out


def test_other_io_error_while_checksumming_is_a_failed_record_not_a_traceback(
        proj, state, dvc_bin, tmp_data, monkeypatch):
    write_pointer(proj, SLUG, tmp_data)
    fake.set_script(dvc_bin, content=tmp_data)

    def broken(path, chunk):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(archive, "md5_file", broken)

    assert fetch(proj, state=state) == 1

    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "dvc-failed"


def test_restore_disk_full_leaves_no_run_and_records_disk_full(proj, state, dvc_bin, tmp_data,
                                                                monkeypatch):
    ready_copy(proj, state, dvc_bin, tmp_data)

    def full(zip_file, dest, *, force=False):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(archive, "extract_zip", full)

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    assert leftovers(proj) == []
    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "disk-full"


def test_restore_bad_zip_during_extraction_is_unreadable_zip(proj, state, dvc_bin, tmp_data,
                                                              monkeypatch):
    ready_copy(proj, state, dvc_bin, tmp_data)

    def bad(zip_file, dest, *, force=False):
        raise zipfile.BadZipFile("bad central directory")

    monkeypatch.setattr(archive, "extract_zip", bad)

    assert restore(proj, state=state) == 1

    assert not (proj / "workspace" / SLUG).exists()
    rec = read_record(state)
    assert rec["outcome"] == "failed" and rec["reason"] == "unreadable-zip"
