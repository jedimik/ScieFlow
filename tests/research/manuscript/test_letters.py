"""`scieflow manuscript letter` and the starter letters (M-F 5.13). Synthetic data only."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import tex
from tests.research.manuscript.conftest import RUN, SLUG
from tests.research.manuscript.t8_support import make_build, snapshot


def letter(*args):
    return CliRunner().invoke(main, ["manuscript", "letter", RUN, *args], catch_exceptions=False)


def test_manuscript_help_lists_package_and_letter():
    result = CliRunner().invoke(main, ["manuscript", "--help"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "package" in result.output and "letter" in result.output


def test_a_cover_letter_is_written_and_an_existing_one_is_kept_without_force(synth):
    result = letter("--kind", "cover")
    assert result.exit_code == 0, result.output
    path = synth.ms / "letters" / "cover-letter.tex"
    assert "Ada Synthetic" in path.read_text(encoding="utf-8")
    again = letter("--kind", "cover")
    assert again.exit_code == 1, again.output
    assert "--force" in again.output
    forced = letter("--kind", "cover", "--force")
    assert forced.exit_code == 0, forced.output


def test_the_response_letter_fills_the_title(synth):
    result = letter("--kind", "response")
    assert result.exit_code == 0, result.output
    text = (synth.ms / "letters" / "response-to-reviewers.tex").read_text(encoding="utf-8")
    assert "Manuscript: Gait timing in synthetic cohorts" in text
    assert "TITLE" not in text


def test_special_characters_in_the_title_are_escaped(synth):
    synth.edit("metadata.yml", "title: Gait timing in synthetic cohorts",
               'title: "Gait & timing: 5% of cases"')
    assert letter("--kind", "cover").exit_code == 0
    text = (synth.ms / "letters" / "cover-letter.tex").read_text(encoding="utf-8")
    assert "``Gait \\& timing: 5\\% of cases''" in text


def test_without_a_corresponding_author_the_placeholder_stays(synth):
    synth.edit("metadata.yml", "corresponding: true", "corresponding: false")
    assert letter("--kind", "cover").exit_code == 0
    text = (synth.ms / "letters" / "cover-letter.tex").read_text(encoding="utf-8")
    assert "CORRESPONDING AUTHOR NAME" in text


def test_a_starter_letter_is_not_part_of_the_source_tree(synth):
    before = tex.source_tree_hash(synth.ms)
    assert letter("--kind", "cover").exit_code == 0
    assert tex.source_tree_hash(synth.ms) == before


def test_a_build_stays_valid_after_a_letter_is_written(synth):
    make_build(synth)
    assert letter("--kind", "cover").exit_code == 0
    record = json.loads((synth.ms / "build" / "build.json").read_text(encoding="utf-8"))
    assert record["source_tree_sha256"] == tex.source_tree_hash(synth.ms)


def test_a_legacy_profile_does_not_block_a_starter_letter(synth):
    (synth.profiles_dir / f"{SLUG}.yml").unlink()
    (synth.profiles_dir / f"{SLUG}.md").write_text("# Hand-written page\n", encoding="utf-8")
    result = letter("--kind", "cover")
    assert result.exit_code == 0, result.output
    text = (synth.ms / "letters" / "cover-letter.tex").read_text(encoding="utf-8")
    assert "JOURNAL NAME" in text


def test_a_symlinked_letters_folder_is_refused(synth, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (synth.ms / "letters").symlink_to(outside, target_is_directory=True)
    before = snapshot(synth.run_dir)
    result = letter("--kind", "cover")
    assert result.exit_code == 1, result.output
    assert list(outside.iterdir()) == []
    assert snapshot(synth.run_dir) == before


def test_an_unknown_kind_is_a_usage_error(synth):
    result = CliRunner().invoke(main, ["manuscript", "letter", RUN, "--kind", "poster"])
    assert result.exit_code == 2
