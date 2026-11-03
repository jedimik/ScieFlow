"""`scieflow manuscript point add|list` (M-G 5.9): a copy of the source tree, pinned by sha256.

Synthetic data only. The copy's selection is the one `tex.source_tree_hash` counts (same exclusions), so the
test compares against that function rather than a second copy of its rules.
"""

from __future__ import annotations

import json

import pytest

from scieflow.core import events
from scieflow.research.manuscript import tex
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import MAIN_MARKER, point_folder, read_json, sha, cli

EXCLUDED = {
    "build/main.pdf": "pdf",
    "main.aux": "aux",
    "old.bak-1a2b3c.tex": "stale",
    "drafts/notes.tex": "draft",
    "letters/cover.tex": "letter",
}


def copied_names(folder) -> list:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_add_copies_exactly_the_source_selection(synth):
    synth.write_manuscript(EXCLUDED)
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 0, result.output
    folder = point_folder(synth, "v1")
    record = read_json(folder / "point.json")
    assert copied_names(folder / "files") == sorted(record["files"])
    for name in EXCLUDED:
        assert name not in record["files"], name
    assert "main.tex" in record["files"]


def test_point_json_pins_each_file_and_the_tree(synth):
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 0, result.output
    folder = point_folder(synth, "v1")
    record = read_json(folder / "point.json")
    assert set(record) == {"version", "label", "origin", "tree_sha256", "files"}
    assert record["version"] == 1
    assert record["label"] == "v1"
    assert record["origin"] == "current"
    for rel, digest in record["files"].items():
        assert sha((folder / "files" / rel).read_bytes()) == digest, rel
    assert record["tree_sha256"] == tex.source_tree_hash(synth.ms)
    assert tex.source_tree_hash(folder / "files") == record["tree_sha256"]


def test_two_points_of_one_tree_differ_only_in_label(synth):
    assert cli("point", "add", RUN, "v1").exit_code == 0
    assert cli("point", "add", RUN, "v2").exit_code == 0
    first = read_json(point_folder(synth, "v1") / "point.json")
    second = read_json(point_folder(synth, "v2") / "point.json")
    first.pop("label")
    second.pop("label")
    assert first == second


@pytest.mark.parametrize("label", ["Bad", "../x", "a/b", "-a", "a" * 41, "a_b", "a b", ""])
def test_bad_labels_are_refused_before_any_write(synth, label):
    # "--" ends the options, so a label that starts with "-" reaches the label check, not click's parser
    result = cli("point", "add", RUN, "--", label)
    assert result.exit_code == 1, result.output
    assert not (synth.run_dir / "revision").exists()


@pytest.mark.parametrize("label", ["v1", "1", "rev-2.a", "a" * 40])
def test_good_labels_are_accepted(synth, label):
    result = cli("point", "add", RUN, label)
    assert result.exit_code == 0, result.output
    assert point_folder(synth, label).is_dir()


def test_existing_label_is_refused_and_left_alone(synth):
    assert cli("point", "add", RUN, "v1").exit_code == 0
    before = (point_folder(synth, "v1") / "point.json").read_bytes()
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 1, result.output
    assert "exists" in result.output
    assert (point_folder(synth, "v1") / "point.json").read_bytes() == before


def test_symlink_in_the_tree_is_refused(synth):
    (synth.ms / "alias.tex").symlink_to(synth.ms / "main.tex")
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output
    assert not point_folder(synth, "v1").exists()


def test_symlinked_folder_is_refused(synth):
    (synth.ms / "linked").symlink_to(synth.root)
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 1, result.output
    assert "[symlink]" in result.output
    assert "linked is a symbolic link; refused" in result.output
    assert not point_folder(synth, "v1").exists()


def test_exactly_200_files_is_accepted_and_201_is_refused(synth):
    base = sum(1 for p in synth.ms.rglob("*") if p.is_file())
    extra = synth.ms / "extra"
    extra.mkdir()
    for index in range(200 - base):
        (extra / f"f{index:03d}.txt").write_text("synthetic\n", encoding="utf-8")
    assert cli("point", "add", RUN, "at-limit").exit_code == 0
    (extra / "f-over.txt").write_text("synthetic\n", encoding="utf-8")
    result = cli("point", "add", RUN, "over")
    assert result.exit_code == 1, result.output
    assert "[point-limit]" in result.output
    assert "more than 200 files in the source tree" in result.output
    assert not point_folder(synth, "over").exists()


def test_more_than_50_mb_is_refused(synth):
    (synth.ms / "big.bin").write_bytes(b"\0" * (50 * 1024 * 1024 + 1))
    result = cli("point", "add", RUN, "v1")
    assert result.exit_code == 1, result.output
    assert "[point-limit]" in result.output
    assert "the source tree is over" in result.output
    assert not point_folder(synth, "v1").exists()


def test_add_emits_a_counted_event_without_manuscript_text(synth):
    assert cli("point", "add", RUN, "v1").exit_code == 0
    notes = events.read(synth.run_dir, types=("note.manuscript_point",))
    assert len(notes) == 1
    data = notes[0]["data"]
    assert data["label"] == "v1"
    assert data["origin"] == "current"
    assert data["files"] == len(read_json(point_folder(synth, "v1") / "point.json")["files"])
    assert MAIN_MARKER not in json.dumps(notes)


def test_add_without_from_package_is_allowed_inside_a_dispatch(synth, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    assert cli("point", "add", RUN, "v1").exit_code == 0


def test_list_is_sorted_and_reports_origin(synth):
    assert cli("point", "add", RUN, "v2").exit_code == 0
    assert cli("point", "add", RUN, "v1").exit_code == 0
    result = cli("point", "list", RUN, "--json")
    assert result.exit_code == 0, result.output
    records = json.loads(result.output)
    assert [r["label"] for r in records] == ["v1", "v2"]
    assert {r["origin"] for r in records} == {"current"}


def test_list_with_no_points_prints_nothing_and_succeeds(synth):
    result = cli("point", "list", RUN)
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""
