"""`scieflow manuscript revision respond` (M-G 5.12): the response starter, one section per adopted comment.

Synthetic data only. The starter names ids, locations and quotes; the response starter is a manuscript-side
file and is never a message or an event (M-G 5.16).
"""

from __future__ import annotations

import json

from tests.research.revision.revision_support import events_text
from tests.research.revision.t6_support import (
    CHANGE_PLACEHOLDER, NO_CHANGE_PLACEHOLDER, RESPONSE_PLACEHOLDER, adopted_round, respond, response_path,
    round_path,
)

EXPECTED_RESPONSE = """# Response to reviewers, round 1

Decision: major (letter).

Paragraphs with no comment:
- E.p1: greeting line
- E.p2: summary of the manuscript
- E.p3: decision line
- R1.p1: reviewer header line
- R2.p1: reviewer header line
- R2.p4: closing and signature

## E.M1 (major) \u2014 letter:7-7

> The reviewers agree that the topic is relevant but raise important concerns about the analysis.

Agent's reading: Synthetic reason. Suggested resolution: Synthetic resolution.

Response: [write your response]
Change: [file \u2014 what changed]
No change: [why]

## R1.M1 (major) \u2014 letter:11-11

> The sample size calculation is not reported in the Methods section and should be added.

Agent's reading: Synthetic reason. Suggested resolution: Synthetic resolution.

Response: [write your response]
Change: [file \u2014 what changed]
No change: [why]

## R1.m1 (minor) \u2014 letter:12-12

> The timing measure is defined twice with different units; please reconcile the two definitions.

Response: [write your response]
Change: [file \u2014 what changed]
No change: [why]

## R2.m1 (minor) \u2014 letter:16-16

> The figures are clear but Figure 2 lacks a legend describing the error bars.

Response: [write your response]
Change: [file \u2014 what changed]
No change: [why]

## R2.m2 (minor) \u2014 letter:18-18

> Please also check the reference list for formatting.

Response: [write your response]
Change: [file \u2014 what changed]
No change: [why]
"""


def test_respond_writes_the_exact_starter_for_the_fixture(synth):
    adopted_round(synth)
    result = respond(synth)
    assert result.exit_code == 0, result.output
    assert response_path(synth).read_text(encoding="utf-8") == EXPECTED_RESPONSE


def test_every_section_carries_the_three_placeholders(synth):
    adopted_round(synth)
    assert respond(synth).exit_code == 0
    text = response_path(synth).read_text(encoding="utf-8")
    assert text.count(RESPONSE_PLACEHOLDER) == 5
    assert text.count(CHANGE_PLACEHOLDER) == 5
    assert text.count(NO_CHANGE_PLACEHOLDER) == 5


def test_an_existing_starter_is_kept_without_force(synth):
    adopted_round(synth)
    assert respond(synth).exit_code == 0
    response_path(synth).write_text("Typed by the author.\n", encoding="utf-8")
    again = respond(synth)
    assert again.exit_code == 1, again.output
    assert "--force" in again.output
    assert response_path(synth).read_text(encoding="utf-8") == "Typed by the author.\n"


def test_force_replaces_the_starter(synth):
    adopted_round(synth)
    assert respond(synth).exit_code == 0
    response_path(synth).write_text("Typed by the author.\n", encoding="utf-8")
    forced = respond(synth, "--force")
    assert forced.exit_code == 0, forced.output
    assert response_path(synth).read_text(encoding="utf-8") == EXPECTED_RESPONSE


def test_respond_needs_an_adopted_round(synth):
    result = respond(synth)
    assert result.exit_code == 1, result.output
    assert not response_path(synth).exists()


def test_respond_refuses_a_symbolic_link_at_the_starter_path(synth, tmp_path):
    adopted_round(synth)
    target = tmp_path / "elsewhere.md"
    target.write_text("outside\n", encoding="utf-8")
    response_path(synth).symlink_to(target)
    result = respond(synth, "--force")
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert target.read_text(encoding="utf-8") == "outside\n"


def test_no_letter_text_reaches_the_events(synth):
    adopted_round(synth)
    assert respond(synth).exit_code == 0
    assert "The reviewers agree that the topic is relevant" not in events_text(synth)
    assert "Synthetic resolution" not in events_text(synth)


def rewrite(path, edit_doc) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit_doc(doc)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_review_entry_without_a_location_is_refused_not_a_traceback(synth):
    adopted_round(synth)
    rewrite(round_path(synth, "reviews", "E.json"), lambda doc: doc["major"][0].pop("location"))
    result = respond(synth)
    assert result.exit_code == 1, result.output
    assert "review-invalid" in result.output


def test_a_review_that_is_not_an_object_is_refused(synth):
    adopted_round(synth)
    round_path(synth, "reviews", "E.json").write_text("[]\n", encoding="utf-8")
    result = respond(synth)
    assert result.exit_code == 1, result.output
    assert "review-invalid" in result.output


def test_an_adopted_comment_that_is_not_text_is_refused(synth):
    adopted_round(synth)
    rewrite(round_path(synth, "adopted.json"), lambda doc: doc["comments"].__setitem__(0, 5))
    result = respond(synth)
    assert result.exit_code == 1, result.output
    assert "adopted-invalid" in result.output


def test_a_not_comment_entry_without_a_reason_is_refused(synth):
    adopted_round(synth)
    rewrite(round_path(synth, "adopted.json"), lambda doc: doc["not_comments"][0].pop("reason"))
    result = respond(synth)
    assert result.exit_code == 1, result.output
    assert "adopted-invalid" in result.output
