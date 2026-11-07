"""T-9 t2: zip reading, part 1 — pre-check, index and LRU (B2:5.6, plan 5.4, t2 items 1-2)."""

from __future__ import annotations

import os
import struct
import threading
import zipfile

import pytest

from scieflow.core import archives as a
from tests.fixtures.b2_archives import make_project, tree_snapshot
from tests.fixtures.b2_zips import info, put_zip, reset_index, write_zip
from tests.fixtures.b2_archives import zip_path

SLUG = "r1"
EOCD = b"PK\x05\x06"
ZIP64_REC = b"PK\x06\x06"


@pytest.fixture(autouse=True)
def fresh_cache():
    reset_index()
    yield
    reset_index()


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


@pytest.fixture
def zipcalls(monkeypatch):
    """Count the zipfile.ZipFile constructions made in read mode by the archive reader."""
    calls = []
    real = zipfile.ZipFile

    class Spy(real):
        def __init__(self, *args, **kwargs):
            mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if mode == "r":
                calls.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", Spy)
    return calls


def _precheck(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        return a.precheck_central_directory(fd, os.fstat(fd).st_size)
    finally:
        os.close(fd)


def _zip64_record_offset(data: bytes) -> int:
    pos = data.find(ZIP64_REC)
    assert pos >= 0, "fixture must be a zip64 archive"
    return pos


def test_plain_zip_precheck_reports_entries_and_cd_size(project):
    path = put_zip(project, SLUG, [("a.txt", b"aa"), ("b.txt", b"bb")])
    entries, cd_bytes = _precheck(path)
    assert entries == 2
    assert 0 < cd_bytes < os.path.getsize(path)


def test_zip64_end_record_is_followed(project, monkeypatch):
    # A small file-count limit makes zipfile write the zip64 end-of-central-directory record.
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)
    path = put_zip(project, SLUG, [("a", b"1"), ("b", b"2"), ("c", b"3")])
    assert ZIP64_REC in path.read_bytes()
    entries, _cd = _precheck(path)
    assert entries == 3


def test_too_many_entries_is_refused_before_zipfile_parses(project, monkeypatch, zipcalls):
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)
    path = put_zip(project, SLUG, [("a", b"1"), ("b", b"2"), ("c", b"3")])
    data = bytearray(path.read_bytes())
    eocd = data.rfind(EOCD)
    rec = _zip64_record_offset(bytes(data))
    # saturate the EOCD entry count so the zip64 record is the one that counts
    struct.pack_into("<H", data, eocd + 10, 0xFFFF)
    # zip64 record: total entries (8 bytes) at +32
    data[rec + 32:rec + 40] = (a.MAX_ARCHIVE_ENTRIES + 1).to_bytes(8, "little")
    path.write_bytes(bytes(data))
    zipcalls.clear()
    with pytest.raises(a.ZipRefused) as err:
        a.index(project, SLUG)
    assert err.value.reason == "too-many-entries"
    assert zipcalls == []


def test_oversized_central_directory_is_refused_before_zipfile_parses(project, monkeypatch, zipcalls):
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)
    path = put_zip(project, SLUG, [("a", b"1"), ("b", b"2"), ("c", b"3")])
    data = bytearray(path.read_bytes())
    eocd = data.rfind(EOCD)
    rec = _zip64_record_offset(bytes(data))
    # saturate the EOCD central-directory size so the zip64 record is the one that counts
    struct.pack_into("<I", data, eocd + 12, 0xFFFFFFFF)
    # zip64 record: central directory size (8 bytes) at +40
    data[rec + 40:rec + 48] = (a.MAX_CENTRAL_DIRECTORY_BYTES + 1).to_bytes(8, "little")
    path.write_bytes(bytes(data))
    zipcalls.clear()
    with pytest.raises(a.ZipRefused) as err:
        a.index(project, SLUG)
    assert err.value.reason == "too-many-entries"
    assert zipcalls == []


