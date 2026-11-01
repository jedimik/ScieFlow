"""`scieflow manuscript revision adopt` (M-G 5.11): verbatim, covering, schema-valid comments.

Every refusal writes nothing: no reviews/, no adopted.json. Rules named in messages: letter-changed,
blocks-differ, review-invalid, quote-not-in-letter, letter-uncovered, not-comment-conflict, draft-path,
draft-size, adopted-exists, dispatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scieflow.core.review import decisions
from tests.research.manuscript.conftest import RUN
from tests.research.revision.revision_support import (
    LETTER, LETTER_SHA, adopt, events_text, ingest, read_json, sha, valid_draft, write_draft, cli,
)

REVIEWS = ("E", "R1", "R2")


def ready(synth):
    """Ingest LETTER and return the draft path of a valid draft (not yet written under the run)."""
    assert ingest(synth).exit_code == 0
    return write_draft(synth, valid_draft())


def round_path(synth, *parts) -> Path:
    return synth.run_dir.joinpath("revision", "round-1", *parts)


def assert_nothing_written(synth):
    assert not round_path(synth, "reviews").exists()
    assert not round_path(synth, "adopted.json").exists()


def test_a_valid_draft_adopts_into_one_review_per_block(synth):
    draft_path = ready(synth)
    result = adopt(synth, draft_path)
    assert result.exit_code == 0, result.output
    for block in REVIEWS:
        document = read_json(round_path(synth, "reviews", f"{block}.json"))
        assert decisions.schema_error("manuscript-review", document) is None, block
    adopted = read_json(round_path(synth, "adopted.json"))
    assert set(adopted) == {"version", "round", "letter_sha256", "draft_sha256", "comments", "not_comments"}
    assert adopted["version"] == 1
    assert adopted["round"] == 1
    assert adopted["letter_sha256"] == LETTER_SHA
    assert adopted["draft_sha256"] == sha(draft_path.read_bytes())
    assert adopted["comments"] == ["E.M1", "R1.M1", "R1.m1", "R2.m1", "R2.m2"]
    assert [row["paragraph"] for row in adopted["not_comments"]] == [
        "E.p1", "E.p2", "E.p3", "R1.p1", "R2.p1", "R2.p4"]


def test_review_files_are_canonical_json(synth):
    adopt(synth, ready(synth))
    path = round_path(synth, "reviews", "R1.json")
    expected = json.dumps(read_json(path), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert path.read_text(encoding="utf-8") == expected


def test_the_adopt_event_carries_counts_only(synth):
    assert adopt(synth, ready(synth)).exit_code == 0
    text = events_text(synth)
    event = next(json.loads(ln) for ln in text.splitlines() if "note.revision_adopted" in ln)
    assert event["data"]["round"] == 1
    assert event["data"]["comments"] == 5
    assert event["data"]["not_comments"] == 6
    assert "Gait timing" not in text


def test_the_draft_is_checked_against_the_stored_letter_not_the_prompt_copy(synth):
    draft_path = ready(synth)
    letter_file = round_path(synth, "letter.txt")
    letter_file.write_text(LETTER.replace("Synthetic Editor", "Other Editor"), encoding="utf-8")
    result = adopt(synth, draft_path)
    assert result.exit_code == 1
    assert "letter-changed" in result.output
    assert_nothing_written(synth)


def test_a_draft_with_another_letter_hash_is_refused(synth):
    draft = valid_draft(letter_sha="0" * 64)
    assert ingest(synth).exit_code == 0
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 1
    assert "letter-changed" in result.output
    assert_nothing_written(synth)


def test_a_missing_block_review_is_blocks_differ(synth):
    draft = valid_draft()
    draft["reviews"] = [item for item in draft["reviews"] if item["block"] != "R2"]
    assert ingest(synth).exit_code == 0
    result = adopt(synth, write_draft(synth, draft))
    assert result.exit_code == 1
    assert "blocks-differ" in result.output
    assert_nothing_written(synth)


def mutate(mutator):
    draft = valid_draft()
    mutator(draft)
    return draft


def review_of(draft, block):
    return next(item["review"] for item in draft["reviews"] if item["block"] == block)


CASES = {
    "schema": (lambda d: review_of(d, "R1").pop("summary"), "review-invalid"),
    "pass-present": (lambda d: review_of(d, "R1").update({"pass": "blind"}), "review-invalid"),
    "evidence-present": (lambda d: review_of(d, "R1").update({"evidence": []}), "review-invalid"),
    "bad-agent": (lambda d: review_of(d, "R1").update({"agent": "journal-r9"}), "review-invalid"),
    "bad-reviewed": (lambda d: review_of(d, "R1").update({"reviewed": "paper"}), "review-invalid"),
    "location-outside-block": (
        lambda d: review_of(d, "R1")["minor"][0].update({"location": "letter:16-16"}), "review-invalid"),
    "location-outside-letter": (
        lambda d: review_of(d, "R1")["minor"][0].update({"location": "letter:12-99"}), "review-invalid"),
    "duplicate-id": (lambda d: review_of(d, "R1")["minor"].append(
        dict(review_of(d, "R1")["minor"][0])), "review-invalid"),
    "paraphrase": (lambda d: review_of(d, "R1")["major"][0].update(
        {"problem": "The sample size is never stated in the methods, and it should be included."}),
        "quote-not-in-letter"),
    "dropped-paragraph": (lambda d: (review_of(d, "R2")["minor"].pop(1),
                                     d["not_comments"].pop(
                                         [r["paragraph"] for r in d["not_comments"]].index("R2.p4"))),
                          "letter-uncovered"),
    "commented-and-listed": (lambda d: d["not_comments"].append(
        {"paragraph": "R1.p2", "reason": "listed by mistake"}), "not-comment-conflict"),
    "duplicate-not-comment": (lambda d: d["not_comments"].append(
        {"paragraph": "E.p1", "reason": "greeting line again"}), "not-comment-invalid"),
    "short-reason": (lambda d: d["not_comments"][0].update({"reason": "hi"}), "not-comment-invalid"),
    "unknown-paragraph": (lambda d: d["not_comments"].append(
        {"paragraph": "R9.p1", "reason": "no such block"}), "not-comment-invalid"),
    "extra-block": (lambda d: d["reviews"].append({"block": "R3", "review": {}}), "blocks-differ"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_refusal_names_its_rule_and_writes_nothing(synth, case):
    mutator, rule = CASES[case]
    assert ingest(synth).exit_code == 0
    result = adopt(synth, write_draft(synth, mutate(mutator)))
    assert result.exit_code == 1, result.output
    assert rule in result.output, result.output
    assert_nothing_written(synth)


def test_a_quote_refusal_never_quotes_the_letter_beyond_60_characters(synth):
    def paraphrase(d):
        review_of(d, "R1")["major"][0]["problem"] = (
            "The sample size calculation is never reported in the Methods, which is a serious gap.")
    assert ingest(synth).exit_code == 0
    result = adopt(synth, write_draft(synth, mutate(paraphrase)))
    assert "quote-not-in-letter" in result.output
    assert "R1.M1" in result.output
    assert "The sample size calculation is not reported in the Methods section and should be added." \
        not in result.output


def test_a_draft_outside_the_run_is_refused(synth, tmp_path):
    assert ingest(synth).exit_code == 0
    outside = tmp_path / "draft.json"
    outside.write_text(json.dumps(valid_draft()), encoding="utf-8")
    result = adopt(synth, outside)
    assert result.exit_code == 1
    assert "draft-path" in result.output
    assert_nothing_written(synth)


def test_a_draft_over_one_megabyte_is_refused(synth):
    assert ingest(synth).exit_code == 0
    path = synth.run_dir / "revision" / "round-1" / "draft" / "big.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    result = adopt(synth, path)
    assert result.exit_code == 1
    assert "draft-size" in result.output
    assert_nothing_written(synth)


def test_adopt_is_refused_inside_a_dispatch(synth, monkeypatch):
    draft_path = ready(synth)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = adopt(synth, draft_path)
    assert result.exit_code == 1
    assert "dispatch" in result.output
    assert_nothing_written(synth)


def test_an_existing_adopted_file_needs_replace(synth):
    draft_path = ready(synth)
    assert adopt(synth, draft_path).exit_code == 0
    before = round_path(synth, "adopted.json").read_bytes()
    result = adopt(synth, draft_path)
    assert result.exit_code == 1
    assert "--replace" in result.output
    assert round_path(synth, "adopted.json").read_bytes() == before
    assert adopt(synth, draft_path, "--replace").exit_code == 0

