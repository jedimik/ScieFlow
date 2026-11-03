"""The GAP acceptance loop (M-G 5.12, M-G-D10): letter -> JSON -> coverage -> response letter, on a fixture.

Ingest, adopt (the fixture draft), respond, answer every section and change sections/results.tex; coverage
exits 0 and `revision letter` writes the letter. Removing one section makes coverage name that id, and the
letter is then refused. Synthetic data only.
"""

from __future__ import annotations

from tests.research.revision.t6_support import (
    adopted_round, coverage, fill_all, letter, respond, response_path, round_path,
)


def test_the_letter_json_coverage_loop_is_green_on_a_fixture(synth):
    results = synth.ms / "sections" / "results.tex"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("\\section{Results}\nOriginal synthetic results.\n", encoding="utf-8")

    adopted_round(synth)
    assert respond(synth).exit_code == 0

    results.write_text("\\section{Results}\nRevised synthetic results with a new sentence.\n", encoding="utf-8")
    fill_all(synth, change="sections/results.tex \u2014 added a sentence")

    checked = coverage(synth)
    assert checked.exit_code == 0, checked.output
    assert round_path(synth, "coverage.json").is_file()

    written = letter(synth)
    assert written.exit_code == 0, written.output
    letter_path = synth.ms / "letters" / "response-to-reviewers-r1.tex"
    assert letter_path.is_file()
    assert "\\end{document}" in letter_path.read_text(encoding="utf-8")


def test_a_removed_section_is_named_and_the_letter_is_then_refused(synth):
    results = synth.ms / "sections" / "results.tex"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("\\section{Results}\nOriginal synthetic results.\n", encoding="utf-8")

    adopted_round(synth)
    assert respond(synth).exit_code == 0
    results.write_text("\\section{Results}\nRevised synthetic results with a new sentence.\n", encoding="utf-8")
    fill_all(synth, change="sections/results.tex \u2014 added a sentence")

    path = response_path(synth)
    text = path.read_text(encoding="utf-8")
    start = text.index("## R2.m2 (minor)")
    path.write_text(text[:start], encoding="utf-8")

    checked = coverage(synth)
    assert checked.exit_code == 1, checked.output
    assert "R2.m2" in checked.output

    refused = letter(synth)
    assert refused.exit_code == 1, refused.output
    assert not (synth.ms / "letters" / "response-to-reviewers-r1.tex").exists()
