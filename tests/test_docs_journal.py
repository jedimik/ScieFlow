"""M-F t9 (plan 5.16, 6 t9): the journal and manuscript docs, the skills and the protected rule
match the code.

Rule ids and flags are read from the code, not copied from the plan: a renamed id or flag fails
here. The rule 11 text and the skills-table row are the operator-approved protected edits, pinned
byte for byte in the regions they add.
"""

import os
import re
from pathlib import Path

from click.testing import CliRunner

from scieflow.cli import main

ROOT = Path(__file__).resolve().parents[1]
JOURNALS = ROOT / "docs" / "research" / "journals.md"
MANUSCRIPT = ROOT / "docs" / "research" / "manuscript.md"
CLI_DOC = ROOT / "docs" / "cli.md"
PAPER_REVIEW_DOC = ROOT / "docs" / "research" / "workflows" / "paper-review.md"
PAPER_DRAFT_DOC = ROOT / "docs" / "research" / "workflows" / "paper-draft.md"
CONFIG_DOC = ROOT / "docs" / "research" / "configuration.md"
ARCH_DOC = ROOT / "docs" / "research" / "architecture.md"
EXAMPLES_DOC = ROOT / "docs" / "research" / "examples.md"
SKILLS = ROOT / "src" / "scieflow" / "research" / "skills"
JOURNAL_SRC = ROOT / "src" / "scieflow" / "research" / "journal"
MANUSCRIPT_SRC = ROOT / "src" / "scieflow" / "research" / "manuscript"

JOURNAL_COMMANDS = ("research", "add-source", "prompt", "adopt", "show", "list", "validate", "diff", "style")
MANUSCRIPT_COMMANDS = ("check", "checklist-prompt", "render", "build", "package", "letter")

JOURNAL_FLAGS = (
    "--journal", "--issn", "--url", "--as-of", "--slug", "--restart", "--retry-failed",
    "--refresh-of", "--file", "--note", "--for", "--cross-check", "--write", "--draft",
    "--replace", "--as-agent", "--article-type", "--prompt", "--json",
)
MANUSCRIPT_FLAGS = (
    "--journal", "--article-type", "--guideline", "--main", "--no-write", "--write", "--class",
    "--force", "--engine", "--timeout", "--kind", "--json",
)

# plan 5.5: the profile validation rules (errors and warnings)
PROFILE_RULES = (
    "unknown-key", "missing-key", "type", "enum", "range", "article-type", "evidence-missing",
    "evidence-source", "evidence-quote", "evidence-number", "evidence-name", "guideline-unknown",
    "study-type-unknown", "conflict", "facts", "stale", "sources-unavailable",
)
# plan 5.9: the manuscript check rules
CHECK_RULES = (
    "profile-missing", "profile-legacy", "profile-invalid", "profile-stale", "article-type",
    "abstract-missing", "abstract-words", "abstract-structure", "title-words", "main-words",
    "section-words", "keywords", "highlights", "figures", "tables", "display-items",
    "references-count", "citation-missing", "citation-uncited", "reference-year", "section-missing",
    "section-order", "declaration-missing", "orcid-invalid", "ror-invalid", "credit-invalid",
    "corresponding", "template-class", "template-default", "bib-style", "word-first",
)
# plan 5.10: the reporting-checklist rules
CHECKLIST_RULES = (
    "guideline-unknown", "guideline-not-in-design", "guideline-unverified", "guideline-item-missing",
    "judgement-stale", "judgement-invalid", "design-unreadable",
)

