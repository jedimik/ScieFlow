"""M-G t5 fix (review r1): symbolic links on the revision path are refused, draft text is capped, and the
letter/draft rules not yet covered by a test are exercised (M-G 5.10, 5.11, 5.16).

Every test here uses synthetic letters and drafts only. A refused command writes nothing outside the run:
the link targets are snapshotted before and compared after.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.research.manuscript.conftest import RUN
from tests.research.revision.revision_support import (
    LETTER, LETTER_HTML, cli, ingest, inbox, valid_draft, write_draft, adopt,
)


def snapshot(folder: Path) -> list:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def move_outside(source: Path, tmp_path: Path, name: str) -> Path:
    """Move `source` to tmp_path/name and leave a symbolic link at `source` pointing there."""
    target = tmp_path / name
    shutil.move(str(source), str(target))
    source.symlink_to(target, target_is_directory=True)
    return target


def round_folder(synth, n: int = 1) -> Path:
    return synth.run_dir / "revision" / f"round-{n}"


def inbox_draft(synth, draft: dict) -> Path:
    """A draft placed inside the run but outside revision/, so only the link under test is in the path."""
    path = synth.run_dir / "inbox" / "draft.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- symbolic links on the revision path (finding 1) -------------------------------------------------------

def test_adopt_refuses_a_symlinked_round_folder_and_writes_nothing_through_it(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    target = move_outside(round_folder(synth), tmp_path, "elsewhere-round")
    before = snapshot(target)
    result = adopt(synth, inbox_draft(synth, valid_draft()))
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert snapshot(target) == before


def test_adopt_refuses_a_symlinked_revision_folder(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    target = move_outside(synth.run_dir / "revision", tmp_path, "elsewhere-revision")
    before = snapshot(target)
    result = adopt(synth, inbox_draft(synth, valid_draft()))
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert snapshot(target) == before


def test_adopt_refuses_a_symlinked_letter_even_when_its_bytes_match(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    copy = tmp_path / "letter-copy.txt"
    letter = round_folder(synth) / "letter.txt"
    shutil.copy(letter, copy)
    letter.unlink()
    letter.symlink_to(copy)
    result = adopt(synth, inbox_draft(synth, valid_draft()))
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert not (round_folder(synth) / "adopted.json").exists()
    assert not (round_folder(synth) / "reviews").exists()


def test_adopt_refuses_a_symlinked_reviews_folder(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    target = tmp_path / "elsewhere-reviews"
    target.mkdir()
    (round_folder(synth) / "reviews").symlink_to(target, target_is_directory=True)
    result = adopt(synth, inbox_draft(synth, valid_draft()))
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert snapshot(target) == []
    assert not (round_folder(synth) / "adopted.json").exists()


def test_adopt_refuses_a_symlinked_review_file_before_writing_any_review(synth):
    assert ingest(synth).exit_code == 0
    outside = synth.run_dir.parent / "outside-review.json"
    outside.write_text("{}\n", encoding="utf-8")
    (round_folder(synth) / "reviews").mkdir(parents=True)
    (round_folder(synth) / "reviews" / "R2.json").symlink_to(outside)
    result = adopt(synth, inbox_draft(synth, valid_draft()))
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert outside.read_text(encoding="utf-8") == "{}\n"
    assert not (round_folder(synth) / "reviews" / "E.json").exists()
    assert not (round_folder(synth) / "reviews" / "R1.json").exists()


def test_ingest_refuses_a_symlinked_revision_folder(synth, tmp_path):
    target = tmp_path / "elsewhere-revision"
    target.mkdir()
    (synth.run_dir / "revision").symlink_to(target, target_is_directory=True)
    result = ingest(synth)
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert snapshot(target) == []


def test_ingest_refuses_a_symlinked_revision_even_with_an_existing_point(synth, tmp_path):
    (synth.run_dir / "revision" / "points" / "submitted-r1").mkdir(parents=True)
    target = move_outside(synth.run_dir / "revision", tmp_path, "elsewhere-revision")
    before = snapshot(target)
    result = ingest(synth)
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert snapshot(target) == before


def test_show_refuses_a_symlinked_round_folder(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    move_outside(round_folder(synth), tmp_path, "elsewhere-round")
    result = cli("show", RUN)
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output


def test_prompt_refuses_a_symlinked_round_folder(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    move_outside(round_folder(synth), tmp_path, "elsewhere-round")
    result = cli("prompt", RUN, "--round", "1", "--agent", "codex-paper")
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output


# --- draft-supplied text is capped in messages (finding 2) -------------------------------------------------

FLAT_LETTER = " ".join(LETTER.split())


def test_an_unknown_not_comment_paragraph_is_capped_in_the_message(synth):
    assert ingest(synth).exit_code == 0
    draft = valid_draft()
    draft["not_comments"].append({"paragraph": FLAT_LETTER[:400], "reason": "greeting line"})
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 1, result.output
    assert "not-comment-invalid" in result.output
    assert FLAT_LETTER[:61] not in result.output


def test_an_extra_block_name_is_capped_in_the_blocks_differ_message(synth):
    assert ingest(synth).exit_code == 0
    draft = valid_draft()
    draft["reviews"].append({"block": FLAT_LETTER[:400], "review": {}})
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 1, result.output
    assert "blocks-differ" in result.output
    assert FLAT_LETTER[:61] not in result.output


# --- rules that had no test (finding 3) --------------------------------------------------------------------

def test_a_draft_that_is_a_symbolic_link_is_refused(synth):
    assert ingest(synth).exit_code == 0
    real = inbox_draft(synth, valid_draft())
    link = synth.run_dir / "inbox" / "link.json"
    link.symlink_to(real)
    result = adopt(synth, link)
    assert result.exit_code == 1, result.output
    assert "draft-path" in result.output
    assert not (round_folder(synth) / "adopted.json").exists()


def test_an_empty_letter_is_refused_and_nothing_is_written(synth):
    path = inbox(synth, name="blank.txt", text="\n   \n\t\n")
    result = cli("ingest", RUN, "--letter", str(path))
    assert result.exit_code == 1, result.output
    assert "letter-empty" in result.output
    assert not (synth.run_dir / "revision").exists()


@pytest.mark.parametrize("name,text", [("letter.md", LETTER), ("letter.htm", LETTER_HTML)])
def test_md_and_htm_letters_are_accepted_with_the_same_blocks(synth, name, text):
    result = ingest(synth, name=name, text=text)
    assert result.exit_code == 0, result.output
    import json
    record = json.loads((round_folder(synth) / "letter.json").read_text(encoding="utf-8"))
    assert [block["id"] for block in record["blocks"]] == ["E", "R1", "R2"]
    assert record["source_suffix"] == Path(name).suffix


def test_a_short_quote_is_refused_unless_it_is_the_whole_range(synth):
    assert ingest(synth).exit_code == 0
    draft = valid_draft()
    minor = next(item["review"] for item in draft["reviews"] if item["block"] == "R2")["minor"]
    minor[1]["comment"] = "Please also"
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 1, result.output
    assert "quote-too-short" in result.output
    assert not (round_folder(synth) / "adopted.json").exists()


def test_a_short_quote_equal_to_the_whole_short_range_is_accepted(synth):
    assert ingest(synth).exit_code == 0
    draft = valid_draft()
    r2 = next(item["review"] for item in draft["reviews"] if item["block"] == "R2")
    r2["minor"].append({"id": "m3", "location": "letter:21-21", "comment": "Synthetic Editor"})
    draft["not_comments"] = [item for item in draft["not_comments"] if item["paragraph"] != "R2.p4"]
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 0, result.output
    assert (round_folder(synth) / "adopted.json").is_file()
