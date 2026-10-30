"""`scieflow manuscript build` with the real TeX toolchain (M-F 5.12, t7). Synthetic article only."""

import hashlib
import json
import shutil

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.t7_support import fresh_article


def _run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs latexmk")
def test_a_planted_latexmkrc_in_the_manuscript_is_not_executed(synth):
    fresh_article(synth)
    (synth.profiles_dir / "journal-of-synthetic-neurology.yml").unlink()
    assert _run("manuscript", "render", RUN, "--class", "article").exit_code == 0
    planted = 'open(F, ">PWNED_BY_LATEXMKRC"); print F "x"; close F;\n'
    (synth.ms / ".latexmkrc").write_text(planted, encoding="utf-8")
    (synth.ms / "latexmkrc").write_text(planted, encoding="utf-8")
    result = _run("manuscript", "build", RUN, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    stage = synth.run_dir / "scratch" / "manuscript-build"
    assert not (stage / "PWNED_BY_LATEXMKRC").exists()
    assert not (synth.ms / "PWNED_BY_LATEXMKRC").exists()
    assert not (synth.ms / "build" / "PWNED_BY_LATEXMKRC").exists()


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs latexmk")
def test_the_article_compiles_and_the_recorded_hash_matches(synth):
    fresh_article(synth)
    (synth.profiles_dir / "journal-of-synthetic-neurology.yml").unlink()
    assert _run("manuscript", "render", RUN, "--class", "article").exit_code == 0
    result = _run("manuscript", "build", RUN, "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    pdf = synth.ms / "build" / "main.pdf"
    assert pdf.is_file()
    assert document["ok"] is True
    assert document["engine"] == "latexmk"
    assert document["pdf_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert not list(synth.ms.glob("*.aux")), "the compile must run in a scratch copy"
