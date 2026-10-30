"""Deterministic source archive (M-F 5.13): ziputil and the source.zip of a package. Synthetic data only."""

from __future__ import annotations

import io
import zipfile

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import ziputil
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.t8_support import make_build


def zip_names(data: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [info.filename for info in archive.infolist()]


def test_the_archive_does_not_depend_on_insertion_order():
    one = ziputil.zip_bytes({"b.tex": b"B", "a.tex": b"A", "sub/c.bib": b"C"})
    two = ziputil.zip_bytes({"sub/c.bib": b"C", "a.tex": b"A", "b.tex": b"B"})
    assert one == two


def test_every_entry_has_a_fixed_date_mode_and_deflate():
    data = ziputil.zip_bytes({"b.tex": b"B" * 100, "a.tex": b"A"})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
    assert [info.filename for info in infos] == ["a.tex", "b.tex"]
    for info in infos:
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert (info.external_attr >> 16) & 0o777 == 0o644
        assert info.compress_type == zipfile.ZIP_DEFLATED


@pytest.mark.parametrize("name", ["../escape.tex", "/abs.tex", "a/../../b.tex", ""])
def test_unsafe_names_are_refused(name):
    with pytest.raises(ValueError):
        ziputil.zip_bytes({name: b"x"})


def test_the_source_archive_holds_the_source_tree_and_the_bbl_only(synth):
    (synth.ms / "letters").mkdir()
    (synth.ms / "letters" / "cover-letter.tex").write_text("letter\n", encoding="utf-8")
    (synth.ms / "drafts").mkdir()
    (synth.ms / "drafts" / "old.tex").write_text("draft\n", encoding="utf-8")
    (synth.ms / "main.aux").write_text("aux by-product\n", encoding="utf-8")
    (synth.ms / "methods.tex.bak-abc123").write_text("backup\n", encoding="utf-8")
    make_build(synth)
    (synth.ms / "build" / "main.bbl").write_text("\\bibitem{smith2001}\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["manuscript", "package", RUN], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    names = zip_names((synth.run_dir / "report" / "submission" / "source.zip").read_bytes())
    assert names == sorted(names)
    assert {"main.tex", "methods.tex", "references.bib", "metadata.yml", "build/main.bbl"} <= set(names)
    for excluded in ("letters/cover-letter.tex", "drafts/old.tex", "main.aux",
                     "methods.tex.bak-abc123", "build/main.pdf", "build/build.json"):
        assert excluded not in names, excluded


def test_two_packages_give_byte_identical_archives(synth):
    make_build(synth)

    def package_bytes():
        result = CliRunner().invoke(main, ["manuscript", "package", RUN], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        return (synth.run_dir / "report" / "submission" / "source.zip").read_bytes()

    first = package_bytes()
    assert package_bytes() == first
