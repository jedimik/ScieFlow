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


def test_symlinks_are_stored_as_links_never_followed(tmp_path):
    run = make_run(tmp_path)
    data = tmp_path / "big-data"
    data.mkdir()
    (data / "huge.bin").write_bytes(b"x" * 1000)
    (run / "Data").symlink_to(data)                    # absolute, outside the run
    (run / "latest").symlink_to("results")             # relative, inside the run
    (run / "results").mkdir(exist_ok=True)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "Data" in names and not any(n.startswith("Data/") for n in names)
        assert zf.read("Data").decode() == str(data)

    out = tmp_path / "out"
    archive.extract_zip(dest, out)
    assert os.readlink(out / "Data") == str(data)      # absolute kept verbatim
    assert os.readlink(out / "latest") == "results"


def test_relative_link_escaping_the_archive_is_refused(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        info = zipfile.ZipInfo("escape")
        info.create_system = 3
        info.external_attr = archive._LINK_MODE
        zf.writestr(info, "../../etc")
    with pytest.raises(archive.ArchiveError, match="escapes the archive"):
        archive.extract_zip(evil, tmp_path / "out")


@pytest.mark.parametrize(
    "junk",
    ["tmp/a.txt", ".snakemake/log", "pytest-01/t.py", "runtime/host/bin/python",
     "scratch/clone/x", "sub/.uv-cache/x", "mpl-cache/f"],
)
def test_rebuildable_dirs_are_skipped(tmp_path, junk):
    run = make_run(tmp_path)
    path = run / junk
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("junk")
    (run / "runtime" / "keep.txt").parent.mkdir(parents=True, exist_ok=True)
    (run / "runtime" / "keep.txt").write_text("kept")
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert junk not in names
    assert "runtime/keep.txt" in names   # only runtime/host is a conda prefix


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


# --- disk preflight ----------------------------------------------------------

def test_ensure_space_refuses_with_both_figures(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 254 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=100 * 1024**3))
    with pytest.raises(archive.ArchiveError, match=r"needs 266\.7G, 100\.0G free"):
        archive.ensure_space(run, tmp_path / "workspace" / "_archives")


def test_ensure_space_passes_with_headroom(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 10 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=11 * 1024**3))
    archive.ensure_space(run, tmp_path / "does" / "not" / "exist")


# --- extract -----------------------------------------------------------------

def test_extract_zip_refuses_non_empty_dest_without_force(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    target = tmp_path / "target"
    target.mkdir()
    (target / "local-only.txt").write_text("keep me")

    with pytest.raises(archive.ArchiveError, match="--force"):
        archive.extract_zip(dest_zip, target)
    assert (target / "local-only.txt").read_text() == "keep me"


def test_extract_zip_force_replaces_dest(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    target = tmp_path / "target"
    target.mkdir()
    (target / "local-only.txt").write_text("stale")

    archive.extract_zip(dest_zip, target, force=True)
    assert not (target / "local-only.txt").exists()
    assert (target / "status.yml").read_text() == "phase: done\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_extract_zip_into_empty_existing_dir_needs_no_force(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    (tmp_path / "target").mkdir()
    archive.extract_zip(dest_zip, tmp_path / "target")
    assert (tmp_path / "target" / "status.yml").exists()


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil.txt", "C:evil.txt"])
def test_extract_zip_rejects_unsafe_member_and_writes_nothing(tmp_path, name):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr(name, "pwned")
    target = tmp_path / "area" / "target"

    with pytest.raises(archive.ArchiveError, match="unsafe member"):
        archive.extract_zip(bad, target)
    assert not target.exists()
    assert not (tmp_path / "area" / "evil.txt").exists()
    assert list((tmp_path / "area").iterdir()) == []


# --- cache hardlink ----------------------------------------------------------

def fake_cache(tmp_path: Path, body: bytes, md5: str = "4d3431208b9bf81bdaa193b71a99af1c"):
    cache = tmp_path / "cache"
    obj = cache / "files" / "md5" / md5[:2] / md5[2:]
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    pointer = tmp_path / "run-01.zip.dvc"
    pointer.write_text(f"outs:\n- md5: {md5}\n  size: {len(body)}\n  hash: md5\n  path: run-01.zip\n")
    zip_path = tmp_path / "run-01.zip"
    zip_path.write_bytes(body)
    return cache, obj, pointer, zip_path


def test_link_to_cache_hardlinks_zip_to_cache_object(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    assert archive.link_to_cache(zip_path, pointer, cache) is True
    assert os.path.samefile(zip_path, obj)
    assert zip_path.read_bytes() == b"archive body"
    assert archive.link_to_cache(zip_path, pointer, cache) is True


def test_link_to_cache_missing_object_leaves_copy(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    obj.unlink()
    assert archive.link_to_cache(zip_path, pointer, cache) is False
    assert zip_path.read_bytes() == b"archive body"


def test_link_to_cache_size_mismatch_leaves_copy(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    obj.write_bytes(b"something else entirely")
    assert archive.link_to_cache(zip_path, pointer, cache) is False
    assert not os.path.samefile(zip_path, obj)


def test_link_to_cache_ignores_directory_hash(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    pointer.write_text("outs:\n- md5: 68c1d3127031576cf055cb445c10fb32.dir\n  path: run-01\n")
    assert archive.link_to_cache(zip_path, pointer, cache) is False
