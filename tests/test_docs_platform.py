"""M-I t6 docs contract (plan 5.7 and the t6 RED -> GREEN list).

The docs for the read-only MCP server, the four read-only terminal views, the record checks,
the CHANGELOG, the citation file and the protected root AGENTS.md bullet. Names and flags are
checked against the code (the tool registry, the click help texts, the protocol constant), so
the docs cannot drift silently. Read from this repository only; no network.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]+")

# The protected AGENTS.md bullet (plan section 3), byte for byte.
AGENTS_OLD = (
    "- Runs: `scieflow workspace list | doctor <slug>`. Commands from before the\n"
    "  module merge (`scripts/agent_run.py`, `expx`, `whatsnew`, …) still work and\n"
    "  print their new name — use the new one (`docs/MIGRATION.md`).\n"
)
AGENTS_NEW = (
    "- An interactive coordinator (Hermes, Claude Desktop, Codex) may read runs\n"
    "  through the read-only MCP server `scieflow mcp serve` (`docs/mcp.md`). It\n"
    "  shows only what a sandboxed cloud agent may see (L2 runs are hidden),\n"
    "  writes nothing, and refuses to start inside a dispatch; every action still\n"
    "  goes through the CLI. Sub-agents never use it.\n"
)

# Commands whose documented flags must exist in their --help (plan t6 item 2).
VIEW_COMMANDS = [
    ("mcp", "serve"),
    ("mcp", "tools"),
    ("run", "overview"),
    ("run", "budgets"),
    ("run", "workflows"),
    ("agent", "conversational"),
    ("workspace", "doctor"),
]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text from `heading` up to the next level-2 heading."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    match = re.search(r"^## ", rest, flags=re.M)
    return text[start: start + len(heading) + (match.start() if match else len(rest))]


def _help(*path: str) -> str:
    from scieflow.cli import main

    result = CliRunner().invoke(main, [*path, "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def _tool_names() -> list[str]:
    from scieflow.mcp_server import tools as registry

    return [tool.name for tool in registry.TOOLS]


# --- docs/mcp.md -----------------------------------------------------------------------------

def test_mcp_page_exists_and_is_in_the_nav():
    assert (ROOT / "docs" / "mcp.md").is_file()
    nav = _read("mkdocs.yml")
    assert "docs/mcp.md" not in nav  # nav paths are relative to docs/
    assert re.search(r"^\s*-\s*MCP server:\s*mcp\.md\s*$", nav, flags=re.M), "nav line missing"


def test_mcp_page_names_every_tool_of_the_server():
    text = _read("docs/mcp.md")
    names = _tool_names()
    assert len(names) == 12, names
    for name in names:
        assert f"`{name}`" in text, f"tool {name} not documented"


@pytest.mark.parametrize("phrase", [
    "--workspace",
    "read-only",
    "L2",
    "cloud agent",
    "refuses to start inside a dispatch",
    "sanitis",
    "scieflow mcp serve",
    "scieflow mcp tools",
    "mcpServers",
    "\"command\": \"uv\"",
    "No `.mcp.json` is shipped",
])
def test_mcp_page_states_the_required_facts(phrase):
    assert phrase in _read("docs/mcp.md")


def test_mcp_page_states_the_result_cap_from_the_code():
    from scieflow.mcp_server.protocol import MAX_RESULT_BYTES

    assert f"{MAX_RESULT_BYTES:,}" in _read("docs/mcp.md")


def test_no_mcp_json_is_shipped_at_the_repository_root():
    assert not (ROOT / ".mcp.json").exists()


# --- docs/cli.md -----------------------------------------------------------------------------

def test_cli_reference_has_the_mcp_section():
    assert "## `scieflow mcp`" in _read("docs/cli.md")


def test_cli_reference_names_the_four_views_and_the_doctor_findings():
    text = _read("docs/cli.md")
    for needle in ("run overview", "run budgets", "run workflows",
                   "agent conversational", "invalid_events", "invalid_jobs"):
        assert needle in text, f"{needle} missing from docs/cli.md"


def _flags_in(text: str, heading: str) -> set[str]:
    return set(FLAG.findall(_section(text, heading)))


def test_mcp_flags_documented_exist_in_the_mcp_help():
    documented = _flags_in(_read("docs/cli.md"), "## `scieflow mcp`")
    assert "--workspace" in documented and "--json" in documented
    real = _help("mcp", "serve") + _help("mcp", "tools")
    unknown = {flag for flag in documented if flag not in real}
    assert not unknown, f"documented mcp flags not in --help: {sorted(unknown)}"


def test_view_flags_documented_exist_in_their_help():
    documented = _flags_in(_read("docs/cli.md"), "### Read-only views")
    assert "--json" in documented
    real = "".join(_help(*path) for path in VIEW_COMMANDS if path[0] != "mcp")
    unknown = {flag for flag in documented if flag not in real}
    assert not unknown, f"documented view flags not in --help: {sorted(unknown)}"


# --- docs/architecture.md --------------------------------------------------------------------

def test_architecture_links_the_record_schemas():
    text = _read("docs/architecture.md")
    assert "Records are checked when written" in text
    assert "schemas/events.yml" in text
    assert "schemas/jobs.yml" in text


# --- CHANGELOG.md ----------------------------------------------------------------------------

def test_changelog_has_unreleased_and_the_0_2_0_line():
    text = _read("CHANGELOG.md")
    assert "## [Unreleased]" in text
    assert "## [0.2.0]" in text
    assert "#24" in text


def test_changelog_contains_no_e_mail_address():
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", _read("CHANGELOG.md"))


# --- CITATION.cff ----------------------------------------------------------------------------

def _citation() -> dict:
    data = yaml.safe_load(_read("CITATION.cff"))
    assert isinstance(data, dict)
    return data


def test_citation_is_cff_1_2_0_software():
    data = _citation()
    assert data["cff-version"] == "1.2.0"
    assert data["type"] == "software"
    assert data["title"] == "ScieFlow"
    assert data["repository-code"] == "https://github.com/jedimik/ScieFlow"


def test_citation_version_matches_pyproject():
    pyproject = tomllib.loads(_read("pyproject.toml"))
    assert _citation()["version"] == pyproject["project"]["version"]


def test_citation_authors_and_no_personal_fields_beyond_the_name():
    data = _citation()
    assert data["authors"], "authors must not be empty"
    assert data["authors"][0] == {"family-names": "Krajča", "given-names": "Tomáš"}
    for key in ("email", "orcid", "license"):
        assert key not in data


# --- root AGENTS.md (protected edit, plan section 3) -----------------------------------------

def test_root_agents_carries_the_approved_bullet_once():
    text = _read("AGENTS.md")
    assert text.count(AGENTS_NEW) == 1
    assert text.count(AGENTS_OLD) == 1
    assert text.index(AGENTS_OLD) < text.index(AGENTS_NEW)
