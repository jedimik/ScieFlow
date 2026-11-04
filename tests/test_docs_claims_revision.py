"""M-G t9 (plan 5.15-5.16, 6 t9): the claim-ledger and journal-revision docs, the skill, the
skills symlink and the two operator-applied protected edits match the code.

Command flags and rule ids are read from the CLI and the claims/revision modules, not copied from
the plan. The rule 12 text and the skills-table row are the operator-approved protected edits,
pinned byte for byte (the same pattern as tests/test_docs_journal.py).
"""

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow.cli import main

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DOC = ROOT / "docs" / "research" / "claims.md"
REVISION_DOC = ROOT / "docs" / "research" / "revision.md"
MANUSCRIPT_DOC = ROOT / "docs" / "research" / "manuscript.md"
DATA_PACKAGES_DOC = ROOT / "docs" / "research" / "data-packages.md"
CLI_DOC = ROOT / "docs" / "cli.md"
MKDOCS = ROOT / "mkdocs.yml"
SKILLS = ROOT / "src" / "scieflow" / "research" / "skills"
JOURNAL_REVISION_SKILL = SKILLS / "journal-revision" / "SKILL.md"
PAPER_DRAFT_SKILL = SKILLS / "paper-draft" / "SKILL.md"
JOURNAL_RESEARCH_SKILL = SKILLS / "journal-research" / "SKILL.md"
RESEARCH_AGENTS = ROOT / "src" / "scieflow" / "research" / "AGENTS.md"
ROOT_AGENTS = ROOT / "AGENTS.md"
CLAIMS_SRC = ROOT / "src" / "scieflow" / "research" / "manuscript" / "claims"
REVISION_SRC = ROOT / "src" / "scieflow" / "research" / "revision"

# plan 5.15, as the CLI implements it (revision show has --json only; the plan's --round is absent)
FLAGS = {
    "claims build": ("--main", "--json", "--repin", "--repin-claim"),
    "claims show": ("--id", "--json"),
    "claims check": ("--main", "--json", "--no-write"),
    "claims waive": ("--code", "--reason"),
    "point add": ("--from-package",),
    "point list": ("--json",),
    "diff": ("--from", "--to", "--main", "--json"),
    "revision ingest": ("--letter", "--round", "--decision"),
    "revision prompt": ("--round", "--agent", "--write"),
    "revision adopt": ("--round", "--draft", "--replace"),
    "revision respond": ("--round", "--force"),
    "revision coverage": ("--round", "--json"),
    "revision letter": ("--round", "--force"),
    "revision show": ("--json",),
    "crate": ("--json", "--no-write"),
}

# plan 5.6 rule ids (plus the two extra ids the code emits under C001)
CLAIM_RULES = (
    "orphan", "ref-unresolved", "ref-ambiguous", "ref-unsafe", "ref-too-long", "ref-unsafe-doi",
    "number-missing", "artifact-altered", "evidence-changed", "source-corrected", "blocked-reference",
    "code-dirty", "code-not-ancestor", "data-unreleased", "code-state-unknown", "run-unverifiable",
    "doi-missing", "doi-unselected", "doi-status-unknown", "number-uncheckable",
    "number-unmatched-cited", "status-unreadable", "ledger-stale", "claims-limit",
)
# plan 5.10-5.12 letter, adoption and coverage rule ids
REVISION_RULES = (
    "letter-changed", "blocks-differ", "review-invalid", "quote-not-in-letter", "letter-uncovered",
    "not-comment-conflict", "unknown-comment", "missing", "no-response", "no-change-line",
    "both-change-lines", "no-change-reason", "change-path", "change-not-found",
)

