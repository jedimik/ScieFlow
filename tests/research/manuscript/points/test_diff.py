"""`scieflow manuscript diff` (M-G 5.9): changes.json, diff.txt and the note, deterministic, synthetic only.

The tiny manuscript below is invented. The diff compares a provenance point with the current source tree (or
with another point); both sides are flattened by ScieFlow (\\input followed) before difflib runs.
"""

from __future__ import annotations

import json
import shutil

import pytest

from scieflow.core import events
from scieflow.research.manuscript import diff as diff_mod
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import cli, point_folder, read_json, sha

TINY_MAIN = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "Alpha line.\n"
    "Beta line.\n"
    "Gamma line.\n"
    "\\end{document}\n"
)


@pytest.fixture
def tiny(synth):
    """A small manuscript (no \\input) with one point `v1` taken from it."""
    synth.ms.joinpath("main.tex").write_text(TINY_MAIN, encoding="utf-8")
    assert cli("point", "add", RUN, "v1").exit_code == 0
    return synth


def diff_folder(synth, name: str):
    return synth.run_dir / "report" / "diff" / name


def run_diff(*args):
    return cli("diff", RUN, *args)


def test_changes_json_lists_added_removed_and_changed(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "Beta changed."), encoding="utf-8")
    tiny.ms.joinpath("added.tex").write_text("New file.\n", encoding="utf-8")
    tiny.ms.joinpath("references.bib").unlink()
    result = run_diff("--from", "v1")
    assert result.exit_code == 0, result.output
    changes = read_json(diff_folder(tiny, "v1..current") / "changes.json")
    assert changes["added"] == ["added.tex"]
    assert changes["removed"] == ["references.bib"]
    assert changes["changed"] == [{
        "path": "main.tex",
        "from_sha256": sha(TINY_MAIN.encode("utf-8")),
        "to_sha256": sha(tiny.ms.joinpath("main.tex").read_bytes()),
    }]


def test_diff_txt_is_exact_for_a_one_line_change(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "Beta changed."), encoding="utf-8")
    assert run_diff("--from", "v1").exit_code == 0
    text = diff_folder(tiny, "v1..current").joinpath("diff.txt").read_text(encoding="utf-8")
    assert text == (
        "--- v1\n"
        "+++ current\n"
        "@@ -1,7 +1,7 @@\n"
        " \\documentclass{article}\n"
        " \\begin{document}\n"
        " Alpha line.\n"
        "-Beta line.\n"
        "+Beta changed.\n"
        " Gamma line.\n"
        " \\end{document}\n"
        " \n"
    )


def test_inputs_are_flattened_on_both_sides(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "\\input{methods}"), encoding="utf-8")
    tiny.ms.joinpath("methods.tex").write_text("Method alpha.\n", encoding="utf-8")
    assert cli("point", "add", RUN, "v2").exit_code == 0
    tiny.ms.joinpath("methods.tex").write_text("Method beta.\n", encoding="utf-8")
    assert run_diff("--from", "v2").exit_code == 0
    text = diff_folder(tiny, "v2..current").joinpath("diff.txt").read_text(encoding="utf-8")
    assert "-Method alpha." in text
    assert "+Method beta." in text
    changes = read_json(diff_folder(tiny, "v2..current") / "changes.json")
    assert [c["path"] for c in changes["changed"]] == ["methods.tex"]


def test_to_a_second_point_names_both_sides(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "Beta later."), encoding="utf-8")
    assert cli("point", "add", RUN, "v2").exit_code == 0
    result = run_diff("--from", "v1", "--to", "v2")
    assert result.exit_code == 0, result.output
    changes = read_json(diff_folder(tiny, "v1..v2") / "changes.json")
    assert [c["path"] for c in changes["changed"]] == ["main.tex"]
    assert diff_folder(tiny, "v1..v2").joinpath("diff.txt").read_text(encoding="utf-8").startswith("--- v1\n+++ v2\n")


def test_to_current_is_the_default(tiny):
    assert run_diff("--from", "v1").exit_code == 0
    assert diff_folder(tiny, "v1..current").is_dir()


def test_an_unknown_label_is_refused_and_nothing_is_written(tiny):
    result = run_diff("--from", "missing")
    assert result.exit_code == 1, result.output
    assert not (tiny.run_dir / "report" / "diff").exists()


def test_a_rerun_is_byte_identical(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "Beta changed."), encoding="utf-8")
    assert run_diff("--from", "v1").exit_code == 0
    folder = diff_folder(tiny, "v1..current")
    first = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    assert run_diff("--from", "v1").exit_code == 0
    second = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    assert first == second


def test_without_latexdiff_the_note_is_given_and_the_exit_is_zero(tiny, monkeypatch):
    monkeypatch.setattr(diff_mod.shutil, "which", lambda name, path=None: None)
    result = run_diff("--from", "v1", "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["notes"] == ["latexdiff-not-installed"]
    assert not diff_folder(tiny, "v1..current").joinpath("diff.tex").exists()
    assert diff_folder(tiny, "v1..current").joinpath("diff.txt").is_file()


def test_the_event_carries_counts_only(tiny):
    tiny.ms.joinpath("main.tex").write_text(TINY_MAIN.replace("Beta line.", "Beta changed."), encoding="utf-8")
    assert run_diff("--from", "v1").exit_code == 0
    data = events.read(tiny.run_dir, types=("note.manuscript_diff",))[-1]["data"]
    assert data["added"] == 0
    assert data["removed"] == 0
    assert data["changed"] == 1
    assert "Beta changed" not in json.dumps(data)


def test_the_point_folder_is_not_modified_by_a_diff(tiny):
    before = sorted((point_folder(tiny, "v1") / "files").rglob("*"))
    assert run_diff("--from", "v1").exit_code == 0
    assert sorted((point_folder(tiny, "v1") / "files").rglob("*")) == before


def test_a_point_folder_that_is_a_symbolic_link_is_refused(tiny, tmp_path):
    folder = point_folder(tiny, "v1")
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(folder, elsewhere)
    shutil.rmtree(folder)
    folder.symlink_to(elsewhere)
    result = run_diff("--from", "v1")
    assert result.exit_code == 1, result.output
    assert not (tiny.run_dir / "report" / "diff").exists()
