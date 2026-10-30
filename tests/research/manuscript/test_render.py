"""`scieflow manuscript render` (M-F 5.11, t7): golden bodies, precedence, section files, overwrite rules.

Synthetic data only. The golden files hold the exact main.tex body each class must produce.
"""

import hashlib
import json
import os
import re

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import render, templates
from tests.research.manuscript.conftest import RUN, SLUG, _evidence_for, make_profile
from tests.research.manuscript.t7_support import CLASSES, fresh_article, golden_body

MARKER = re.compile(r"^% scieflow-render: class=(\S+) version=1 sha256=([0-9a-f]{64})$")


def _run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def _main_text(synth) -> str:
    return (synth.ms / "main.tex").read_text(encoding="utf-8")


def _body(text: str) -> str:
    return text.split("\n", 1)[1]


def _drop_profile(synth):
    (synth.profiles_dir / f"{SLUG}.yml").unlink()


@pytest.mark.parametrize("cls", CLASSES)
def test_render_body_matches_the_golden_file(synth, cls):
    fresh_article(synth)
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN, "--class", cls)
    assert result.exit_code == 0, result.output
    text = _main_text(synth)
    assert _body(text) == golden_body(cls)
    match = MARKER.match(text.split("\n", 1)[0])
    assert match, "the first line must be the render marker"
    assert match.group(1) == cls
    assert match.group(2) == hashlib.sha256(_body(text).encode("utf-8")).hexdigest()


def test_article_copies_the_preamble_when_absent(synth):
    fresh_article(synth)
    _drop_profile(synth)
    assert _run("manuscript", "render", RUN, "--class", "article").exit_code == 0
    copied = (synth.ms / "preamble.tex").read_bytes()
    assert copied == (templates.TEMPLATE_DIR / "preamble.tex").read_bytes()


def test_an_existing_preamble_is_never_replaced(synth):
    fresh_article(synth, extra={"preamble.tex": "% the author's own preamble\n"})
    _drop_profile(synth)
    assert _run("manuscript", "render", RUN, "--class", "article").exit_code == 0
    assert (synth.ms / "preamble.tex").read_text(encoding="utf-8") == "% the author's own preamble\n"


@pytest.mark.parametrize("cls", ["elsarticle", "IEEEtran", "svjour3"])
def test_other_classes_do_not_copy_a_preamble(synth, cls):
    fresh_article(synth)
    _drop_profile(synth)
    assert _run("manuscript", "render", RUN, "--class", cls).exit_code == 0
    assert not (synth.ms / "preamble.tex").exists()


def test_escape_covers_every_latex_special_and_never_doubles():
    assert render.escape("& % $ # _ { } ~ ^ \\") == (
        r"\& \% \$ \# \_ \{ \} \textasciitilde{} \textasciicircum{} \textbackslash{}")
    assert render.escape("50%") == r"50\%"


def test_flag_beats_profile_and_profile_beats_the_default(synth):
    synth.write_profile(make_profile(latex_class="svjour3"))
    fresh_article(synth)
    assert _run("manuscript", "render", RUN, "--class", "article").exit_code == 0
    assert "\\documentclass{article}" in _main_text(synth)
    assert _run("manuscript", "render", RUN).exit_code == 0
    assert "\\documentclass{svjour3}" in _main_text(synth)


def test_profile_bibtex_style_beats_the_registry_default(synth):
    synth.write_profile(make_profile(bib_style="unsrt"))
    fresh_article(synth)
    assert _run("manuscript", "render", RUN).exit_code == 0
    assert "\\bibliographystyle{unsrt}" in _main_text(synth)


def test_profile_latex_options_go_in_the_class_brackets(synth):
    data = _with_options(["11pt", "a4paper"])
    synth.write_profile(data)
    fresh_article(synth)
    assert _run("manuscript", "render", RUN).exit_code == 0
    assert "\\documentclass[11pt,a4paper]{article}" in _main_text(synth)


def test_a_required_section_file_missing_exits_one(synth):
    synth.write_profile(make_profile())
    fresh_article(synth, remove=("sections/results.tex",))
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "section-file-missing" in result.output
    assert "results" in result.output


def test_an_optional_section_file_missing_is_skipped(synth):
    data = make_profile()
    data["article_types"][0]["structure"]["sections"][3]["required"] = False  # Discussion
    synth.write_profile(data)
    fresh_article(synth, remove=("sections/discussion.tex",))
    assert _run("manuscript", "render", RUN).exit_code == 0
    text = _main_text(synth)
    assert "discussion" not in text
    assert "\\input{sections/results}" in text


def test_metadata_sections_must_exist(synth):
    fresh_article(synth, metadata=_meta_with("sections: [introduction, ghost]\n"))
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "section-file-missing" in result.output


def _input_order(text: str) -> list:
    stems = re.findall(r"\\input\{sections/([a-z0-9-]+)\}", text)
    return [stem for stem in stems if stem != "abstract"]


