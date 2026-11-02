"""M-D3 t2: post-fetch transfer check and marker reading (plan 5.3; section 6 t2 item 5)."""

import hashlib
import re
from pathlib import Path

import pytest

from scieflow.experiments import remote_bundle as rb

FETCHED = "2026-11-02T12:00:00+00:00"
DEFAULT_FILES = {
    "artifacts/result.png": b"RESULT",
    "logs/stdout.log": b"out\n",
    "logs/stderr.log": b"",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_run(run: Path, files: dict[str, bytes] = DEFAULT_FILES) -> Path:
    """A synthetic run folder as the remote task would leave it (no host name anywhere)."""
    for rel, data in files.items():
        p = run / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (run / "remote").mkdir(parents=True, exist_ok=True)
    sums = "".join(f"{sha(files[rel])}  {rel}\n" for rel in sorted(files))
    (run / "remote" / "SHA256SUMS").write_text(sums)
    (run / "remote" / "exit").write_text("0\n")
    (run / "remote" / "started").write_text("2026-11-02T10:00:00+00:00\n")
    (run / "remote" / "finished").write_text("2026-11-02T10:00:05+00:00\n")
    (run / "remote" / "environment.txt").write_text(
        "system=Linux\nmachine=x86_64\nruntime=Python 3.12.3\n"
    )
    return run


def test_clean_transfer_verifies(tmp_path):
    run = write_run(tmp_path / "run")
    assert rb.verify_transfer(run) == (True, None, 3)


def test_tampered_artifact_is_transfer_hash(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "artifacts" / "result.png").write_bytes(b"TAMPERED")
    assert rb.verify_transfer(run)[:2] == (False, "transfer-hash")


def test_deleted_listed_file_is_transfer_hash(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "logs" / "stdout.log").unlink()
    assert rb.verify_transfer(run)[:2] == (False, "transfer-hash")


def test_unlisted_file_is_transfer_extra(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "logs" / "extra.log").write_bytes(b"extra")
    assert rb.verify_transfer(run)[:2] == (False, "transfer-extra")


def test_planted_symlink_is_fetched_symlink(tmp_path):
    run = write_run(tmp_path / "run")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (run / "artifacts" / "link.png").symlink_to(outside)
    assert rb.verify_transfer(run)[:2] == (False, "fetched-symlink")


def test_symlink_in_remote_folder_is_fetched_symlink(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "remote" / "exit.link").symlink_to(run / "remote" / "exit")
    assert rb.verify_transfer(run)[1] == "fetched-symlink"


def test_missing_checksum_list_is_transfer_missing(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "remote" / "SHA256SUMS").unlink()
    assert rb.verify_transfer(run)[:2] == (False, "transfer-missing")


@pytest.mark.parametrize("line", [
    "{h}  artifacts/../logs/stdout.log",
    "{h}  /etc/passwd",
    "{h}  artifacts//result.png",
    "{h}  remote/exit",
    "\\\\{h}  artifacts/result.png",
    "xyz  artifacts/result.png",
    "{h}  artifacts/result.png\r",
])
def test_malformed_checksum_lines_are_refused(tmp_path, line):
    run = write_run(tmp_path / "run")
    listed = f"{sha(DEFAULT_FILES['artifacts/result.png'])}  artifacts/result.png\n"
    bad = line.format(h=sha(b"x"))
    (run / "remote" / "SHA256SUMS").write_text(listed + bad + "\n")
    ok, reason, _ = rb.verify_transfer(run)
    assert (ok, reason) == (False, "transfer-hash")


def test_duplicate_checksum_line_is_refused(tmp_path):
    run = write_run(tmp_path / "run")
    line = f"{sha(DEFAULT_FILES['artifacts/result.png'])}  artifacts/result.png\n"
    (run / "remote" / "SHA256SUMS").write_text(line + line)
    assert rb.verify_transfer(run)[:2] == (False, "transfer-hash")


# ---- markers (plan 5.3 read_markers) ----

@pytest.mark.parametrize("raw", ["0\n", "0", "3\n", "255\n"])
def test_exit_marker_one_line_of_digits(tmp_path, raw):
    run = write_run(tmp_path / "run")
    (run / "remote" / "exit").write_text(raw)
    assert rb.read_markers(run, FETCHED).exit_code == int(raw.strip())


@pytest.mark.parametrize("raw", ["", "x\n", "1\n2\n", "-1\n", "300\n", "1 2\n"])
def test_bad_exit_marker_is_no_exit_marker(tmp_path, raw):
    run = write_run(tmp_path / "run")
    (run / "remote" / "exit").write_text(raw)
    assert rb.read_markers(run, FETCHED).exit_code is None


def test_missing_exit_marker_is_none(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "remote" / "exit").unlink()
    assert rb.read_markers(run, FETCHED).exit_code is None


def test_environment_keys_are_allowlisted_and_missing_is_null(tmp_path):
    run = write_run(tmp_path / "run")
    (run / "remote" / "environment.txt").write_text(
        "system=Linux\nmachine=x86_64; rm -rf /\nruntime=apptainer version 1.2.3 (x)\n"
        "hostname=evil\n"
    )
    m = rb.read_markers(run, FETCHED)
    assert m.system == "Linux"
    assert m.machine is None  # ';' is outside the allowed character set
    assert m.runtime == "apptainer version 1.2.3 (x)"
    empty = write_run(tmp_path / "run2")
    (empty / "remote" / "environment.txt").unlink()
    m2 = rb.read_markers(empty, FETCHED)
    assert (m2.system, m2.machine, m2.runtime) == (None, None, None)


def test_times_parse_and_bad_times_fall_back_to_fetch_time(tmp_path):
    run = write_run(tmp_path / "run")
    m = rb.read_markers(run, FETCHED)
    assert (m.started, m.finished, m.times_from_fetch) == (
        "2026-11-02T10:00:00+00:00", "2026-11-02T10:00:05+00:00", False)
    (run / "remote" / "started").write_text("not a time\n")
    m2 = rb.read_markers(run, FETCHED)
    assert (m2.started, m2.times_from_fetch) == (FETCHED, True)
