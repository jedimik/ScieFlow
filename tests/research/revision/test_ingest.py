"""`scieflow manuscript revision ingest` (M-G 5.10): normalised letter, blocks, decision, point, event."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.points.point_support import make_package
from tests.research.revision.revision_support import (
    EXPECTED_BLOCKS, LETTER, LETTER_HTML, LETTER_SHA, cli, events_text, ingest, inbox, read_json, sha,
)


def round_dir(synth, n: int = 1) -> Path:
    return synth.run_dir / "revision" / f"round-{n}"


def test_ingest_writes_the_normalised_letter_and_its_block_record(synth):
    result = ingest(synth)
    assert result.exit_code == 0, result.output
    assert (round_dir(synth) / "letter.txt").read_text(encoding="utf-8") == LETTER
    record = read_json(round_dir(synth) / "letter.json")
    assert set(record) == {"version", "round", "letter_sha256", "source_suffix", "decision",
                           "decision_source", "blocks"}
    assert record["version"] == 1
    assert record["round"] == 1
    assert record["letter_sha256"] == LETTER_SHA == sha((round_dir(synth) / "letter.txt").read_bytes())
    assert record["source_suffix"] == ".txt"
    assert record["decision"] == "major"
    assert record["decision_source"] == "letter"
    got = {b["id"]: (b["label_line"], b["start"], b["end"],
                     [(p["id"], p["start"], p["end"]) for p in b["paragraphs"]]) for b in record["blocks"]}
    assert got == EXPECTED_BLOCKS


def test_the_decision_flag_wins_over_the_letter(synth):
    assert ingest(synth, "--decision", "minor").exit_code == 0
    record = read_json(round_dir(synth) / "letter.json")
    assert (record["decision"], record["decision_source"]) == ("minor", "flag")


def test_two_different_decisions_in_the_editor_block_give_unknown(synth):
    text = LETTER.replace("The reviewers agree that the topic is relevant",
                          "The paper is accepted for publication, and the reviewers agree that the topic is relevant")
    assert ingest(synth, text=text).exit_code == 0
    record = read_json(round_dir(synth) / "letter.json")
    assert (record["decision"], record["decision_source"]) == ("unknown", "unknown")


def test_a_decision_phrase_in_a_reviewer_block_is_ignored(synth):
    text = LETTER.replace("Associate Editor's decision: major revision.", "Associate Editor's decision: see below.") \
                 .replace("Please also check the reference list for formatting.",
                          "Please also check; this is a minor revision for me.")
    assert ingest(synth, text=text).exit_code == 0
    record = read_json(round_dir(synth) / "letter.json")
    assert (record["decision"], record["decision_source"]) == ("unknown", "unknown")


def test_html_letter_gives_the_same_blocks_and_decision(synth):
    assert ingest(synth, name="letter.html", text=LETTER_HTML).exit_code == 0
    html = read_json(round_dir(synth) / "letter.json")
    assert html["source_suffix"] == ".html"
    assert [b["id"] for b in html["blocks"]] == ["E", "R1", "R2"]
    assert html["decision"] == "major"
    text_lines = (round_dir(synth) / "letter.txt").read_text(encoding="utf-8").splitlines()
    assert [ln for ln in text_lines if ln.strip()] == [ln for ln in LETTER.splitlines() if ln.strip()]


@pytest.mark.parametrize("case", ["symlink", "pdf", "too-big", "bad-utf8", "outside-run"])
def test_refused_letter_files_write_nothing(synth, tmp_path, case):
    target = inbox(synth, name="letter.txt")
    if case == "symlink":
        link = synth.run_dir / "inbox" / "link.txt"
        link.symlink_to(target)
        path = link
    elif case == "pdf":
        path = inbox(synth, name="letter.pdf")
    elif case == "too-big":
        path = inbox(synth, name="big.txt", data=b"a" * (1024 * 1024 + 1))
    elif case == "bad-utf8":
        path = inbox(synth, name="bad.txt", data=b"Reviewer 1:\n\xff\xfe broken\n")
    else:
        outside = tmp_path / "outside.txt"
        outside.write_text(LETTER, encoding="utf-8")
        path = outside
    result = cli("ingest", RUN, "--letter", str(path))
    assert result.exit_code == 1, result.output
    assert not (synth.run_dir / "revision").exists()


def test_one_megabyte_exactly_is_accepted(synth):
    body = LETTER + "x" * (1024 * 1024 - len(LETTER.encode("utf-8")) - 1) + "\n"
    data = body.encode("utf-8")
    assert len(data) == 1024 * 1024
    path = inbox(synth, name="edge.txt", data=data)
    assert cli("ingest", RUN, "--letter", str(path)).exit_code == 0


def test_ingest_is_refused_inside_a_dispatch(synth, monkeypatch):
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = ingest(synth)
    assert result.exit_code == 1
    assert "dispatch" in result.output
    assert not (synth.run_dir / "revision").exists()


def test_an_existing_round_folder_is_refused(synth):
    round_dir(synth).mkdir(parents=True)
    result = ingest(synth, "--round", "1")
    assert result.exit_code == 1
    assert "round-1" in result.output
    assert not (round_dir(synth) / "letter.txt").exists()


def test_show_summarises_rounds_without_letter_text(synth):
    from tests.research.revision.revision_support import adopt, valid_draft, write_draft
    assert ingest(synth).exit_code == 0
    assert cli("show", RUN).exit_code == 0
    result = cli("show", RUN, "--json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows == [{"adopted": False, "blocks": ["E", "R1", "R2"], "decision": "major",
                     "decision_source": "letter", "letter_sha256": LETTER_SHA, "round": 1}]
    assert adopt(synth, write_draft(synth, valid_draft())).exit_code == 0
    assert json.loads(cli("show", RUN, "--json").output)[0]["adopted"] is True
    assert "Dear Dr. Synthetic" not in cli("show", RUN).output


def test_round_defaults_to_one_past_the_highest_existing_round(synth):
    (synth.run_dir / "revision" / "round-1").mkdir(parents=True)
    (synth.run_dir / "revision" / "round-3").mkdir(parents=True)
    result = ingest(synth)
    assert result.exit_code == 0, result.output
    assert read_json(round_dir(synth, 4) / "letter.json")["round"] == 4
    assert not round_dir(synth, 1).joinpath("letter.txt").exists()


def test_without_a_package_the_point_is_taken_from_the_current_manuscript(synth):
    result = ingest(synth)
    assert result.exit_code == 0, result.output
    assert "point taken from the current manuscript, not from a submission package" in result.output
    point = read_json(synth.run_dir / "revision" / "points" / "submitted-r1" / "point.json")
    assert point["origin"] == "current"


def test_with_a_package_the_point_comes_from_the_package(synth):
    make_package(synth)
    result = ingest(synth)
    assert result.exit_code == 0, result.output
    point = read_json(synth.run_dir / "revision" / "points" / "submitted-r1" / "point.json")
    assert point["origin"] == "package"
    assert "not from a submission package" not in result.output


def test_a_failing_package_is_refused_and_not_replaced_by_the_current_tree(synth):
    make_package(synth)
    zip_path = synth.run_dir / "report" / "submission" / "source.zip"
    zip_path.write_bytes(b"not a zip")
    result = ingest(synth)
    assert result.exit_code == 1
    assert "package-digest" in result.output
    assert not (round_dir(synth)).exists()


def test_an_existing_point_of_that_name_is_kept(synth):
    from scieflow.research.manuscript import points as points_mod
    points_mod.add(synth.run_dir, "submitted-r1")
    before = (synth.run_dir / "revision" / "points" / "submitted-r1" / "point.json").read_bytes()
    result = ingest(synth)
    assert result.exit_code == 0, result.output
    assert (synth.run_dir / "revision" / "points" / "submitted-r1" / "point.json").read_bytes() == before


def test_the_event_carries_counts_and_hashes_but_no_letter_text(synth):
    assert ingest(synth).exit_code == 0
    text = events_text(synth)
    assert "note.revision_ingested" in text
    event = next(json.loads(ln) for ln in text.splitlines() if "note.revision_ingested" in ln)
    data = event["data"]
    assert data["round"] == 1
    assert data["blocks"] == 3
    assert data["paragraphs"] == 10
    assert data["decision"] == "major"
    assert data["letter_sha256"] == LETTER_SHA
    for line in LETTER.splitlines():
        if len(line) > 60:
            assert line[:61] not in text, "letter text beyond 60 characters reached an event"
    assert "Dear Dr. Synthetic" not in text