def test_the_profile_order_is_followed_not_the_imrad_order(synth):
    sections = [
        {"title": "Methods", "aliases": [], "required": True, "words_max": None},
        {"title": "Introduction", "aliases": ["Background"], "required": True, "words_max": None},
        {"title": "Results", "aliases": [], "required": True, "words_max": None},
        {"title": "Discussion", "aliases": [], "required": True, "words_max": None},
    ]
    data = make_profile(sections=sections)
    data["evidence"] = _evidence_for(data)
    synth.write_profile(data)
    fresh_article(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 0, result.output
    assert _input_order(_main_text(synth)) == ["methods", "introduction", "results", "discussion"]


def test_metadata_sections_order_wins_over_the_profile(synth):
    fresh_article(synth, metadata=_meta_with("sections: [results, introduction]\n"))
    synth.write_profile(make_profile())
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 0, result.output
    assert _input_order(_main_text(synth)) == ["results", "introduction"]


def test_a_metadata_section_stem_that_leaves_the_folder_is_refused(synth):
    fresh_article(synth, metadata=_meta_with("sections: [../secret]\n"))
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert not (synth.ms.parent / "secret.tex").exists()


def test_a_missing_abstract_exits_one(synth):
    fresh_article(synth, remove=("sections/abstract.tex",))
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "abstract" in result.output


def test_a_missing_bibliography_exits_one(synth):
    fresh_article(synth, remove=("references.bib",))
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "references.bib" in result.output


def test_a_legacy_markdown_only_profile_is_refused(synth):
    (synth.profiles_dir / f"{SLUG}.yml").unlink()
    (synth.profiles_dir / f"{SLUG}.md").write_text("# hand written\n", encoding="utf-8")
    fresh_article(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "profile-legacy" in result.output


def test_rerendering_is_byte_identical(synth):
    fresh_article(synth)
    _drop_profile(synth)
    assert _run("manuscript", "render", RUN).exit_code == 0
    first = (synth.ms / "main.tex").read_bytes()
    assert _run("manuscript", "render", RUN).exit_code == 0
    assert (synth.ms / "main.tex").read_bytes() == first


def test_a_hand_edited_rendered_file_is_refused_and_force_keeps_a_backup(synth):
    fresh_article(synth)
    _drop_profile(synth)
    assert _run("manuscript", "render", RUN).exit_code == 0
    edited = _main_text(synth) + "\n% a hand edit\n"
    (synth.ms / "main.tex").write_text(edited, encoding="utf-8")
    refused = _run("manuscript", "render", RUN)
    assert refused.exit_code == 1, refused.output
    assert _main_text(synth) == edited
    forced = _run("manuscript", "render", RUN, "--force")
    assert forced.exit_code == 0, forced.output
    backup = synth.ms / f"main.tex.bak-{hashlib.sha256(edited.encode('utf-8')).hexdigest()[:12]}"
    assert backup.read_text(encoding="utf-8") == edited
    assert MARKER.match(_main_text(synth).split("\n", 1)[0])


def test_an_unmarked_main_tex_is_refused_and_force_keeps_it(synth):
    hand_written = "\\documentclass{article}\n"
    fresh_article(synth, extra={"main.tex": hand_written})
    _drop_profile(synth)
    refused = _run("manuscript", "render", RUN)
    assert refused.exit_code == 1, refused.output
    assert (synth.ms / "main.tex").read_text(encoding="utf-8") == hand_written
    assert _run("manuscript", "render", RUN, "--force").exit_code == 0
    backup_name = f"main.tex.bak-{hashlib.sha256(hand_written.encode('utf-8')).hexdigest()[:12]}"
    assert (synth.ms / backup_name).read_text(encoding="utf-8") == hand_written


def test_a_symlinked_main_tex_is_refused(synth, tmp_path):
    fresh_article(synth)
    _drop_profile(synth)
    target = tmp_path / "elsewhere.tex"
    target.write_text("x\n", encoding="utf-8")
    os.symlink(target, synth.ms / "main.tex")
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert target.read_text(encoding="utf-8") == "x\n"


def test_an_unknown_class_is_a_usage_error(synth):
    fresh_article(synth)
    assert _run("manuscript", "render", RUN, "--class", "nosuch").exit_code == 2


def test_a_bibtex_style_that_injects_latex_is_refused(synth):
    synth.write_profile(make_profile(bib_style="unsrt}\\input{/etc/passwd}"))
    fresh_article(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert not (synth.ms / "main.tex").exists()


def test_a_latex_option_that_injects_latex_is_refused(synth):
    synth.write_profile(_with_options(["a]{x}\\input{/etc/passwd}"]))
    fresh_article(synth)
    result = _run("manuscript", "render", RUN)
    assert result.exit_code == 1, result.output
    assert "class-option" in result.output
    assert not (synth.ms / "main.tex").exists()


def test_json_flag_prints_the_result(synth):
    fresh_article(synth)
    _drop_profile(synth)
    result = _run("manuscript", "render", RUN, "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert set(document) >= {"class", "path", "sha256", "written", "backup"}
    assert document["class"] == "article"
    assert document["written"] is True


def _meta_with(extra: str) -> str:
    from tests.research.manuscript.t7_support import GOLD_METADATA

    return GOLD_METADATA + extra


def _with_options(options: list) -> dict:
    """A profile whose latex_options carry evidence (make_profile's evidence predates the edit)."""
    data = make_profile()
    data["templates"]["latex_options"] = options
    data["evidence"] = _evidence_for(data)
    return data
