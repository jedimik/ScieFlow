"""T-8 t1: premise tests for facts the B2 design relies on that no existing test pins.

Characterisation tests of today's code (base 63a836b9): they pass now. Their RED evidence is a
mutation of the premise in a throwaway copy, recorded in the t1 result and never committed.
Hermetic: temporary directories only, synthetic data, no network, no real home, no dvc.
"""

import zipfile
from pathlib import Path

import pytest

from scieflow.core import service, workspace
from scieflow.core.project import Project
from sflib import archive

REPO = Path(__file__).resolve().parents[1]
MARKER = b"P7-STORED-MEMBER-BYTES-0123456789"


def make_run(root: Path) -> Path:
    run = root / "run-01"
    (run / "logs").mkdir(parents=True)
    (run / "results").mkdir()
    (run / "status.yml").write_text("phase: done\n")
    (run / "logs" / "agent.log").write_text("transcript\n")
    (run / "results" / "a.txt").write_bytes(MARKER)
    return run


def test_premise_p6_extract_stages_beside_destination_and_leaves_nothing(tmp_path, monkeypatch):
    """Part A 5.10 (interrupted restore): extract_zip stages in a `.<name>.extract-*` directory
    beside the destination, and a failure during extraction leaves neither destination nor staging."""
    zip_path = tmp_path / "run-01.zip"
    archive.build_zip(make_run(tmp_path), zip_path)

    renamed = []
    real_rename = Path.rename

    def spy(self, target):
        renamed.append(Path(self))
        return real_rename(self, target)

    dest = tmp_path / "out" / "run-01"
    monkeypatch.setattr(Path, "rename", spy)
    archive.extract_zip(zip_path, dest)
    monkeypatch.undo()

    staged = [p for p in renamed if p.name.startswith(".run-01.extract-")]
    assert len(staged) == 1
    assert staged[0].parent == dest.parent
    assert (dest / "logs" / "agent.log").read_text() == "transcript\n"

    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    failed = tmp_path / "out" / "run-02"
    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    with pytest.raises(OSError, match="disk full"):
        archive.extract_zip(zip_path, failed)
    assert not failed.exists()
    assert [p.name for p in failed.parent.iterdir() if p.name.startswith(".run-02.extract-")] == []


def test_premise_p7_altered_stored_member_raises_bad_zip_file(tmp_path):
    """Part A 5.2 (T8-D6): every member read checks the member CRC. Stored members are read raw
    from the file, so the CRC is the guard against altered bytes."""
    zip_path = tmp_path / "run-01.zip"
    archive.build_zip(make_run(tmp_path), zip_path)

    raw = bytearray(zip_path.read_bytes())
    raw[raw.index(MARKER)] ^= 0xFF
    zip_path.write_bytes(bytes(raw))

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.getinfo("results/a.txt").compress_type == zipfile.ZIP_STORED
        with pytest.raises(zipfile.BadZipFile):
            zf.read("results/a.txt")
        assert zf.read("logs/agent.log") == b"transcript\n"


def test_premise_p8_pointer_only_slug_is_not_a_run(tmp_path):
    """Part A 5.1 and 5.6 (archived rows come from pointers, never from a run listing): a slug that
    has only workspace/_archives/<slug>.zip.dvc is in neither listing, and `_archives` is not listed."""
    root = tmp_path / "project"
    pointers = root / "workspace" / "_archives"
    pointers.mkdir(parents=True)
    (pointers / "run-01.zip.dvc").write_text("outs:\n- md5: 0123456789abcdef0123456789abcdef\n  size: 1\n")

    assert archive.archived_slugs(root / "workspace") == ["run-01"]
    assert workspace.list_runs(root) == []
    assert service.list_runs_with_budget(Project(root)) == []


def test_premise_p11_restored_run_gets_manuscript_history(tmp_path):
    """Part A 5.10 and 5.7.4 (manuscript history after a restore): a run unpacked from an archive has
    no provenance.git, and opening its manuscript history rebuilds one from the drafts."""
    ws = tmp_path / "src" / "workspace" / "r1"
    round_dir = ws / "manuscript" / "curation" / "rounds" / "1"
    round_dir.mkdir(parents=True)
    (round_dir / "results.tex").write_text("merged\n")
    (ws / "status.yml").write_text("phase: done\n")
    zip_path = tmp_path / "r1.zip"
    archive.build_zip(ws, zip_path)

    fresh = tmp_path / "fresh"
    archive.extract_zip(zip_path, fresh / "workspace" / "r1")
    assert not (fresh / "workspace" / "r1" / "provenance.git").exists()

    view = service.manuscript_history(Project(fresh), "r1")
    assert view["available"] is True
    assert len(view["points"]) >= 1


def test_premise_p12_gitignore_tracks_pointers_and_ignores_local_state():
    """Part A 5.5 and 5.14 (pointers are tracked, zips and machine-local state are not): the
    repository .gitignore keeps the archive pointers, ignores the archive zips, the DVC config and
    the state directory."""
    lines = {line.strip() for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for expected in (
        "!workspace/_archives/",
        "workspace/_archives/*",
        "!workspace/_archives/*.dvc",
        ".dvc/config",
        ".scieflow/",
    ):
        assert expected in lines
