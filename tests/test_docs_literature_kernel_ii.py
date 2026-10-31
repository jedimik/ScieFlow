"""Docs, skills and protected rule 6 match the literature-kernel II code (M-C2 t8, plan Part A 5.10).

Every command, exit code and default named here is one the code defines; a renamed one fails.
Test data is none: these tests read repository text and the Click command tree only.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import click
import pytest

from scieflow.research.cli import research

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "docs" / "research" / "literature-kernel.md"
DISCOVERY = ROOT / "docs" / "research" / "discovery-providers.md"
REVIEW = ROOT / "docs" / "research" / "review-contract.md"
NEWS = ROOT / "docs" / "news" / "index.md"
CLI = ROOT / "docs" / "cli.md"
PAPER_DRAFT = ROOT / "src" / "scieflow" / "research" / "skills" / "paper-draft" / "SKILL.md"
LIT_REVIEW = ROOT / "src" / "scieflow" / "research" / "skills" / "lit-review" / "SKILL.md"
RESEARCH_AGENTS = ROOT / "src" / "scieflow" / "research" / "AGENTS.md"
NEWS_AGENTS = ROOT / "src" / "scieflow" / "news" / "AGENTS.md"

# The M-C2 approved rule 6 (plan section 3, Edit 1; M-C2-D17), byte for byte, without rule 7.
APPROVED_RULE_6 = """\
6. Paper discovery uses the shared search commands (same sources for every
   agent): `scieflow research search all` (every enabled source at once,
   merged and ranked; the output names each source's status), or one source:
   `scieflow research search openalex`, `scieflow research search arxiv`,
   `scieflow research search europepmc`, `scieflow research search crossref`,
   `scieflow research search pubmed`, `scieflow research search semantic-scholar`,
   `scieflow research search datacite` (PubMed and Semantic Scholar are off
   until the user switches them on). `scieflow research snowball` follows the
   citations of chosen seed DOIs, one or two hops.
   Never invent papers, DOIs, or citation counts — every paper you report
   must come from a search-command result. Searches run through the provider
   Gateway: exit 3 means refused (the query failed the leak check, or the
   source is switched off). Rephrase the query; never retry around a refusal.
   Exit 6 means the run's network mode (`scieflow research network <slug>`)
   is `replay` or `offline` and no recorded snapshot answers the request:
   nothing was sent. Report it; never change the network mode to get past
   it (you may only switch a run to `replay` or `offline`, never back).
   `scieflow research verify-bib <slug>` checks that every reference in the
   bibliography is the work it claims to be. A `mismatch` or `unresolvable`
   verdict blocks merge until the reference is fixed or a human overrules
   its `review-blocker` gate; never answer that gate. `inconclusive` never
   blocks: it means the check could not tell.
"""

# The protected news sentence (plan section 3, Edit 2).
NEWS_AGENTS_INTRO = (
    "An interest with a `literature:` block runs no agent: it re-runs a saved\n"
    "literature monitor of a run (`scieflow research monitor`) and reports only\n"
    "the papers that monitor has not seen before.\n"
)

E_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _read(path):
    return path.read_text(encoding="utf-8")


def _exit_rows(text):
    """The rows of the Exit codes table of the kernel page (M-C1 helper, same shape)."""
    section = text.split("\n## Exit codes\n", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("|") and "---" not in line]


def _cli_rows():
    return [line for line in _read(CLI).splitlines() if line.startswith("| `")]


def _command(path):
    node = research
    for name in path:
        assert isinstance(node, click.Group), f"{' '.join(path)}: parent is not a group"
        assert name in node.commands, f"`scieflow research {' '.join(path)}` is not a command"
        node = node.commands[name]
    return node


# --- research rule 6 and the news sentence are the approved text ----------------------------


def test_research_rule_6_is_the_approved_text_m_c2():
    assert APPROVED_RULE_6 in _read(RESEARCH_AGENTS), \
        "src/scieflow/research/AGENTS.md rule 6 is not the M-C2 approved text"


def test_news_agents_names_the_literature_interest():
    assert NEWS_AGENTS_INTRO in _read(NEWS_AGENTS), "news AGENTS.md lacks the literature: sentence"


def test_research_agents_names_the_new_commands():
    text = _read(RESEARCH_AGENTS)
    assert "scieflow research verify-bib" in text
    assert "search semantic-scholar" in text


# --- the kernel page -----------------------------------------------------------------------


def test_kernel_page_names_the_identity_check_and_its_verdicts():
    text = _read(KERNEL)
    for token in ("scieflow research verify-bib", "`verified`", "`mismatch`",
                  "`unresolvable`", "`inconclusive`", "`review-blocker`", "--round",
                  "references.bib"):
        assert token in text, f"literature-kernel.md does not name {token}"


def test_kernel_page_names_the_new_sources_and_the_d16_default():
    text = _read(KERNEL)
    for token in ("scieflow research search pubmed", "scieflow research search semantic-scholar",
                  "scieflow research search datacite", "D16", "off by default",
                  "config/integrations.yml"):
        assert token in text, f"literature-kernel.md does not name {token}"


def test_kernel_page_names_snowball_monitor_oa_and_the_news_hook():
    text = _read(KERNEL)
    for token in ("scieflow research snowball", "scieflow research monitor",
                  "scieflow research oa", "`literature:`", "contact_email", "Unpaywall"):
        assert token in text, f"literature-kernel.md does not name {token}"


def test_kernel_page_states_the_gold_set_scope_and_the_no_pdf_rule():
    text = _read(KERNEL)
    for token in ("150", "0 of 100", "50 of 50", "neurology", "never downloads"):
        assert token in text, f"literature-kernel.md does not state {token!r}"


def test_kernel_page_exit_table_has_row_4_for_verify_bib():
    rows = _exit_rows(_read(KERNEL))
    row = next((r for r in rows if r.split("|")[1].strip() == "`4`"), None)
    assert row is not None, "literature-kernel.md exit table has no row for exit code 4"
    assert "verify-bib" in row


def test_kernel_page_keeps_the_m_c1_exit_rule_sentence():
    text = _read(KERNEL)
    assert ("`search all` exits `0` when any source answered; it exits `6` only when no source "
            "answered and every source asked is a snapshot miss, `3` when no source answered and "
            "some source was refused or switched off, and `1` otherwise.") in text


# --- cli reference --------------------------------------------------------------------------


def test_cli_reference_has_row_4_for_verify_bib():
    row = next((r for r in _cli_rows() if r.split("|")[1].strip() == "`4`"), None)
    assert row is not None, "docs/cli.md has no exit-code row 4"
    assert "verify-bib" in row and "refusal-grade" in row


def test_cli_reference_rows_3_and_6_name_the_new_commands():
    rows = _cli_rows()
    for code in ("3", "6"):
        row = next(r for r in rows if r.split("|")[1].strip() == f"`{code}`"
                   and "research search" in r)
        for command in ("research snowball", "research oa", "research verify-bib",
                        "research monitor"):
            assert command in row, f"docs/cli.md row {code} omits `scieflow {command}`"


# --- discovery providers ---------------------------------------------------------------------


def test_discovery_page_lists_the_five_new_providers():
    text = _read(DISCOVERY)
    for key in ("search-pubmed", "search-semantic-scholar", "search-datacite",
                "opencitations", "unpaywall"):
        assert f"`{key}`" in text, f"discovery-providers.md does not list `{key}`"
    assert "`contact_email`" in text
    assert "config/integrations.example.yml" in text


def test_discovery_page_says_the_address_is_never_written():
    text = _read(DISCOVERY)
    assert "never" in text.lower() and "address" in text.lower()


# --- review contract --------------------------------------------------------------------------


def test_review_contract_names_the_identity_blockers_next_to_the_bundle_blockers():
    text = _read(REVIEW)
    for token in ("identity", "review-blocker", "verify-bib", "inconclusive", "merge-check"):
        assert token in text, f"review-contract.md does not name {token}"


# --- news page ----------------------------------------------------------------------------------


def test_news_page_documents_the_literature_interest():
    text = _read(NEWS)
    assert "literature:" in text
    assert "scieflow research monitor" in text
    assert "## New literature" in text
    assert "No new papers since the last run." in text


# --- skills -----------------------------------------------------------------------------------


def _phase_5(text):
    return text.split("## Phase 5 — merge", 1)[1].split("\n## Phase 6", 1)[0]


def test_paper_draft_phase_5_runs_verify_bib_before_merge_check():
    phase = _phase_5(_read(PAPER_DRAFT))
    assert "scieflow research verify-bib <slug> --round <N>" in phase
    assert phase.index("scieflow research verify-bib <slug> --round <N>") < \
        phase.index("review merge-check"), "verify-bib must come before merge-check"
    assert "Never answer that gate yourself." in phase


def test_lit_review_names_snowball_and_monitor():
    text = _read(LIT_REVIEW)
    assert "scieflow research snowball" in text
    assert "scieflow research monitor" in text
    assert "Select the papers that best answer" in text
    assert "scieflow research search all" in text


# --- the commands the docs name exist --------------------------------------------------------


@pytest.mark.parametrize("path", [
    ("search", "pubmed"), ("search", "semantic-scholar"), ("search", "datacite"),
    ("snowball",), ("verify-bib",), ("oa",), ("monitor",),
])
def test_documented_m_c2_commands_exist(path):
    assert isinstance(_command(path), click.Command)


# --- hygiene of the edited pages ----------------------------------------------------------------


@pytest.mark.parametrize("path", [KERNEL, DISCOVERY, REVIEW, NEWS, CLI, PAPER_DRAFT, LIT_REVIEW])
def test_edited_pages_carry_no_e_mail_address(path):
    assert E_MAIL.search(_read(path)) is None, f"{path.name} contains an e-mail address"


def test_mkdocs_strict_build_passes_when_mkdocs_is_installed(tmp_path):
    try:
        import mkdocs  # noqa: F401
    except ImportError:
        pytest.skip("mkdocs is not available in this environment")
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-d", str(tmp_path / "site")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stderr[-2000:]
