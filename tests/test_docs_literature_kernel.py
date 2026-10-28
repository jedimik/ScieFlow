"""Docs, nav, skill and research rule 6 match the literature-kernel code (M-C1 t6, Part A 5.12).

Every mode, command and exit code named here is the one the code defines; a renamed one fails.
"""

from pathlib import Path

import click

from scieflow.core import network
from scieflow.research.cli import research

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "research" / "literature-kernel.md"
SKILL = ROOT / "src" / "scieflow" / "research" / "skills" / "lit-review" / "SKILL.md"
RESEARCH_AGENTS = ROOT / "src" / "scieflow" / "research" / "AGENTS.md"

# The human-approved rule 6 text (plan section 3), byte for byte.
APPROVED_RULE_6 = """\
6. Paper discovery uses the shared search commands (same sources for every
   agent): `scieflow research search all` (every enabled source at once,
   merged and ranked; the output names each source's status), or one source:
   `scieflow research search openalex`, `scieflow research search arxiv`,
   `scieflow research search europepmc`, `scieflow research search crossref`.
   Never invent papers, DOIs, or citation counts — every paper you report
   must come from a search-command result. Searches run through the provider
   Gateway: exit 3 means refused (the query failed the leak check, or the
   source is switched off). Rephrase the query; never retry around a refusal.
   Exit 6 means the run's network mode (`scieflow research network <slug>`)
   is `replay` or `offline` and no recorded snapshot answers the request:
   nothing was sent. Report it; never change the network mode to get past
   it (you may only switch a run to `replay` or `offline`, never back).
"""


def _doc():
    return DOC.read_text(encoding="utf-8")


def _exit_rows(text):
    """The rows of the Exit codes table of the kernel page."""
    section = text.split("\n## Exit codes\n", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("|") and "---" not in line]


def test_the_page_exists_and_is_in_the_nav():
    assert DOC.is_file(), "docs/research/literature-kernel.md is missing"
    assert "research/literature-kernel.md" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


def test_the_page_names_every_network_mode():
    text = _doc()
    for mode in network.MODES:
        assert f"`{mode}`" in text, f"literature-kernel.md does not name the `{mode}` mode"


def test_the_page_states_the_l2_default_and_its_refusal():
    text = _doc()
    assert "L2" in text and "`replay`" in text
    assert "`l2-default`" in text, "the L2 default origin is not named"


def test_the_page_names_every_command_it_documents():
    text = _doc()
    for command in ("scieflow research search all", "scieflow research network",
                    "scieflow research snapshots list", "scieflow research snapshots verify",
                    "scieflow research snapshots copy"):
        assert command in text, f"literature-kernel.md does not document `{command}`"


def test_the_documented_commands_exist_in_the_cli():
    commands = research.commands
    assert "network" in commands and "snapshots" in commands and "search" in commands
    assert "all" in commands["search"].commands
    for name in ("openalex", "arxiv", "europepmc", "crossref"):
        assert name in commands["search"].commands
    snaps = commands["snapshots"]
    assert isinstance(snaps, click.Group)
    assert {"list", "verify", "copy"} <= set(snaps.commands)


def test_the_page_has_an_exit_code_table_with_codes_3_and_6():
    rows = _exit_rows(_doc())
    assert any(row.split("|")[1].strip() == "`3`" for row in rows), "no row for exit code 3"
    assert any(row.split("|")[1].strip() == "`6`" for row in rows), "no row for exit code 6"
    assert all(len(row.strip().strip("|").split("|")) == 3 for row in rows), \
        "every exit-code row needs code, meaning and where"


def test_the_page_covers_sanitiser_no_export_d17_and_credits():
    text = _doc()
    lower = text.lower()
    assert "sanitis" in lower, "the inbound sanitiser is not described"
    assert "no-export" in lower, "the no-export invariant is not described"
    assert "D17" in text, "the D17 contract is not referenced"
    assert "VENDORED.md" in text and "CREDITS.md" in text


def test_the_cli_reference_has_rows_3_and_6_for_search():
    cli = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    rows = [line for line in cli.splitlines() if line.startswith("| `")]
    for code in ("3", "6"):
        assert any(row.split("|")[1].strip() == f"`{code}`" and "research search" in row
                   for row in rows), f"docs/cli.md has no row {code} for scieflow research search"


def test_the_discovery_providers_page_names_the_network_flag():
    text = (ROOT / "docs" / "research" / "discovery-providers.md").read_text(encoding="utf-8")
    assert "--network" in text
    assert "literature-kernel.md" in text


def test_the_data_protection_page_states_the_l2_network_default():
    text = (ROOT / "docs" / "data-protection.md").read_text(encoding="utf-8")
    assert "network" in text.lower() and "`replay`" in text and "L2" in text


def test_research_rule_6_is_the_approved_text_byte_for_byte():
    text = RESEARCH_AGENTS.read_text(encoding="utf-8")
    assert APPROVED_RULE_6 in text, "src/scieflow/research/AGENTS.md rule 6 is not the approved text"


def test_the_lit_review_phase_2_uses_search_all_and_keeps_selection():
    text = SKILL.read_text(encoding="utf-8")
    assert "scieflow research search all" in text
    assert "Merge and deduplicate" not in text, "the manual merge/dedupe step is still in phase 2"
    assert "Select the papers that best answer" in text
