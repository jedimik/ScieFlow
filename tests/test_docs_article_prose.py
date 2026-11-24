"""The article-prose guide and the places that send a writer to it.

`src/scieflow/research/templates/article-prose.md` is the one place that says how article text is
written. A guide nobody is sent to changes nothing, so these tests keep its pointers alive: the
outline plans an argument per paragraph, the draft prompt pastes the guide, the reviser and the
reviewer persona carry it into every review loop, the merge runs the signals, and a docs page
explains it to people. The last tests pin what the `% argues:` line relies on: the claim ledger
and the word count read a manuscript the same with and without it.
"""

import re
from pathlib import Path
from string import Template

from scieflow.research.manuscript import tex
from scieflow.research.manuscript.claims import units as units_mod

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src" / "scieflow" / "research" / "templates"
GUIDE_PATH = "src/scieflow/research/templates/article-prose.md"
GUIDE = ROOT / GUIDE_PATH
SCRIPT_PATH = "scripts/prose_signals.py"
PAPER_DRAFT_SKILL = ROOT / "src" / "scieflow" / "research" / "skills" / "paper-draft" / "SKILL.md"
PERSONA = TEMPLATES / "adversarial-review.md"
BLIND = TEMPLATES / "review-pass-blind.md"
REVISE = TEMPLATES / "revise-accepted.md"
DOC = ROOT / "docs" / "research" / "article-prose.md"
PAPER_DRAFT_DOC = ROOT / "docs" / "research" / "workflows" / "paper-draft.md"
MKDOCS = ROOT / "mkdocs.yml"
NAV_LINE = "      - Article Prose: research/article-prose.md\n"
PROVES_NOTHING = "A clean report proves nothing"


def _read(path):
    return path.read_text(encoding="utf-8")


def _block(text, first_line):
    """The fenced prompt block of the skill that starts with `first_line`."""
    start = text.index(first_line)
    return text[start : text.index("```", start)]


def _phase(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:end] if end != -1 else text[start:]


# --- the guide ---------------------------------------------------------------------------------


def test_the_guide_names_the_failure_its_opposite_and_the_way_out():
    text = _read(GUIDE)
    assert text.startswith("# Article prose: every paragraph argues\n")
    for word in ("inventory", "fog", "ladder", "`% argues:`", "so what"):
        assert word in text.lower(), f"the guide lost its leading word {word!r}"


def test_the_guide_is_short_enough_to_be_pasted_into_every_draft_prompt():
    text = _read(GUIDE)
    assert len(text.splitlines()) <= 140, "the guide grew past 140 lines: move reference out, do not add"
    assert max(len(line) for line in text.splitlines()) <= 120


def test_the_guide_can_be_pasted_through_a_string_template():
    assert "$" not in _read(GUIDE)            # core/review fills its prompts with string.Template


def test_the_guide_has_no_line_a_dispatch_could_read_as_a_prompt_header():
    for line in _read(GUIDE).splitlines():
        assert not re.match(r"(output|kind):", line), line


def test_every_repository_path_the_guide_names_exists():
    paths = set(re.findall(r"`((?:src|scripts|docs)/[\w./-]+)`", _read(GUIDE)))
    assert SCRIPT_PATH in paths
    for path in paths:
        assert (ROOT / path).exists(), f"the guide names {path}, which does not exist"


def test_the_guide_says_what_the_signals_cannot_do():
    assert PROVES_NOTHING in _read(GUIDE)


def test_the_guide_carries_what_the_trial_with_the_writer_model_showed():
    text = _read(GUIDE)
    assert "four to eight sentences" in text            # one paragraph per claim is a list in disguise
    assert "Keep to the length your task sets" in text  # an argued text grows
    assert "manifest" in text                           # words of the task leaked into the text
    assert "A consequence is a claim as well" in text   # a fact without a consequence got one invented


# --- paper-draft: where the text is planned, written, merged ------------------------------------


def test_the_outline_prompt_plans_one_argument_per_paragraph():
    block = _block(_read(PAPER_DRAFT_SKILL), "# ScieFlow research sub-agent task: article outline")
    assert "ARGUES:" in block
    assert "plan few of them" in block                  # a paragraph per claim is a list in disguise
    assert "each ending with\nits evidence ref in square brackets" in block or \
        "each ending with its evidence ref in square brackets" in block       # provenance rule kept


def test_the_draft_prompt_pastes_the_guide_and_asks_for_the_argues_line():
    block = _block(_read(PAPER_DRAFT_SKILL), "# ScieFlow research sub-agent task: full article draft")
    assert f"PROSE GUIDE: <paste {GUIDE_PATH}>" in block
    assert "% argues:" in block
    assert block.index("HARD RULES:") < block.index("PROSE:") < block.index("OUTLINE:")
    assert "% source: [data:<id>]" in block                                   # provenance rule kept


