"""Docs, nav, skill and research rule 6 match the literature-kernel code (M-C1 t6, Part A 5.12).

Every mode, command and exit code named here is the one the code defines; a renamed one fails.
"""

from pathlib import Path

import click

from scieflow.core import network
from scieflow.research.cli import research
from scieflow.research.search import fanout, registry

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
    assert "shape, file name, provider and body hash of every file" in text
    assert "shape, key, request and body hash" not in text


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


# --- M-C1 t6 fix: review r1 findings 1-4 and non-blocking notes N1-N3 -------------------------

EXIT_RULE = ("`search all` exits `0` when any source answered; it exits `6` only when no source "
             "answered and every source asked is a snapshot miss, `3` when no source answered and "
             "some source was refused or switched off, and `1` otherwise.")
MODE_SCOPE = ("The mode applies to a request made for a run: with `--workspace SLUG`, or from inside "
              "a dispatch, where the run is the dispatched one. Without either, the search is unscoped")
CORRUPT = ("A corrupt or forged snapshot read by a `replay` or `offline` run is a refusal "
           "(exit `3`): nothing is sent")


def test_the_documented_exit_codes_are_the_code_constants():
    assert registry.EXIT_REFUSED == 3 and registry.EXIT_MISS == 6 and registry.EXIT_FAILED == 1
    assert fanout.EXIT_OK == 0 and fanout.EXIT_FAILED == 1
    assert fanout.EXIT_REFUSED == 3 and fanout.EXIT_MISS == 6


def test_search_all_exit_rule_matches_the_code():
    ok = {"source": "openalex", "status": "ok", "count": 1}
    refused = {"source": "crossref", "status": "refused", "count": 0}
    miss = {"source": "arxiv", "status": "offline-miss", "count": 0}
    failed = {"source": "europepmc", "status": "failed", "count": 0}
    assert fanout.exit_code([ok, refused]) == 0
    assert fanout.exit_code([miss, miss]) == 6
    assert fanout.exit_code([miss, refused]) == 3
    assert fanout.exit_code([miss, failed]) == 1


def test_the_page_states_the_search_all_exit_rule():
    assert EXIT_RULE in _doc(), "literature-kernel.md does not state the search all exit rule"


def test_the_cli_reference_states_the_search_all_exit_rule_on_rows_3_and_6():
    rows = [line for line in (ROOT / "docs" / "cli.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("| `")]
    for code in ("3", "6"):
        row = next(r for r in rows if r.split("|")[1].strip() == f"`{code}`" and "research search" in r)
        assert "no source answered" in row, f"docs/cli.md row {code} omits the search all rule"


def test_the_page_lists_the_literal_status_values():
    text = _doc()
    for value in ("ok", "refused", "off", "offline-miss", "failed"):
        assert f"`{value}`" in text, f"literature-kernel.md does not list the status `{value}`"


def test_the_search_all_options_name_workspace_and_network():
    text = _doc()
    assert "Options: " in text, "no Options paragraph for search all"
    start = text.index("Options: ")
    options = text[start:text.index("One source at a time", start)]
    assert "--workspace SLUG" in options and "--network MODE" in options


def test_the_page_says_how_a_search_is_tied_to_a_run():
    text = _doc()
    assert MODE_SCOPE in text, "the page does not say how a search is tied to a run"
    assert "`--network replay`" in text or "`--network offline`" in text


def test_the_discovery_providers_page_says_how_a_search_is_tied_to_a_run():
    text = (ROOT / "docs" / "research" / "discovery-providers.md").read_text(encoding="utf-8")
    assert "`--workspace SLUG`" in text and "unscoped" in text


def test_the_page_says_a_corrupt_snapshot_is_a_refusal_in_no_egress_runs():
    assert CORRUPT in _doc(), "the corrupt-snapshot refusal is not documented"


def test_the_live_row_says_no_snapshot_not_no_cache():
    row = next(line for line in _doc().splitlines() if line.startswith("| `live`"))
    assert "no snapshot" in row


def test_the_page_says_network_yml_is_read_only_to_dispatches():
    assert "`network.yml`, when it exists, is read-only to the agent" in _doc()
    assert "read-only too, once it exists" in _doc()


def test_the_page_limits_say_a_dispatch_can_create_a_missing_snapshots_folder():
    text = _doc()
    assert "A dispatch can create a `snapshots/` folder that does not exist yet" in text
    assert "creates the folder first" in text


def test_the_data_protection_page_names_the_l2_providers_refusal():
    text = (ROOT / "docs" / "data-protection.md").read_text(encoding="utf-8")
    assert "`l2_providers`" in text and "exit 3" in text
    assert "read-only to dispatched agents once it exists" in text
