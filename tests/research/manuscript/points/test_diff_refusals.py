"""Refusals of `scieflow manuscript diff` (M-G 5.9, 5.16): an edited or extended point, --from current, and a
symbolic link in the current tree. Each refusal names its rule and writes no report. Synthetic data only.
"""

from __future__ import annotations

import pytest

from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import cli, point_folder

TINY_MAIN = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "Alpha line.\n"
    "\\end{document}\n"
)


@pytest.fixture
def tiny(synth):
    synth.ms.joinpath("main.tex").write_text(TINY_MAIN, encoding="utf-8")
    assert cli("point", "add", RUN, "v1").exit_code == 0
    return synth


def test_an_edited_point_file_is_refused(tiny):
    (point_folder(tiny, "v1") / "files" / "main.tex").write_text("edited after pinning\n", encoding="utf-8")
    result = cli("diff", RUN, "--from", "v1")
    assert result.exit_code == 1, result.output
    assert "diff refused [point-tampered]" in result.output
    assert not (tiny.run_dir / "report" / "diff").exists()


def test_an_added_file_in_a_point_is_refused(tiny):
    (point_folder(tiny, "v1") / "files" / "extra.tex").write_text("added after pinning\n", encoding="utf-8")
    result = cli("diff", RUN, "--from", "v1")
    assert result.exit_code == 1, result.output
    assert "diff refused [point-tampered]" in result.output
    assert not (tiny.run_dir / "report" / "diff").exists()


def test_from_current_is_refused(tiny):
    result = cli("diff", RUN, "--from", "current")
    assert result.exit_code == 1, result.output
    assert "diff refused [from-current]" in result.output
    assert not (tiny.run_dir / "report" / "diff").exists()


def test_a_symbolic_link_in_the_current_tree_is_refused_for_to_current(tiny):
    (tiny.ms / "alias.tex").symlink_to(tiny.ms / "main.tex")
    result = cli("diff", RUN, "--from", "v1")
    assert result.exit_code == 1, result.output
    assert "diff refused [symlink]" in result.output
    assert "alias.tex is a symbolic link" in result.output
    assert not (tiny.run_dir / "report" / "diff").exists()
