"""T-12 t5: descriptor-anchored, bounded control reads (plan 5.6, FG safe-read contract).

Synthetic temporary directories only. Reasons carry category words and relative
names, never file content, so a secret-looking value must never appear in a result.
"""

import os
import threading
from types import SimpleNamespace

import pytest
import yaml

from scieflow.core import control_read as cr

pytestmark = pytest.mark.skipif(not cr.supported(), reason="needs O_NOFOLLOW and dir_fd support")

ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    yield root, fd
    os.close(fd)


def _write(root, name, text):
    (root / name).write_text(text, encoding="utf-8")


def test_regular_mapping_is_ok(run):
    root, fd = run
    _write(root, "status.yml", "run: r1\niteration: 2\n")
    result = cr.read_yaml(fd, "status.yml")
    assert result.status == "ok"
    assert result.data == {"run": "r1", "iteration": 2}


def test_missing_file(run):
    _, fd = run
    assert cr.read_yaml(fd, "status.yml").status == "missing"


def test_symlink_to_inside_and_outside_is_refused(run, tmp_path):
    root, fd = run
    _write(root, "real.yml", "a: 1\n")
    (root / "status.yml").symlink_to(root / "real.yml")
    assert cr.read_yaml(fd, "status.yml").status == "symlink"
    outside = tmp_path / "outside.yml"
    outside.write_text("a: 1\n")
    (root / "config.yml").symlink_to(outside)
    assert cr.read_yaml(fd, "config.yml").status == "symlink"


def test_fifo_returns_promptly_as_not_regular(run):
    root, fd = run
    os.mkfifo(root / "status.yml")
    box = {}

    def go():
        box["result"] = cr.read_yaml(fd, "status.yml")

    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "a FIFO blocked the read"
    assert box["result"].status == "not-regular"


def test_directory_is_not_regular(run):
    root, fd = run
    (root / "status.yml").mkdir()
    assert cr.read_yaml(fd, "status.yml").status == "not-regular"


def test_exactly_the_cap_is_read_and_one_byte_more_is_oversized(run, monkeypatch):
    root, fd = run
    cap = cr.CONTROL_CAP
    exact = "k: " + "v" * (cap - 4) + "\n"
    assert len(exact.encode()) == cap
    _write(root, "status.yml", exact)
    assert cr.read_yaml(fd, "status.yml").status == "ok"

    parsed = []
    real_load = yaml.safe_load

    def spy(text):
        parsed.append(text)
        return real_load(text)

    monkeypatch.setattr(cr.yaml, "safe_load", spy)
    over = exact + "x"
    _write(root, "config.yml", over)
    assert cr.read_yaml(fd, "config.yml").status == "oversized"
    assert parsed == [], "an oversized control must never be parsed"


def test_invalid_utf8_and_invalid_yaml_are_malformed(run):
    root, fd = run
    (root / "status.yml").write_bytes(b"k: \xff\xfe\n")
    assert cr.read_yaml(fd, "status.yml").status == "malformed"
    _write(root, "config.yml", "k: [unclosed\n")
    assert cr.read_yaml(fd, "config.yml").status == "malformed"


def test_a_list_is_invalid_shape(run):
    root, fd = run
    _write(root, "status.yml", "- one\n- two\n")
    assert cr.read_yaml(fd, "status.yml").status == "invalid-shape"


@pytest.mark.skipif(ROOT_USER, reason="root reads a mode-000 file")
def test_mode_000_is_unreadable(run):
    root, fd = run
    _write(root, "status.yml", "a: 1\n")
    (root / "status.yml").chmod(0)
    try:
        assert cr.read_yaml(fd, "status.yml").status == "unreadable"
    finally:
        (root / "status.yml").chmod(0o644)


def test_replacement_between_the_two_fstat_calls_is_changed(run, monkeypatch):
    root, fd = run
    _write(root, "status.yml", "a: 1\n")
    real_fstat = os.fstat
    calls = []

    def fake_fstat(handle):
        st = real_fstat(handle)
        calls.append(handle)
        if len(calls) < 2:
            return st
        return SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev, st_ino=st.st_ino,
                               st_size=st.st_size, st_mtime_ns=st.st_mtime_ns + 1)

    monkeypatch.setattr(cr.os, "fstat", fake_fstat)
    assert cr.read_yaml(fd, "status.yml").status == "changed"


def test_reasons_never_carry_file_content(run):
    root, fd = run
    _write(root, "status.yml", "token: [SECRET-VALUE-123\n")
    _write(root, "config.yml", "- SECRET-LIST-456\n")
    for name in ("status.yml", "config.yml"):
        result = cr.read_yaml(fd, name)
        assert "SECRET" not in repr(result), name
        assert "SECRET" not in result.reason


def test_open_dir_refuses_a_symlinked_component(run):
    root, _ = run
    (root / "real").mkdir()
    (root / "link").symlink_to(root / "real")
    fd = cr.open_dir(root, ("real",))
    os.close(fd)
    with pytest.raises(OSError):
        cr.open_dir(root, ("link",))


def test_stat_entry_names_each_kind(run):
    root, fd = run
    _write(root, "file.yml", "a: 1\n")
    (root / "dir").mkdir()
    (root / "link").symlink_to(root / "file.yml")
    os.mkfifo(root / "fifo")
    assert cr.stat_entry(fd, "file.yml") == "file"
    assert cr.stat_entry(fd, "dir") == "dir"
    assert cr.stat_entry(fd, "link") == "symlink"
    assert cr.stat_entry(fd, "fifo") == "other"
    assert cr.stat_entry(fd, "absent") == "missing"


def test_scan_dir_stops_at_its_limit_without_listing_the_rest(run, monkeypatch):
    root, fd = run
    for i in range(50):
        (root / f"e{i:02d}").mkdir()
    real_scandir = os.scandir
    pulled = []

    class Counting:
        def __init__(self, handle):
            self._it = real_scandir(handle)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

        def __iter__(self):
            for entry in self._it:
                pulled.append(entry.name)
                yield entry

    monkeypatch.setattr(cr.os, "scandir", lambda handle: Counting(handle))
    names, truncated = cr.scan_dir(fd, 10)
    assert len(names) == 10
    assert truncated is True
    assert len(pulled) <= 11, "the scan must stop at its limit, not list the directory"


def test_scan_dir_complete_when_under_the_limit(run):
    root, fd = run
    for i in range(5):
        (root / f"e{i}").mkdir()
    names, truncated = cr.scan_dir(fd, 10)
    assert sorted(names) == [f"e{i}" for i in range(5)]
    assert truncated is False