RULE12 = """\
12. **Claim ledger and journal revision** (`docs/research/claims.md`,
    `docs/research/revision.md`). Every number in a manuscript traces to
    `% source: [data:<id>]`, `[run:<id>]` or a cited DOI, and `uv run
    scieflow manuscript claims check <slug>` proves it mechanically: a
    number missing from its evidence, an orphan claim, stale or altered
    evidence, a retracted or blocking reference, or a run from changed
    code is an error. Fix the text or the evidence; never edit
    `report/claims/`, never change a number only to make it match, and
    never waive a finding (`scieflow manuscript claims waive` is the
    user's). A journal's decision letter enters a run only through `uv run
    scieflow manuscript revision ingest` (the user's). Structure it from
    `uv run scieflow manuscript revision prompt` (`--role
    research.submitter`), quoting every reviewer comment verbatim; the
    coordinator adopts the result with `revision adopt`. Answer every
    comment in `revision/round-<N>/response.md` until `uv run scieflow
    manuscript revision coverage <slug> --round <N>` passes.
"""
WORKFLOW_ROW = "| Claim ledger and journal revision | `src/scieflow/research/skills/journal-revision/SKILL.md` |"
ROUTE = """\
When they share a journal's decision letter, ask to answer reviewers, or ask whether every number in a
manuscript traces to its evidence, follow `src/scieflow/research/skills/journal-revision/SKILL.md`.
"""
ROOT_ROW = "| Check a manuscript's claims; answer a journal decision letter | `skills/journal-revision/SKILL.md` |"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _docs_text() -> str:
    return _read(CLAIMS_DOC) + "\n" + _read(REVISION_DOC)


def _code_text(folder: Path) -> str:
    return "\n".join(_read(p) for p in sorted(folder.glob("*.py")))


# --- pages, navigation, CLI parity ---


def test_claims_and_revision_pages_exist_and_are_in_the_nav():
    assert CLAIMS_DOC.is_file()
    assert REVISION_DOC.is_file()
    nav = _read(MKDOCS)
    assert "research/claims.md" in nav
    assert "research/revision.md" in nav


@pytest.mark.parametrize("command", sorted(FLAGS))
def test_every_documented_flag_exists_in_the_cli_help(command):
    result = CliRunner().invoke(main, ["manuscript", *command.split(), "--help"])
    assert result.exit_code == 0, result.output
    for flag in FLAGS[command]:
        assert flag in result.output, (command, flag)


@pytest.mark.parametrize("command", sorted(FLAGS))
def test_docs_name_every_command_and_flag(command):
    text = _docs_text()
    assert f"scieflow manuscript {command}" in text, command
    for flag in FLAGS[command]:
        assert flag in text, (command, flag)


def test_cli_and_manuscript_docs_list_the_new_surface():
    cli = _read(CLI_DOC)
    assert "scieflow manuscript claims" in cli
    assert "scieflow manuscript revision" in cli
    assert "claims check" in _read(DATA_PACKAGES_DOC)
    assert "claims check" in _read(MANUSCRIPT_DOC)
    assert "claims.md" in _read(MANUSCRIPT_DOC)


# --- claim check: codes, rule ids, waivers, numbers, L2, verify gate ---


@pytest.mark.parametrize("rule", CLAIM_RULES)
def test_claim_rule_ids_exist_in_code_and_docs(rule):
    assert f'"{rule}"' in _code_text(CLAIMS_SRC) or f"'{rule}'" in _code_text(CLAIMS_SRC), rule
    assert f"`{rule}`" in _docs_text(), rule


@pytest.mark.parametrize("code", ["C001", "C002", "C003", "C005", "C006"])
def test_check_codes_are_documented(code):
    assert code in _read(CLAIMS_DOC)


def test_c004_is_documented_as_reserved_and_never_emitted():
    assert "C004" not in _code_text(CLAIMS_SRC)
    assert "C004" in _read(CLAIMS_DOC)
    assert "reserved" in _read(CLAIMS_DOC)


def test_waiver_limits_are_documented():
    text = _read(CLAIMS_DOC)
    assert "SCIEFLOW_DISPATCH" in text
    assert "10 to 500 characters" in text
    assert "not waivable" in text
    assert "C005" in text and "data-unreleased" in text
    assert "human" in text


def test_number_rule_is_documented():
    text = _read(CLAIMS_DOC)
    assert "round_half_up" in text and "round_half_even" in text
    assert "1900" in text  # years are not checked
    assert "number-unmatched-cited" in text


def test_l2_and_no_egress_rules_are_documented():
    text = _read(CLAIMS_DOC)
    assert "L2" in text
    assert "data-unreleased" in text
    assert "no network call" in text