def test_intake_asks_once_for_a_model_text():
    phase = _phase(_read(PAPER_DRAFT_SKILL), "## Phase 1 — intake")
    assert "**Model text**" in phase and "inputs/style/" in phase
    assert "their own earlier version" in phase     # whose sentences may be reused, and whose may not


def test_the_merge_judges_the_prose_and_runs_the_signals_before_verify():
    text = _read(PAPER_DRAFT_SKILL)
    merge = _phase(text, "## Phase 5 — merge")
    assert GUIDE_PATH in merge
    assert f"uv run {SCRIPT_PATH} --argues" in merge
    assert PROVES_NOTHING.lower() in merge.lower()
    assert SCRIPT_PATH not in _phase(text, "## Phase 6 — verify")   # the text is final before it is checked


def test_every_script_the_skill_runs_exists():
    scripts = set(re.findall(r"uv run (scripts/[\w./-]+\.py)", _read(PAPER_DRAFT_SKILL)))
    assert SCRIPT_PATH in scripts
    for path in scripts:
        assert (ROOT / path).is_file(), path


# --- the review loop: persona and reviser --------------------------------------------------------


def test_the_reviewer_persona_reads_as_the_journals_reader():
    text = _read(PERSONA)
    assert "- Read as the journal's reader:" in text
    assert "the conclusion it owes the reader" in text
    assert "Say in your summary what the manuscript\n  argues." in text       # the whole text gets the reader's test
    assert "You are a highly demanding senior researcher" in text            # the persona is kept


def test_the_reviser_is_sent_to_the_guide():
    text = _read(REVISE)
    assert GUIDE_PATH in text
    assert "% argues:" in text
    assert "minimal diffs" in text                                           # the rule it had is kept


def test_the_two_review_templates_still_fill_in():
    for path in (BLIND, REVISE):
        text = _read(path)
        names = set(re.findall(r"\$\{([a-z_]+)\}", text))
        filled = Template(text).substitute(dict.fromkeys(names, "x"))        # a stray dollar sign raises
        assert "$" not in filled
    assert "$" not in _read(PERSONA)


# --- the docs page -------------------------------------------------------------------------------


def test_the_docs_page_explains_the_guide_to_people():
    text = _read(DOC)
    for needed in (GUIDE_PATH, SCRIPT_PATH, "`% argues:`", "inputs/style/", PROVES_NOTHING):
        assert needed in text, f"docs/research/article-prose.md lacks {needed!r}"


def test_the_mkdocs_nav_has_one_line_for_the_page():
    assert _read(MKDOCS).count(NAV_LINE) == 1
    assert DOC.is_file()


def test_the_paper_draft_page_links_the_docs_page():
    assert "(../article-prose.md)" in _read(PAPER_DRAFT_DOC)


def test_the_docs_page_reports_the_calibration_as_one_case():
    text = _read(DOC)
    assert "one article" in text          # the thresholds come from one case and the page says so


def test_the_docs_page_reports_the_trial_and_what_it_does_not_show():
    text = _read(DOC)
    assert "## A trial with a writer model" in text
    assert "two drafts per cell" in text
    assert "not by a human reader" in text


# --- what the argues line relies on --------------------------------------------------------------

HEAD = "\\documentclass{article}\n\\begin{document}\n\\section{Results}\n"
PARAGRAPH = (
    "Because every output is resolved from one study file, a run can be repeated from it. % source: [data:r1]\n"
    "The phantom run took 14 minutes on 4 cores. % source: [data:r2]\n"
)
ARGUES = "% argues: one study file is all a user edits, so a study can be repeated from that file alone\n"
TAIL = "\n\\end{document}\n"


def _units(folder, body):
    folder.mkdir()
    (folder / "main.tex").write_text(HEAD + body + TAIL, encoding="utf-8")
    tree = tex.read_tree(folder, "main.tex", keep_comments=True)
    return [(unit.text, unit.comments) for unit in units_mod.extract_units(tree)]


def test_the_claim_ledger_reads_the_same_units_with_and_without_an_argues_line(tmp_path):
    plain = _units(tmp_path / "plain", PARAGRAPH)
    tagged = _units(tmp_path / "tagged", ARGUES + PARAGRAPH)
    assert len(plain) == 2
    assert tagged == plain


def test_an_argues_line_adds_no_words_to_the_count():
    assert tex.count_words(HEAD + ARGUES + PARAGRAPH + TAIL) == tex.count_words(HEAD + PARAGRAPH + TAIL)