def test_garbage_is_unreadable_zip(project, zipcalls):
    zip_path(project, SLUG).write_bytes(b"this is not a zip archive at all")
    with pytest.raises(a.ZipRefused) as err:
        a.index(project, SLUG)
    assert err.value.reason == "unreadable-zip"
    assert zipcalls == []


def test_precheck_reads_only_the_tail(project, monkeypatch):
    path = put_zip(project, SLUG, [(f"f{i}", b"x" * 50) for i in range(40)])
    reads = []
    real = os.pread

    def spy(fd, n, offset):
        reads.append((n, offset))
        return real(fd, n, offset)

    monkeypatch.setattr(os, "pread", spy)
    _precheck(path)
    size = os.path.getsize(path)
    assert reads, "the pre-check reads the end records"
    assert sum(n for n, _ in reads) <= a.EOCD_SEARCH_BYTES + 56
    assert min(offset for _, offset in reads) >= size - a.EOCD_SEARCH_BYTES


def test_index_is_one_zipfile_per_stat_key(project, zipcalls):
    path = put_zip(project, SLUG, [("a.txt", b"aa")])
    first = a.index(project, SLUG)
    second = a.index(project, SLUG)
    assert first is second
    assert len(zipcalls) == 1
    assert os.path.exists(path)


def test_changed_file_is_parsed_again(project, zipcalls):
    path = put_zip(project, SLUG, [("a.txt", b"aa")])
    a.index(project, SLUG)
    write_zip(path, [("a.txt", b"bbbbbb")])
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    a.index(project, SLUG)
    assert len(zipcalls) == 2


def test_lru_keeps_four_and_evicts_the_oldest(project, zipcalls):
    slugs = [f"s{i}" for i in range(1, 6)]
    for slug in slugs:
        put_zip(project, slug, [("a.txt", slug.encode())])
    for slug in slugs:
        a.index(project, slug)
    assert len(zipcalls) == 5
    assert len(a._INDEX_LRU) == a.INDEX_CACHE
    for slug in slugs[1:]:
        a.index(project, slug)           # four hits, nothing re-parsed
    assert len(zipcalls) == 5
    a.index(project, "s1")               # the oldest was evicted
    assert len(zipcalls) == 6


def test_concurrent_index_parses_once(project, zipcalls):
    put_zip(project, SLUG, [("a.txt", b"aa")])
    results = []
    errors = []

    def worker():
        try:
            results.append(a.index(project, SLUG).key)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(results)) == 1
    assert len(zipcalls) == 1


def test_symlink_into_the_dvc_cache_is_read(project):
    cached = project.root / ".dvc" / "cache" / "01" / "rest"
    write_zip(cached, [("status.yml", b"kind: x\n")])
    link = zip_path(project, SLUG)
    os.symlink(cached, link)
    idx = a.index(project, SLUG)
    assert "status.yml" in idx.members


def test_symlink_outside_the_cache_is_refused(project, tmp_path):
    outside = tmp_path / "elsewhere.zip"
    write_zip(outside, [("status.yml", b"kind: x\n")])
    os.symlink(outside, zip_path(project, SLUG))
    with pytest.raises(a.ZipRefused) as err:
        a.index(project, SLUG)
    assert err.value.reason == "link-outside-cache"


def test_missing_and_not_regular(project):
    with pytest.raises(a.ZipRefused) as missing:
        a.index(project, SLUG)
    assert missing.value.reason == "missing"
    os.mkfifo(zip_path(project, SLUG))
    with pytest.raises(a.ZipRefused) as fifo:
        a.index(project, SLUG)
    assert fifo.value.reason == "not-regular"


def test_index_writes_nothing(project):
    put_zip(project, SLUG, [("a.txt", b"aa")])
    before = tree_snapshot(project.root)
    a.index(project, SLUG)
    assert tree_snapshot(project.root) == before
    assert not os.path.exists(project.state_dir / "archives")


def test_info_helper_keeps_names_exact(project):
    # the fixtures write exactly the names they are given (unsafe names included)
    path = write_zip(zip_path(project, SLUG), [(info("../x"), b"x")])
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == ["../x"]