RULE11 = """\
11. **Target journal and manuscript check** (`docs/research/journals.md`).
    A journal profile is `config/journals/<journal>.yml`, written only by
    `uv run scieflow journal adopt` after `uv run scieflow journal research`
    (the user names the journal; guideline pages are downloaded only from
    the addresses a human approved in the run's `journal-sources` gate).
    Never write or edit a profile `.yml` yourself, never answer a
    `journal-sources` gate, and never add a source page (`scieflow journal
    add-source` is the user's). Every value in a profile draft carries a
    verbatim quote from a stored source. Limits, structure, reference style
    and the reporting guideline come from the profile; before calling a
    manuscript submission-ready run `uv run scieflow manuscript check
    <slug>`, and build the package only with `uv run scieflow manuscript
    package <slug>`.
"""
ROW = "| Research a target journal; check and package a manuscript | `skills/journal-research/SKILL.md` |"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text of one `## ` section of cli.md (up to the next `## ` heading)."""
    assert f"\n{heading}\n" in text, heading
    return text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def _help_text() -> str:
    """The --help output of every journal and manuscript command, concatenated."""
    chunks = []
    for group, commands in (("journal", JOURNAL_COMMANDS), ("manuscript", MANUSCRIPT_COMMANDS)):
        chunks.append(CliRunner().invoke(main, [group, "--help"], catch_exceptions=False).output)
        for command in commands:
            result = CliRunner().invoke(main, [group, command, "--help"], catch_exceptions=False)
            assert result.exit_code == 0, (group, command)
            chunks.append(result.output)
    return "\n".join(chunks)


def _source(directory: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(directory.glob("*.py")))


# --- existence and navigation ---------------------------------------------------------------


def test_the_two_pages_exist_and_are_in_the_nav():
    assert JOURNALS.is_file() and MANUSCRIPT.is_file()
    nav = _text(ROOT / "mkdocs.yml")
    assert "Target Journals: research/journals.md" in nav
    assert "Manuscript Check and Package: research/manuscript.md" in nav


# --- journals.md ----------------------------------------------------------------------------


def test_journals_doc_names_every_journal_command():
    text = _text(JOURNALS)
    for command in JOURNAL_COMMANDS:
        assert f"scieflow journal {command}" in text, command


def test_journals_doc_names_the_flags_of_its_commands():
    text = _text(JOURNALS)
    for flag in JOURNAL_FLAGS:
        if flag == "--article-type":
            continue  # shown by `journal show` and `manuscript`, not by `journal research`
        assert flag in text, flag


def test_every_documented_flag_exists_in_help():
    """Walk the --help of every command: each flag named in the docs must be a real flag."""
    help_text = _help_text()
    documented = set()
    for path in (JOURNALS, MANUSCRIPT, CLI_DOC):
        text = _text(path)
        if path == CLI_DOC:
            text = _section(text, "## `scieflow journal`") + _section(text, "## `scieflow manuscript`")
        documented |= set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", text))
    missing = sorted(flag for flag in documented if flag not in help_text and flag != "--help")
    assert not missing, missing


def test_journals_doc_names_every_rule_id_of_the_profile_format():
    text = _text(JOURNALS)
    source = _source(JOURNAL_SRC)
    for rule in PROFILE_RULES:
        assert f"`{rule}`" in text, rule
        assert f'"{rule}"' in source, rule


def test_journals_doc_states_the_sourced_value_rule_and_the_gate_authority():
    text = _text(JOURNALS)
    for phrase in ("verbatim quote", "20 characters", "journal-sources", "human", "add-source",
                   "config/journals/", "exit"):
        assert phrase in text, phrase


def test_journals_doc_says_how_to_switch_guideline_downloads_on():
    text = _text(JOURNALS)
    for phrase in ("journal-guidelines", "enabled: true", "config/integrations.yml", "off by default"):
        assert phrase in text, phrase


def test_journals_doc_has_the_residuals_and_the_limits():
    text = _text(JOURNALS)
    assert "## Known limits" in text
    for number in range(1, 10):
        assert f"R{number}" in text, f"R{number}"


# --- manuscript.md --------------------------------------------------------------------------


def test_manuscript_doc_names_every_manuscript_command():
    text = _text(MANUSCRIPT)
    for command in MANUSCRIPT_COMMANDS:
        assert f"scieflow manuscript {command}" in text, command


def test_manuscript_doc_names_every_check_rule_and_checklist_rule():
    text = _text(MANUSCRIPT)
    source = _source(MANUSCRIPT_SRC)
    for rule in CHECK_RULES + CHECKLIST_RULES:
        assert f"`{rule}`" in text, rule
        assert f'"{rule}"' in source, rule


def test_manuscript_doc_defines_the_word_count_and_the_main_text():
    text = _text(MANUSCRIPT)
    for phrase in ("Word count", "Main text", "Abstract and title are counted on their own",
                   "references", "acknowledgements", "competing interests"):
        assert phrase in text, phrase


def test_manuscript_doc_has_the_docx_note_and_the_residuals():
    text = _text(MANUSCRIPT)
    assert "DOCX" in text and "M-F-D1" in text
    assert "## Known limits" in text
    for number in range(1, 10):
        assert f"R{number}" in text, f"R{number}"


def test_manuscript_doc_states_the_build_safety_rules():
    text = _text(MANUSCRIPT)
    for phrase in ("-no-shell-escape", "scratch", "latexmk", "source tree hash", "pdf_sha256",
                   "-norc", "openin_any=p", "openout_any=p"):
        assert phrase in text, phrase


def test_r7_does_not_claim_tex_reads_whatever_the_user_can_read():
    """The build pins openin_any=p and -norc (build.py:180-181, 236); the docs must say so, not overstate reads."""
    for path in (MANUSCRIPT, JOURNALS):
        text = _text(path)
        assert "TeX can read files the user can read" not in text, path
        assert "TeX can read files that the user can read" not in text, path
    assert "paranoid" in _text(MANUSCRIPT)


def test_adopt_is_recorded_as_agent_when_the_coordinator_runs_it():
    """Without --as-agent adopt.py records adopted_by=human (adopt.py:108,194); the coordinator is an agent."""
    assert "journal adopt <run> --as-agent" in _text(JOURNALS)
    assert "journal adopt <run> --as-agent" in _text(SKILLS / "journal-research" / "SKILL.md")
    assert "journal adopt <run> --as-agent" in _text(PAPER_REVIEW_DOC)


# --- docs/cli.md ----------------------------------------------------------------------------


def test_cli_reference_has_journal_and_manuscript_sections_after_design():
    text = _text(CLI_DOC)
    journal_section = _section(text, "## `scieflow journal`")
    manuscript_section = _section(text, "## `scieflow manuscript`")
    for command in JOURNAL_COMMANDS:
        assert f"scieflow journal {command}" in journal_section, command
    for command in MANUSCRIPT_COMMANDS:
        assert f"scieflow manuscript {command}" in manuscript_section, command
    assert "journals.md" in journal_section
    assert "manuscript.md" in manuscript_section
    assert text.index("## `scieflow journal`") > text.index("## `scieflow design`")
    assert text.index("## `scieflow journal`") < text.index("## `scieflow run`")


# --- workflow and reference pages -----------------------------------------------------------


def test_workflow_and_reference_pages_point_to_the_new_flow():
    review = _text(PAPER_REVIEW_DOC)
    draft = _text(PAPER_DRAFT_DOC)
    assert "config/journals/<slug>.md" not in review
    assert "journals.md" in review
    assert "manuscript render" in draft and "manuscript package" in draft
    assert "journals.md" in draft
    assert "journal:" in _text(CONFIG_DOC)
    assert "journals.md" in _text(CONFIG_DOC)
    assert "<slug>.yml" in _text(ARCH_DOC)
    assert "config/journals/nature-methods.md" not in _text(EXAMPLES_DOC)


# --- agent rule 11 and the skills -----------------------------------------------------------


def test_protected_rule_11_and_the_skills_row_are_byte_exact():
    research = _text(ROOT / "src" / "scieflow" / "research" / "AGENTS.md")
    root = _text(ROOT / "AGENTS.md")
    assert RULE11 in research
    assert ROW in root
    assert "src/scieflow/research/skills/journal-research/SKILL.md" in research


def test_journal_research_skill_resolves_and_names_the_protocol():
    link = ROOT / "skills" / "journal-research"
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(SKILLS / "journal-research")
    text = _text(SKILLS / "journal-research" / "SKILL.md")
    for phrase in ("journal research", "journal-sources", "add-source", "--role research.journal-profile",
                   "--cross-check", "journal adopt", "manuscript check", "checklist-prompt",
                   "--role research.reviewer", "manuscript render", "manuscript build",
                   "manuscript package", "never"):
        assert phrase in text, phrase
    assert "human" in text.lower()


def test_paper_review_journal_phase_points_to_the_skill_and_no_longer_writes_md():
    text = _text(SKILLS / "paper-review" / "SKILL.md")
    assert "journal-research/SKILL.md" in text
    assert "output: config/journals/" not in text
    assert "journal show" in text and "--prompt" in text


def test_paper_draft_names_the_manuscript_commands_and_the_style_file():
    text = _text(SKILLS / "paper-draft" / "SKILL.md")
    for phrase in ("scieflow manuscript render", "scieflow manuscript build", "scieflow manuscript check",
                   "scieflow manuscript package", "--style-file journal/figure-style.yml",
                   "journal-research/SKILL.md", "journal show"):
        assert phrase in text, phrase
