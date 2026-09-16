import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sflib import archive


def make_run(root: Path) -> Path:
    run = root / "run-01"
    (run / "logs").mkdir(parents=True)
    (run / "results" / "nested").mkdir(parents=True)
    (run / "empty").mkdir()
    (run / "__pycache__").mkdir()
    (run / "status.yml").write_text("phase: done\n")
    (run / "logs" / "agent.log").write_text("transcript\n")
    (run / "results" / "nested" / "data.bin").write_bytes(os.urandom(4096))
    (run / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"cache")
    (run / "stray.pyc").write_bytes(b"cache")
    return run


def tree(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


# --- paths -------------------------------------------------------------------

def test_archive_and_pointer_paths(tmp_path):
    ws = tmp_path / "workspace"
    assert archive.archive_path(ws, "run-01") == ws / "_archives" / "run-01.zip"
    assert archive.pointer_path(ws, "run-01") == ws / "_archives" / "run-01.zip.dvc"


def test_archived_slugs_lists_pointers_only(tmp_path):
    ws = tmp_path / "workspace"
    assert archive.archived_slugs(ws) == []
    (ws / "_archives").mkdir(parents=True)
    (ws / "_archives" / "b-run.zip.dvc").write_text("outs: []\n")
    (ws / "_archives" / "a-run.zip.dvc").write_text("outs: []\n")
    (ws / "_archives" / "a-run.zip").write_bytes(b"zip body")
    assert archive.archived_slugs(ws) == ["a-run", "b-run"]


# --- build / verify ----------------------------------------------------------

def test_build_zip_round_trip_skips_cache_keeps_logs(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "out" / "run-01.zip"

    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        zf.extractall(tmp_path / "restored")

    expected = {k: v for k, v in tree(run).items()
                if "__pycache__" not in k and not k.endswith(".pyc")}
    assert tree(tmp_path / "restored") == expected
    assert "logs/agent.log" in expected
    assert "empty" in expected


def test_build_zip_members_are_stored_uncompressed(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}


def test_build_zip_is_deterministic(tmp_path):
    run = make_run(tmp_path)
    archive.build_zip(run, tmp_path / "a.zip")
    archive.build_zip(run, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_build_zip_refuses_symlinks_and_names_them(tmp_path):
    run = make_run(tmp_path)
    (run / "link-to-data").symlink_to(tmp_path)
    with pytest.raises(archive.ArchiveError, match="link-to-data"):
        archive.build_zip(run, tmp_path / "run-01.zip")
    assert not (tmp_path / "run-01.zip").exists()


def test_build_zip_failure_leaves_no_zip_and_no_partial(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"

    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "write", boom)
    with pytest.raises(OSError, match="disk full"):
        archive.build_zip(run, dest)
    assert not dest.exists()
    assert not dest.with_name("run-01.zip.partial").exists()


def test_workspace_size_counts_archived_bytes_only(tmp_path):
    run = make_run(tmp_path)
    expected = len("phase: done\n") + len("transcript\n") + 4096
    assert archive.workspace_size(run) == expected


def test_verify_zip_returns_member_count(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        assert archive.verify_zip(dest) == len(zf.infolist())


def test_verify_zip_rejects_truncated_file(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    dest.write_bytes(dest.read_bytes()[:100])
    with pytest.raises(archive.ArchiveError, match="not a readable zip"):
        archive.verify_zip(dest)
