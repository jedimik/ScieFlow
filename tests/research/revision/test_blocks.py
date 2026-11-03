"""Blocks and paragraphs of a decision letter (M-G 5.10): headers, ranges and paragraph ids."""

from __future__ import annotations

import unicodedata

import pytest

from scieflow.research.revision import letter as letter_mod
from tests.research.revision.revision_support import EXPECTED_BLOCKS, LETTER


def as_tuple(blocks):
    return {
        b["id"]: (b["label_line"], b["start"], b["end"],
                  [(p["id"], p["start"], p["end"]) for p in b["paragraphs"]])
        for b in blocks
    }


def test_synthetic_letter_splits_into_E_R1_R2_with_exact_ranges():
    blocks = letter_mod.parse(letter_mod.normalise(LETTER))
    assert [b["id"] for b in blocks] == ["E", "R1", "R2"]
    assert as_tuple(blocks) == EXPECTED_BLOCKS


def test_text_before_the_first_header_belongs_to_E_and_the_first_editor_header_takes_it_over():
    text = letter_mod.normalise("Dear Dr. Synthetic,\n\nEditor's note: please revise.\n\nReviewer 1:\nA point.\n")
    blocks = letter_mod.parse(text)
    assert [b["id"] for b in blocks] == ["E", "R1"]
    assert blocks[0]["label_line"] == 3
    assert blocks[0]["start"] == 1


def test_an_editor_header_after_a_reviewer_is_E_when_no_editor_block_exists_yet():
    text = letter_mod.normalise("Reviewer #1:\nFirst point here.\n\nEditor comments:\nSecond decision text.\n")
    blocks = letter_mod.parse(text)
    assert [b["id"] for b in blocks] == ["R1", "E"]
    assert blocks[1]["label_line"] == 4


def test_a_second_editor_header_after_the_first_editor_block_is_E2():
    text = letter_mod.normalise("Editor's note:\nOne.\n\nReviewer 1:\nA.\n\nAssociate editor:\nTwo.\n")
    blocks = letter_mod.parse(text)
    assert [b["id"] for b in blocks] == ["E", "R1", "E2"]


def test_a_repeated_reviewer_header_is_text_of_the_current_block():
    text = letter_mod.normalise("Reviewer 1:\nA point.\n\nReviewer 2:\nB point.\n\nReviewer 1:\nStill B.\n")
    blocks = letter_mod.parse(text)
    assert [b["id"] for b in blocks] == ["R1", "R2"]
    assert blocks[1]["end"] == 8
    assert [p["id"] for p in blocks[1]["paragraphs"]] == ["R2.p1", "R2.p2"]


def test_a_header_longer_than_120_characters_is_text():
    long_header = "Reviewer 1 " + "x" * 120
    text = letter_mod.normalise(f"Editor:\nDecision.\n\n{long_header}\nBody.\n")
    assert [b["id"] for b in letter_mod.parse(text)] == ["E"]


def test_an_empty_leading_block_is_dropped():
    text = letter_mod.normalise("\n\nReviewer 1:\nA point.\n")
    assert [b["id"] for b in letter_mod.parse(text)] == ["R1"]


def test_paragraphs_are_maximal_runs_of_non_blank_lines_and_never_cross_a_header():
    text = letter_mod.normalise("Reviewer 1:\nLine one.\nLine two.\n\nLine three.\nReviewer 2:\nLine four.\n")
    blocks = as_tuple(letter_mod.parse(text))
    assert blocks["R1"][3] == [("R1.p1", 1, 3), ("R1.p2", 5, 5)]
    assert blocks["R2"][3] == [("R2.p1", 6, 7)]


def test_normalise_nfc_crlf_and_trailing_spaces():
    raw = "Re\u0301view\r\nline one   \r\n\rline two\t \r\n"
    assert letter_mod.normalise(raw) == unicodedata.normalize("NFC", "Re\u0301view") + "\nline one\n\nline two\n"


@pytest.mark.parametrize("header, expected", [
    ("Reviewer #2 (Synthetic):", "R2"),
    ("REFEREE 3", "R3"),
    ("Reviewer 12 comments", "R12"),
])
def test_reviewer_header_forms(header, expected):
    blocks = letter_mod.parse(letter_mod.normalise(f"{header}\nBody.\n"))
    assert [b["id"] for b in blocks] == [expected]