def test_verify_gate_is_documented():
    text = _read(CLAIMS_DOC)
    assert "run mark" in text
    assert "verify done" in text
    assert "fail closed" in text
    assert "not wired" not in text


# --- plan section 8 residuals (R2, R4, R5, R9) must be named in the docs ---


def test_claims_residuals_r2_r5_r9_are_documented():
    text = _read(CLAIMS_DOC)
    assert "forg" in text, "R2 file-based integrity (forged files or events)"
    assert "RO-Crate" in text and "validator" in text, "R5 structural crate check only"
    assert "shallow" in text, "R9 shallow clones and code-not-ancestor"


def test_revision_residuals_r2_and_r4_are_documented():
    text = _read(REVISION_DOC)
    assert "forg" in text, "R2 for the revision events file"
    assert "latexdiff" in text and "not installed" in text, "R4 latexdiff not tested here"


def test_point_source_is_documented_as_the_code_takes_it():
    text = _read(REVISION_DOC)
    assert "otherwise from the current manuscript" not in text
    assert "refuses the ingest" in text


# --- decision letter, adoption and coverage ---


@pytest.mark.parametrize("rule", REVISION_RULES)
def test_revision_rule_ids_exist_in_code_and_docs(rule):
    code = _code_text(REVISION_SRC)
    assert f'"{rule}"' in code, rule
    assert f"`{rule}`" in _read(REVISION_DOC), rule


def test_letter_formats_and_size_are_documented():
    text = _read(REVISION_DOC)
    for suffix in (".txt", ".md", ".html", ".htm"):
        assert suffix in text, suffix
    assert "1 MB" in text
    assert "--replace" in text and "--force" in text


def test_response_starter_placeholders_and_files_are_documented():
    text = _read(REVISION_DOC)
    assert "Response: [write your response]" in text
    assert "Change: [file" in text
    assert "No change: [why]" in text
    assert "response.md" in text and "adopted.json" in text and "coverage.json" in text


def test_dispatch_refusals_are_documented_for_revision():
    text = _read(REVISION_DOC)
    assert "dispatch" in text
    assert "ingest" in text and "adopt" in text


# --- skill, symlink, the other skills that point at it ---


def test_journal_revision_skill_names_the_commands_and_limits():
    text = _read(JOURNAL_REVISION_SKILL)
    for phrase in (
        "scieflow manuscript claims build",
        "scieflow manuscript claims check",
        "scieflow manuscript claims waive",
        "scieflow manuscript revision ingest",
        "scieflow manuscript revision prompt",
        "--role research.submitter",
        "verbatim",
        "scieflow manuscript revision adopt",
        "scieflow manuscript revision respond",
        "scieflow manuscript revision coverage",
        "scieflow manuscript revision letter",
        "scieflow manuscript point",
        "scieflow manuscript diff",
        "scieflow manuscript crate",
    ):
        assert phrase in text, phrase
    assert "**Never waive" in text
    assert "Never ingest a letter or adopt a draft" in text
    assert "Never edit `report/claims/`" in text


def test_skills_symlink_resolves_to_the_skill():
    link = ROOT / "skills" / "journal-revision"
    assert link.is_symlink()
    assert os.readlink(link) == "../src/scieflow/research/skills/journal-revision"
    assert (link / "SKILL.md").is_file()


def test_paper_draft_phase_5_and_7_use_the_claim_check():
    text = _read(PAPER_DRAFT_SKILL)
    assert "scieflow manuscript claims build" in text
    assert "scieflow manuscript claims check" in text
    assert "3-5 numeric" not in text
    assert "claim errors" in text


def test_journal_research_points_at_the_revision_skill():
    assert "journal-revision/SKILL.md" in _read(JOURNAL_RESEARCH_SKILL)


# --- protected edits (operator-applied; byte-exact in the regions they add) ---


def test_protected_rule_12_workflow_row_and_routing_are_byte_exact():
    research = _read(RESEARCH_AGENTS)
    assert RULE12 in research
    assert WORKFLOW_ROW in research
    assert ROUTE in research


def test_protected_skills_table_row_is_byte_exact():
    assert ROOT_ROW in _read(ROOT_AGENTS)
