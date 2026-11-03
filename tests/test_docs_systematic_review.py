"""M-H t9 (plan 5.15, 6 t9): the systematic-review skill, docs, workflow and menu match the code.

Every `scieflow sr` invocation in the skill and the docs must resolve in the assembled click tree
(greedy walk over the subcommand names, leaf required). The two protected edits (the research
AGENTS paragraph and the root skills-table row) are pinned byte for byte. Synthetic data only.
"""

import os
import re
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from scieflow.cli import main

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "src" / "scieflow" / "research" / "skills" / "systematic-review"
SKILL = SKILL_DIR / "SKILL.md"
DOC = ROOT / "docs" / "research" / "systematic-review.md"
CLI_DOC = ROOT / "docs" / "cli.md"
MKDOCS = ROOT / "mkdocs.yml"
RESEARCH_AGENTS = ROOT / "src" / "scieflow" / "research" / "AGENTS.md"
ROOT_AGENTS = ROOT / "AGENTS.md"

# The two human-approved protected edits, exactly as applied (plan section 3).
RULE_PARAGRAPH = (
    "When they ask for a systematic or living review (a locked protocol, dual screening by two agents,\n"
    "blinded adjudication, a PRISMA flow and checklist, risk-of-bias and GRADE worksheets), follow\n"
    "`src/scieflow/research/skills/systematic-review/SKILL.md`. Its counts, Cohen's kappa and PRISMA\n"
    "numbers come only from the `uv run scieflow sr` commands; never write `sr/` files or `sr.*` events\n"
    "yourself, and never adjudicate a screening conflict (a human answers the `sr-adjudication` gate).\n"
)
SKILLS_ROW = (
    "| Run a systematic or living review (protocol lock, dual screening, PRISMA) | "
    "`skills/systematic-review/SKILL.md` |\n"
)
NAV_LINE = "      - Systematic Review: research/systematic-review.md\n"

SR_SUBCOMMANDS = (
    "protocol", "status", "search", "records", "screen", "kappa", "conflicts", "adjudicate",
    "verify", "prisma", "rob", "grade", "eval",
)

# A `scieflow sr` invocation: at the start of a line or a backtick span, optionally after `uv run`.
INVOKE = re.compile(r"(?:^|`|\$ )[ \t]*(?:uv run )?scieflow sr(?=[ \t`\n]|$)([^`\n]*)", re.M)


def _sr_group():
    return main.get_command(click.Context(main), "sr")


def _resolve(tokens):
    """Greedy walk of the click tree from `sr`. Returns (path, leaf)."""
    node = _sr_group()
    path = []
    for tok in tokens:
        if isinstance(node, click.Group) and tok in node.commands:
            node = node.commands[tok]
            path.append(tok)
        else:
            break
    return path, node


def _invocations(text):
    """(line, tokens) for each sr invocation with at least one word after `sr`. A bare group
    mention (`uv run scieflow sr` commands) names no command and is skipped."""
    out = []
    for match in INVOKE.finditer(text):
        words = [t for t in match.group(1).split() if not t.startswith(("-", "<"))]
        if words:
            out.append((match.group(0).strip(" `$"), match.group(1).split()))
    return out


def test_the_skill_folder_and_its_symlink_resolve():
    assert SKILL.is_file(), "the skill file is missing"
    link = ROOT / "skills" / "systematic-review"
    assert link.is_symlink()
    assert os.readlink(link) == "../src/scieflow/research/skills/systematic-review"
    assert link.resolve() == SKILL_DIR.resolve()


def test_every_sr_invocation_in_the_skill_and_docs_is_a_real_command():
    checked = 0
    for path in (SKILL, DOC):
        assert path.is_file(), f"{path.name} is missing"
        for line, tokens in _invocations(path.read_text(encoding="utf-8")):
            found, leaf = _resolve(tokens)
            assert found, f"{path.name}: `{line}` names no command of the sr group"
            assert not isinstance(leaf, click.Group), f"{path.name}: `{line}` stops at a group"
            checked += 1
    assert checked >= 15, "the skill and the docs should name the whole loop"


def test_every_sr_subcommand_is_documented_in_the_cli_reference():
    text = CLI_DOC.read_text(encoding="utf-8")
    start = text.index("## `scieflow sr`")
    end = text.index("\n## ", start + 5)
    section = text[start:end]
    group = _sr_group()
    assert set(SR_SUBCOMMANDS) == set(group.commands), "the sr group changed: update this list and docs"
    for name in SR_SUBCOMMANDS:
        assert name in section, f"docs/cli.md `scieflow sr` section does not mention {name}"


def test_cli_reference_section_help_matches_the_tree():
    result = CliRunner().invoke(main, ["sr", "--help"])
    assert result.exit_code == 0
    assert "Systematic review" in result.output


def test_every_scieflow_agent_run_in_the_skill_and_docs_names_the_screener_role():
    seen = 0
    for path in (SKILL, DOC):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "scieflow agent run" in line:
                seen += 1
                assert "--role research.sr-screeners" in line, f"{path.name}: {line.strip()}"
    assert seen >= 2, "the skill documents the two screener dispatches"


def test_the_skill_states_the_rules_it_depends_on():
    text = SKILL.read_text(encoding="utf-8").lower()
    for needle in (
        "staffing gate", "research.sr-screeners", "design init", "--type systematic-review",
        "design check", "design lock", "research network", "sr protocol check", "sr search",
        "sr screen prepare", "sr screen import", "sr conflicts", "sr rob", "sr grade",
        "sr prisma <slug> --write", "sr verify", "sr/reports/", "never edit `sr/` files",
        "never adjudicate", "never compute counts or kappa",
    ):
        assert needle in text, f"the skill does not state {needle!r}"


def test_the_docs_page_lists_the_residuals_r1_to_r8():
    text = DOC.read_text(encoding="utf-8")
    for n in range(1, 9):
        assert re.search(rf"\bR{n}\b", text), f"docs/research/systematic-review.md lacks residual R{n}"


def test_the_mkdocs_nav_has_one_line_for_the_page():
    text = MKDOCS.read_text(encoding="utf-8")
    assert text.count(NAV_LINE) == 1
    assert DOC.is_file()


def test_the_research_agents_paragraph_is_the_approved_text_byte_for_byte():
    text = RESEARCH_AGENTS.read_text(encoding="utf-8")
    assert text.count(RULE_PARAGRAPH) == 1


def test_the_root_skills_row_is_the_approved_text_byte_for_byte():
    text = ROOT_AGENTS.read_text(encoding="utf-8")
    assert text.count(SKILLS_ROW) == 1
    assert "skills/systematic-review/SKILL.md" in text


@pytest.mark.parametrize("path", [SKILL, DOC])
def test_no_docs_or_skill_text_writes_patient_data_or_credentials(path):
    text = path.read_text(encoding="utf-8").lower()
    for banned in ("api_key=", "password=", "token_file:", "/home/jedimik/"):
        assert banned not in text
