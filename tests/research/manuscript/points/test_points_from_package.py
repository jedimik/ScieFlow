"""`scieflow manuscript point add --from-package` (M-G 5.9, 5.16): the source.zip of a package, pinned.

Synthetic data only. Every archive here is built by point_support.zip_bytes. The refusals cover the digest
pin, unsafe names, symbolic links, the entry and size limits, a missing package and a dispatch. Each refusal
asserts its rule-specific message, so a refusal for the wrong reason fails the test.
"""

from __future__ import annotations

import pytest

from scieflow.core import events
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import (
    cli, make_package, point_folder, read_json, replace_source_zip, sha, zip_bytes,
)
from tests.research.manuscript.t8_support import submission


@pytest.fixture
def packaged(synth):
    make_package(synth)
    return synth


def test_from_package_extracts_the_source_zip(packaged):
    sub = submission(packaged)
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 0, result.output
    folder = point_folder(packaged, "pkg")
    record = read_json(folder / "point.json")
    assert record["origin"] == "package"
    assert record["package_sha256"] == sha((sub / "source.zip").read_bytes())
    assert (folder / "files" / "main.tex").read_bytes() == (packaged.ms / "main.tex").read_bytes()
    for rel, digest in record["files"].items():
        assert sha((folder / "files" / rel).read_bytes()) == digest, rel


def test_from_package_emits_the_origin_event(packaged):
    assert cli("point", "add", RUN, "pkg", "--from-package").exit_code == 0
    data = events.read(packaged.run_dir, types=("note.manuscript_point",))[-1]["data"]
    assert data["label"] == "pkg"
    assert data["origin"] == "package"


def test_a_tampered_zip_is_refused_by_the_digest_pin_alone(packaged):
    # A VALID archive with other contents, package.json left as it was: only the sha256 pin can refuse it.
    sub = submission(packaged)
    (sub / "source.zip").write_bytes(zip_bytes([("main.tex", b"x\n")]))
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[package-digest]" in result.output
    assert "does not match the digest in package.json" in result.output
    assert not point_folder(packaged, "pkg").exists()


@pytest.mark.parametrize("name", ["../x.tex", "/x.tex", "a\\b.tex", "ok/../../x.tex", "./x.tex", "dir//x.tex"])
def test_unsafe_entry_names_are_refused_before_any_write(packaged, name):
    replace_source_zip(packaged, zip_bytes([("main.tex", b"x\n"), (name, b"y\n")]))
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[zip-name]" in result.output
    assert "is not a safe relative path" in result.output
    assert not point_folder(packaged, "pkg").exists()
    assert not (packaged.run_dir / "x.tex").exists()
    assert not (packaged.run_dir / "revision" / "x.tex").exists()


def test_a_symbolic_link_entry_is_refused(packaged):
    replace_source_zip(packaged, zip_bytes([("main.tex", b"x\n"), ("alias.tex", b"main.tex", 0o120777)]))
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[symlink]" in result.output
    assert "is a symbolic link entry; refused" in result.output
    assert not point_folder(packaged, "pkg").exists()


def test_more_than_200_entries_are_refused(packaged):
    entries = [(f"f{index:03d}.tex", b"x\n") for index in range(201)]
    replace_source_zip(packaged, zip_bytes(entries))
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[point-limit]" in result.output
    assert "more than 200 entries in source.zip" in result.output
    assert not point_folder(packaged, "pkg").exists()


def test_more_than_50_mb_of_entries_is_refused(packaged):
    replace_source_zip(packaged, zip_bytes([("big.bin", b"\0" * (50 * 1024 * 1024 + 1))]))
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[point-limit]" in result.output
    assert "the entries of source.zip are over" in result.output
    assert not point_folder(packaged, "pkg").exists()


def test_a_symbolic_source_zip_is_refused(packaged):
    sub = submission(packaged)
    elsewhere = packaged.root / "elsewhere.zip"
    elsewhere.write_bytes((sub / "source.zip").read_bytes())
    (sub / "source.zip").unlink()
    (sub / "source.zip").symlink_to(elsewhere)
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[symlink]" in result.output
    assert "source.zip is a symbolic link; refused" in result.output
    assert not point_folder(packaged, "pkg").exists()


def test_a_missing_package_is_refused(synth):
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "[package-missing]" in result.output
    assert "no report/submission folder" in result.output
    assert not point_folder(synth, "pkg").exists()


def test_from_package_is_refused_inside_a_dispatch(packaged, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = cli("point", "add", RUN, "pkg", "--from-package")
    assert result.exit_code == 1, result.output
    assert "dispatch" in result.output
    assert not point_folder(packaged, "pkg").exists()
