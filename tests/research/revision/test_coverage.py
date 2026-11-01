"""`scieflow manuscript revision coverage` (M-G 5.12): every adopted comment answered, with a change or a reason.

Rules named in the output: missing, no-response, no-change-line, both-change-lines, no-change-reason,
change-path, change-not-found, unknown-comment (warning). Synthetic data only.
"""

from __future__ import annotations

import json

from tests.research.revision.revision_support import events_text, read_json
from tests.research.revision.t6_support import (
    adopted_round, change_methods, coverage, fill_all, respond, response_path, round_path,
)

COMMENTS = ["E.M1", "R1.M1", "R1.m1", "R2.m1", "R2.m2"]


def starter(synth) -> None:
    adopted_round(synth)
    assert respond(synth).exit_code == 0


def answered(synth) -> None:
    starter(synth)
    fill_all(synth)
    change_methods(synth)


def problems(synth) -> list:
    return read_json(round_path(synth, "coverage.json"))["problems"]


def rules_for(synth, comment: str) -> set:
    return {row["rule"] for row in problems(synth) if row["comment"] == comment and row["level"] == "error"}


def test_a_complete_answer_is_ok_and_writes_coverage_json(synth):
    answered(synth)
    result = coverage(synth)
    assert result.exit_code == 0, result.output
    document = read_json(round_path(synth, "coverage.json"))
    assert set(document) == {"version", "round", "ok", "comments", "problems"}
    assert document["ok"] is True
    assert document["comments"] == COMMENTS
    assert document["problems"] == []


def test_coverage_json_is_byte_identical_on_a_re_run(synth):
    answered(synth)
    assert coverage(synth).exit_code == 0
    first = round_path(synth, "coverage.json").read_bytes()
    assert coverage(synth).exit_code == 0
    assert round_path(synth, "coverage.json").read_bytes() == first


def test_json_output_is_the_coverage_document(synth):
    answered(synth)
    result = coverage(synth, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_a_missing_section_is_named(synth):
    answered(synth)
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8").replace("## R1.m1 (minor)", "## R1.mm (minor)"),
                    encoding="utf-8")
    result = coverage(synth)
    assert result.exit_code == 1, result.output
    assert "R1.m1" in result.output
    assert "missing" in rules_for(synth, "R1.m1")


def test_a_placeholder_response_is_no_response(synth):
    starter(synth)
    fill_all(synth)
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8").replace("Response: Synthetic answer to the comment.",
                                                              "Response: [write your response]"),
                    encoding="utf-8")
    change_methods(synth)
    assert coverage(synth).exit_code == 1
    assert "no-response" in rules_for(synth, "E.M1")


def test_neither_change_line_is_no_change_line(synth):
    starter(synth)
    fill_all(synth, change="")
    change_methods(synth)
    assert coverage(synth).exit_code == 1
    for comment in COMMENTS:
        assert "no-change-line" in rules_for(synth, comment), comment


def test_both_change_lines_are_refused(synth):
    answered(synth)
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "No change: [why]", "No change: Synthetic reason given with both lines."), encoding="utf-8")
    assert coverage(synth).exit_code == 1
    assert "both-change-lines" in rules_for(synth, "R1.M1")


def test_a_short_no_change_reason_is_refused(synth):
    starter(synth)
    fill_all(synth, change="")
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8").replace("No change: [why]", "No change: short"),
                    encoding="utf-8")
    change_methods(synth)
    assert coverage(synth).exit_code == 1
    assert "no-change-reason" in rules_for(synth, "R2.m2")


def test_a_long_enough_no_change_reason_is_ok(synth):
    starter(synth)
    fill_all(synth, change="")
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "No change: [why]", "No change: The synthetic comment needs no edit."), encoding="utf-8")
    change_methods(synth)
    assert coverage(synth).exit_code == 0


def test_a_change_without_a_manuscript_path_is_change_path(synth):
    starter(synth)
    fill_all(synth, change="a few words but no path")
    change_methods(synth)
    assert coverage(synth).exit_code == 1
    assert "change-path" in rules_for(synth, "E.M1")


def test_a_change_path_outside_the_manuscript_is_change_path(synth, tmp_path):
    starter(synth)
    outside = tmp_path / "outside.tex"
    outside.write_text("outside\n", encoding="utf-8")
    fill_all(synth, change="../outside.tex \u2014 synthetic")
    assert coverage(synth).exit_code == 1
    assert "change-path" in rules_for(synth, "E.M1")


def test_a_change_to_an_unchanged_file_is_not_found(synth):
    starter(synth)
    fill_all(synth)
    assert coverage(synth).exit_code == 1
    for comment in COMMENTS:
        assert "change-not-found" in rules_for(synth, comment), comment


def test_a_change_to_a_file_absent_from_both_is_not_found(synth):
    starter(synth)
    fill_all(synth, change="sections/missing.tex \u2014 synthetic")
    assert coverage(synth).exit_code == 1
    assert "change-not-found" in rules_for(synth, "R1.M1")


def test_a_change_naming_a_symbolic_link_is_change_path(synth, tmp_path):
    starter(synth)
    outside = tmp_path / "outside.tex"
    outside.write_text("outside\n", encoding="utf-8")
    (synth.ms / "methods.tex").unlink()
    (synth.ms / "methods.tex").symlink_to(outside)
    fill_all(synth)
    assert coverage(synth).exit_code == 1
    assert "change-path" in rules_for(synth, "E.M1")


def test_an_unknown_comment_is_a_warning_only(synth):
    answered(synth)
    path = response_path(synth)
    path.write_text(path.read_text(encoding="utf-8") + "\n## R9.M1 (major) \u2014 letter:1-1\n", encoding="utf-8")
    result = coverage(synth)
    assert result.exit_code == 0, result.output
    warnings = [row for row in problems(synth) if row["rule"] == "unknown-comment"]
    assert [row["level"] for row in warnings] == ["warning"]


def test_a_symbolic_link_at_the_starter_is_refused(synth, tmp_path):
    answered(synth)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("outside\n", encoding="utf-8")
    response_path(synth).unlink()
    response_path(synth).symlink_to(outside)
    result = coverage(synth)
    assert result.exit_code == 1, result.output
    assert "symlink" in result.output
    assert not round_path(synth, "coverage.json").exists()


def test_the_coverage_event_carries_counts_and_no_text(synth):
    answered(synth)
    assert coverage(synth).exit_code == 0
    text = events_text(synth)
    event = next(json.loads(line) for line in text.splitlines() if "note.revision_coverage" in line)
    assert event["data"]["ok"] is True
    assert "Synthetic answer" not in text
