"""`scieflow manuscript package` outputs (M-F 5.13): the file list, package.json, the cover letter fills,
declarations, highlights, reporting checklists, the submission checklist, determinism and events.

Synthetic data only. No network, no TeX run (the build record is written by t8_support.make_build).
"""

from __future__ import annotations

import json
import re
import shutil

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.research.manuscript import build as build_mod
from scieflow.research.manuscript import package as package_mod, tex
from tests.research.manuscript.conftest import RUN, make_profile
from tests.research.manuscript.t8_support import PDF, make_build, project_of, sha, snapshot, submission

FILES = {
    "main.pdf", "source.zip", "cover-letter.tex", "declarations.md", "highlights.txt",
    "manuscript-check.md", "submission-checklist.md", "package.json",
}
TEXT = ("declarations.md", "highlights.txt", "manuscript-check.md", "submission-checklist.md",
        "package.json", "cover-letter.tex")
TIMESTAMP = re.compile(r"\b(?:19|20)\d\d-\d\d-\d\d\b|\bT\d\d:\d\d")


@pytest.fixture
def built(synth):
    make_build(synth)
    return synth


def package_cli(synth):
    result = CliRunner().invoke(main, ["manuscript", "package", RUN], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return submission(synth)


def read(folder, name: str) -> str:
    return (folder / name).read_text(encoding="utf-8")


def test_a_clean_run_writes_exactly_the_listed_files(built):
    folder = package_cli(built)
    assert {p.name for p in folder.iterdir()} == FILES
    assert (folder / "main.pdf").read_bytes() == PDF


def test_package_json_pins_every_file_and_the_build(built):
    folder = package_cli(built)
    record = json.loads(read(folder, "package.json"))
    assert set(record["files"]) == FILES - {"package.json"}
    for name, digest in record["files"].items():
        assert sha((folder / name).read_bytes()) == digest, name
    assert record["build"]["pdf_sha256"] == sha(PDF)
    assert record["build"]["source_tree_sha256"] == tex.source_tree_hash(built.ms)
    assert record["blockers"] == 0
    assert record["data_level"] == "L0"
    assert record["journal"]["slug"] == "journal-of-synthetic-neurology"


def test_no_text_file_carries_a_generation_timestamp(built):
    folder = package_cli(built)
    for name in TEXT:
        assert not TIMESTAMP.search(read(folder, name)), name


def test_two_runs_are_byte_identical(built):
    first = {p.name: p.read_bytes() for p in package_cli(built).iterdir()}
    second = {p.name: p.read_bytes() for p in package_cli(built).iterdir()}
    assert first == second


def test_the_cover_letter_is_filled_from_the_metadata_and_profile(built):
    text = read(package_cli(built), "cover-letter.tex")
    assert "``Gait timing in synthetic cohorts''" in text
    assert "Journal of Synthetic Neurology" in text
    assert "Ada Synthetic" in text
    assert "ada.synthetic@example.test" in text
    assert "Synthetic Institute" in text
    assert "Synthetic Editor" in text
    for placeholder in ("CORRESPONDING AUTHOR NAME", "JOURNAL NAME", "email@example.com", "EDITOR NAME"):
        assert placeholder not in text, placeholder


def test_placeholders_still_present_are_listed_in_the_checklist(built):
    checklist = read(package_cli(built), "submission-checklist.md")
    assert "[brief description" in checklist
    assert "Name, Affiliation" in checklist
    assert "EDITOR NAME" not in checklist
    assert "ORCID and ROR were checked offline only" in checklist
    assert "ScieFlow uploads nothing; you submit" in checklist


def test_declarations_follow_the_profile_order_and_carry_the_credit_statement(built):
    text = read(package_cli(built), "declarations.md")
    order = [text.index(title) for title in ("Competing interests", "Funding", "Ethics")]
    assert order == sorted(order)
    assert "None declared." in text
    assert "Synthetic funder." in text
    assert "Ada Synthetic: Conceptualization." in text


def test_the_highlights_file_lists_the_metadata_highlights(built):
    assert read(package_cli(built), "highlights.txt") == "Synthetic highlight one.\n"


def test_a_checked_guideline_writes_its_unverified_checklist(built):
    folder = package_mod.package_run(project_of(built), built.run_dir,
                                     guidelines=("consort-2010",)).folder
    text = read(folder, "reporting-checklist-consort-2010.md")
    assert "unverified skeleton" in text
    record = json.loads(read(folder, "package.json"))
    assert "reporting-checklist-consort-2010.md" in record["files"]
    assert record["guidelines"] == [{"id": "consort-2010", "verified": False}]


def test_a_word_first_profile_adds_the_docx_note(synth):
    synth.write_profile(make_profile(word_first=True))
    make_build(synth)
    text = read(package_cli(synth), "submission-checklist.md")
    assert "DOCX" in text


def test_an_l2_run_gets_the_data_level_line(built, monkeypatch):
    monkeypatch.setattr(package_mod._levels, "run_level", lambda ws: "L2")
    folder = package_cli(built)
    assert "derived from L2 data: share results only through `scieflow data release`" in read(
        folder, "submission-checklist.md")
    assert json.loads(read(folder, "package.json"))["data_level"] == "L2"


def test_an_existing_cover_letter_is_used_unchanged(built):
    letters = built.ms / "letters"
    letters.mkdir()
    custom = b"\\documentclass{letter}\n% my own cover letter\n"
    (letters / "cover-letter.tex").write_bytes(custom)
    assert (submission(built) / "cover-letter.tex") != custom
    folder = package_cli(built)
    assert (folder / "cover-letter.tex").read_bytes() == custom


def test_only_the_submission_folder_and_the_event_log_change(built):
    before = snapshot(built.run_dir)
    package_cli(built)
    after = snapshot(built.run_dir)
    changed = {rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)}
    outside = {rel for rel in changed
               if not rel.startswith("report/submission/")
               and rel not in (events.EVENTS_FILE, f"{events.EVENTS_FILE}.lock")}
    assert outside == set(), outside


def test_an_existing_submission_folder_is_recreated(built):
    stale = submission(built)
    stale.mkdir(parents=True)
    (stale / "old-file.txt").write_text("from an earlier package\n", encoding="utf-8")
    folder = package_cli(built)
    assert not (folder / "old-file.txt").exists()
    assert {p.name for p in folder.iterdir()} == FILES


def test_the_package_event_carries_counts_and_hashes_only(built):
    package_cli(built)
    [entry] = events.read(built.run_dir, types=("note.manuscript_package",))
    assert entry["data"]["journal"] == "journal-of-synthetic-neurology"
    assert isinstance(entry["data"]["package_sha256"], str) and len(entry["data"]["package_sha256"]) == 64
    assert "@" not in json.dumps(entry["data"])


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs latexmk and the article class")
def test_a_real_compile_then_package(synth):
    # The synthetic figures are not files on disk: drop them so the article compiles without assets.
    text = (synth.ms / "main.tex").read_text(encoding="utf-8")
    text = re.sub(r"\\begin\{figure\}.*?\\end\{figure\}\n", "", text, flags=re.S)
    (synth.ms / "main.tex").write_text(text.replace("as Figure~\\ref{fig:one} shows", "as shown"),
                                       encoding="utf-8")
    record = build_mod.build_run(synth.root, synth.run_dir)
    assert record.ok, record.log_tail
    result = package_mod.package_run(project_of(synth), synth.run_dir)
    pdf = (synth.ms / "build" / "main.pdf").read_bytes()
    assert (result.folder / "main.pdf").read_bytes() == pdf
    assert result.files["main.pdf"] == sha(pdf)
