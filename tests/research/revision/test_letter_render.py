"""`scieflow manuscript revision letter` (M-G 5.12): the response to reviewers, from the vendored template.

Synthetic data only. The vendored template is read, never modified: the letter's preamble must equal the
template's head (up to and including the \\newpage after \\tableofcontents) with the title filled in.
"""

from __future__ import annotations

from pathlib import Path

from scieflow._vendor import researcher_core as vendored
from scieflow.research.manuscript import tex
from tests.research.revision.revision_support import LETTER_SHA, review_doc
from tests.research.revision.t6_support import (
    adopted_round, change_methods, coverage, letter, respond, response_path, round_path,
)

TITLE = "Gait timing in synthetic cohorts"
TEMPLATE = Path(vendored.__file__).resolve().parent / "templates" / "response-to-reviewers.tex"
CUT = "\\tableofcontents\n\\newpage\n"

RESPONSE = "We thank the editor & the reviewer: 5% of cases, p_value # $2."

EXPECTED_BODY = (
    "\n"
    "\\section{Editor}\n"
    "\n"
    "\\subsection{Comment E.M1}\n"
    "\\reviewercomment{The reviewers agree that the topic is relevant but raise important concerns about the "
    "analysis.}\n"
    "\n"
    "\\response{We thank the editor \\& the reviewer: 5\\% of cases, p\\_value \\# \\$2.}\n"
    "\n"
    "\\noindent\\textbf{Change:} methods.tex \u2014 we added the sample size.\n"
    "\n"
    "\\section{Reviewer 1}\n"
    "\n"
    "\\subsection{Comment R1.m1}\n"
    "\\reviewercomment{The timing measure is defined twice with different units; please reconcile the two "
    "definitions.}\n"
    "\n"
    "\\response{We thank the editor \\& the reviewer: 5\\% of cases, p\\_value \\# \\$2.}\n"
    "\n"
    "\\noindent\\textbf{No change:} The two definitions are the same measure.\n"
    "\n"
    "\\end{document}\n"
)


def template_head() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return text[: text.index(CUT) + len(CUT)].replace("TITLE", TITLE)


def two_comment_draft() -> dict:
    """E has one major comment, R1 one minor comment, R2 none (every R2 paragraph is in not_comments)."""
    return {
        "version": 1,
        "round": 1,
        "letter_sha256": LETTER_SHA,
        "reviews": [
            {"block": "E", "review": review_doc("E", major=[(
                "M1", "letter:7-7",
                "The reviewers agree that the topic is relevant but raise important concerns about the analysis.")])},
            {"block": "R1", "review": review_doc("R1", minor=[(
                "m1", "letter:12-12",
                "The timing measure is defined twice with different units; please reconcile the two definitions.")])},
            {"block": "R2", "review": review_doc("R2")},
        ],
        "not_comments": [
            {"paragraph": "E.p1", "reason": "greeting line"},
            {"paragraph": "E.p2", "reason": "summary of the manuscript"},
            {"paragraph": "E.p3", "reason": "decision line"},
            {"paragraph": "R1.p1", "reason": "reviewer header line"},
            {"paragraph": "R2.p1", "reason": "reviewer header line"},
            {"paragraph": "R2.p2", "reason": "figures praised"},
            {"paragraph": "R2.p3", "reason": "formatting remark"},
            {"paragraph": "R2.p4", "reason": "closing and signature"},
        ],
    }


def answer_two(synth) -> None:
    """Respond, then write the answers by hand: E.M1 changes the manuscript, R1.m1 gives a reason."""
    assert respond(synth).exit_code == 0
    response_path(synth).write_text(
        "# Response to reviewers, round 1\n"
        "\n"
        "Decision: major (letter).\n"
        "\n"
        "## E.M1 (major) \u2014 letter:7-7\n"
        "\n"
        "> The reviewers agree that the topic is relevant but raise important concerns about the analysis.\n"
        "\n"
        "Response: " + RESPONSE + "\n"
        "Change: methods.tex \u2014 we added the sample size.\n"
        "No change: [why]\n"
        "\n"
        "## R1.m1 (minor) \u2014 letter:12-12\n"
        "\n"
        "> The timing measure is defined twice with different units; please reconcile the two definitions.\n"
        "\n"
        "Response: " + RESPONSE + "\n"
        "Change: [file \u2014 what changed]\n"
        "No change: The two definitions are the same measure.\n",
        encoding="utf-8",
    )
    change_methods(synth)


def test_the_letter_is_the_exact_text_for_a_two_comment_fixture(synth):
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    result = letter(synth)
    assert result.exit_code == 0, result.output
    path = synth.ms / "letters" / "response-to-reviewers-r1.tex"
    assert path.read_text(encoding="utf-8") == template_head() + EXPECTED_BODY


def test_the_preamble_is_the_vendored_head_with_the_title(synth):
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    assert letter(synth).exit_code == 0
    text = (synth.ms / "letters" / "response-to-reviewers-r1.tex").read_text(encoding="utf-8")
    assert text.startswith(template_head())
    assert "TITLE" not in text.split(CUT, 1)[0]


def test_the_vendored_template_is_not_modified(synth):
    before = TEMPLATE.read_bytes()
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    assert letter(synth).exit_code == 0
    assert TEMPLATE.read_bytes() == before


def test_a_second_letter_is_refused_without_force_and_forced_with_it(synth):
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    assert letter(synth).exit_code == 0
    path = synth.ms / "letters" / "response-to-reviewers-r1.tex"
    path.write_text("kept\n", encoding="utf-8")
    again = letter(synth)
    assert again.exit_code == 1, again.output
    assert "--force" in again.output
    assert path.read_text(encoding="utf-8") == "kept\n"
    forced = letter(synth, "--force")
    assert forced.exit_code == 0, forced.output
    assert path.read_text(encoding="utf-8") == template_head() + EXPECTED_BODY


def test_the_letter_is_refused_unless_coverage_is_ok(synth):
    adopted_round(synth, two_comment_draft())
    assert respond(synth).exit_code == 0
    result = letter(synth)
    assert result.exit_code == 1, result.output
    assert "coverage" in result.output
    assert not (synth.ms / "letters" / "response-to-reviewers-r1.tex").exists()


def test_the_letter_leaves_the_source_tree_hash_unchanged(synth):
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    before = tex.source_tree_hash(synth.ms)
    assert letter(synth).exit_code == 0
    assert tex.source_tree_hash(synth.ms) == before


def test_the_letter_is_written_after_a_coverage_run_that_is_ok(synth):
    adopted_round(synth, two_comment_draft())
    answer_two(synth)
    assert coverage(synth).exit_code == 0
    assert letter(synth).exit_code == 0
    assert (synth.ms / "letters" / "response-to-reviewers-r1.tex").is_file()
    assert "\"ok\": true" in round_path(synth, "coverage.json").read_text(encoding="utf-8")
