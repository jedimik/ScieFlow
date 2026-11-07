"""T-9 t2: zip reading, part 2 — entries, exact member access, bounded chunks (B2:5.6, plan 5.4, t2 items 3-4)."""

from __future__ import annotations

import os
import zipfile

import pytest

from scieflow.core import archives as a
from tests.fixtures.b2_archives import make_project, zip_path
from tests.fixtures.b2_zips import info, put_zip, reset_index, set_flag_bit, symlink_info

SLUG = "r1"


@pytest.fixture(autouse=True)
def fresh_cache():
    reset_index()
    yield
    reset_index()


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


def _mixed(project):
    entries = [
        (info("status.yml"), b"kind: x\n"),
        (info("runs/"), b""),
        (info("runs/a.txt"), b"hello"),
        (info("runs/b/c.csv"), b"1,2"),
        (info("a.txt"), b"aa"),
        (info("z.lock"), b""),
        (symlink_info("link"), b"a.txt"),
        (info("enc.txt"), b"secret-bytes"),
        (info("unsup.bz", compress_type=zipfile.ZIP_BZIP2), b"bz data"),
        (info("../x"), b"x"),
        (info("/abs"), b"x"),
        (info("C:x"), b"x"),
        (info("a/../../x"), b"x"),
    ]
    path = put_zip(project, SLUG, entries)
    set_flag_bit(path, "enc.txt")
    return a.index(project, SLUG)


def test_root_listing_is_dirs_then_files_by_name(project):
    idx = _mixed(project)
    got = a.entries(idx, "", 1)
    names = [(e["name"], e["kind"]) for e in got["entries"]]
    assert names == [
        ("runs", "dir"),
        ("a.txt", "file"),
        ("enc.txt", "encrypted"),
        ("link", "link"),
        ("status.yml", "file"),
        ("unsup.bz", "unsupported"),
    ]


def test_hidden_locks_and_unsafe_names_are_counted_not_listed(project):
    idx = _mixed(project)
    got = a.entries(idx, "", 1)
    assert got["hidden_locks"] == 1
    assert got["unsafe_count"] == 4
    listed = {e["name"] for e in got["entries"]}
    assert "z.lock" not in listed
    assert not listed & {"..", "x", "abs", "C:x", "/abs"}


def test_link_shows_its_target_text(project):
    idx = _mixed(project)
    link = next(e for e in a.entries(idx, "", 1)["entries"] if e["name"] == "link")
    assert link["target"] == "a.txt"


def test_subdirectory_listing_and_implied_parents(project):
    idx = _mixed(project)
    got = a.entries(idx, "runs", 1)
    assert [(e["name"], e["kind"], e["path"]) for e in got["entries"]] == [
        ("b", "dir", "runs/b"),
        ("a.txt", "file", "runs/a.txt"),
    ]
    assert got["total"] == 2


def test_missing_or_traversal_directory_is_empty_with_a_note(project):
    idx = _mixed(project)
    for path in ("nope", "../x", "runs/../.."):
        got = a.entries(idx, path, 1)
        assert got["entries"] == []
        assert got["note"] == "no such directory"


def test_entries_paginate_and_say_so(project):
    names = [(info(f"f{i:04d}.txt"), b"x") for i in range(a.PAGE_ENTRIES + 1)]
    put_zip(project, SLUG, names)
    idx = a.index(project, SLUG)
    first = a.entries(idx, "", 1)
    assert first["pages"] == 2
    assert first["total"] == a.PAGE_ENTRIES + 1
    assert len(first["entries"]) == a.PAGE_ENTRIES
    assert first["note"] == f"entries 1-{a.PAGE_ENTRIES} of {a.PAGE_ENTRIES + 1}"
    second = a.entries(idx, "", 2)
    assert len(second["entries"]) == 1
    assert second["note"] == f"entries {a.PAGE_ENTRIES + 1}-{a.PAGE_ENTRIES + 1} of {a.PAGE_ENTRIES + 1}"


def test_page_out_of_range_is_empty_with_a_note(project):
    idx = _mixed(project)
    got = a.entries(idx, "", 9)
    assert got["entries"] == []
    assert "out of range" in got["note"]


def test_open_member_reads_the_exact_name(project):
    idx = _mixed(project)
    member_info, chunks = a.open_member(idx, "runs/a.txt")
    assert member_info.file_size == 5
    assert b"".join(chunks) == b"hello"


@pytest.mark.parametrize("path", ["./status.yml", "status.yml/", "runs/a", "runs/../status.yml", "missing"])
def test_non_exact_names_are_no_such_member(project, path):
    idx = _mixed(project)
    with pytest.raises(a.ZipRefused) as err:
        a.open_member(idx, path)
    assert err.value.reason == "no-such-member"


@pytest.mark.parametrize("path, kind", [
    ("runs/", "dir"),
    ("link", "link"),
    ("../x", "unsafe"),
    ("enc.txt", "encrypted"),
    ("unsup.bz", "unsupported"),
])
def test_non_file_entries_are_refused(project, path, kind):
    idx = _mixed(project)
    with pytest.raises(a.ZipRefused) as err:
        a.open_member(idx, path)
    assert err.value.reason == kind


def test_chunk_size_is_the_turn_output_chunk():
    from scieflow.core import turn_output
    assert a.CHUNK_BYTES == turn_output.CHUNK_BYTES


def test_members_are_read_in_bounded_chunks(project):
    big = bytes(range(256)) * 600          # 153,600 bytes: more than two chunks
    put_zip(project, SLUG, [(info("big.bin"), big)])
    idx = a.index(project, SLUG)
    _info, chunks = a.open_member(idx, "big.bin")
    parts = list(chunks)
    assert all(len(p) <= a.CHUNK_BYTES for p in parts)
    assert len(parts) >= 3
    assert b"".join(parts) == big


def test_deflated_member_is_read_exactly_to_its_declared_size(project):
    big = b"A" * 300_000
    put_zip(project, SLUG, [(info("big.txt", compress_type=zipfile.ZIP_DEFLATED), big)])
    idx = a.index(project, SLUG)
    member_info, chunks = a.open_member(idx, "big.txt")
    data = b"".join(chunks)
    assert len(data) == member_info.file_size == len(big)


def test_altered_stored_member_is_corrupt(project):
    payload = b"<<PAYLOAD-MARKER>>" + b"z" * 100_000
    path = put_zip(project, SLUG, [(info("data.bin"), payload)])
    raw = bytearray(path.read_bytes())
    pos = raw.find(b"<<PAYLOAD-MARKER>>")
    raw[pos + 2] ^= 0x20                  # one byte of stored data, same length
    path.write_bytes(bytes(raw))
    idx = a.index(project, SLUG)
    _info, chunks = a.open_member(idx, "data.bin")
    with pytest.raises(a.ZipRefused) as err:
        b"".join(chunks)
    assert err.value.reason == "corrupt-member"


def test_entries_writes_nothing(project, tmp_path):
    from tests.fixtures.b2_archives import tree_snapshot
    idx = _mixed(project)
    before = tree_snapshot(project.root)
    a.entries(idx, "", 1)
    a.open_member(idx, "runs/a.txt")
    assert tree_snapshot(project.root) == before
    assert os.path.exists(zip_path(project, SLUG))
