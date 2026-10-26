"""Docs, nav, exit code and root policy files match the data-protection code (M-B t6, Part A 5.13).

Every rule id and check id named here is the one the code emits; a renamed id fails this test.
"""

from pathlib import Path

from scieflow.core import agent_run
from scieflow.research.lib import providers

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "data-protection.md"

DISPATCH_RULES = ("policy-invalid", "endpoint-without-safe-entry", "l2-cloud", "l2-unsandboxed",
                  "l1-external-sharing", "scan-warning", "scan-incomplete")
SCAN_RULES = ("redcap-export", "identifier-column", "identifier-values", "free-text-column",
              "dicom-header", "imaging-file", "eeg-header", "binary-tabular", "unreadable")
RELEASE_CHECKS = ("small-cell", "counts-unknown", "record-level", "identifier-column",
                  "identifier-values", "free-text-column", "path-values", "dicom-header",
                  "eeg-header", "binary-tabular", "not-inspectable")
NON_CATALOGUE_EGRESS = ("scite", "notebooklm", "remote push", "remote fetch", "agent dispatch",
                        "DVC", "chats", "news")


def _egress_rows(text):
    """The rows of the Egress section only (the levels table above it is not an egress table)."""
    section = text.split("\n## Egress\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [row for row in rows if "---" not in row and "Destination" not in row]


def test_the_doc_exists_and_is_in_the_nav():
    assert DOC.is_file()
    nav = (ROOT / "mkdocs.yml").read_text()
    assert "Data protection: data-protection.md" in nav
    assert nav.index("data-protection.md") > nav.index("remote.md")


def test_the_egress_table_has_a_row_for_every_catalogue_key():
    rows = _egress_rows(DOC.read_text())
    for key in providers.CATALOGUE:
        matching = [row for row in rows if row.split("|")[1].strip().startswith(f"`{key}`")]
        assert matching, f"docs/data-protection.md has no egress row for `{key}`"


def test_the_egress_table_has_a_row_for_every_other_egress_point():
    rows = _egress_rows(DOC.read_text())
    for name in NON_CATALOGUE_EGRESS:
        assert any(name.lower() in row.split("|")[1].lower() for row in rows), \
            f"no egress row for {name}"


def test_every_egress_row_states_its_l2_behaviour():
    for row in _egress_rows(DOC.read_text()):
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        assert len(cells) == 5, f"egress row must have five columns: {row[:80]}"
        assert cells[-1], f"egress row has an empty L2 column: {row[:80]}"


def test_every_dispatch_scan_and_release_rule_id_is_named():
    text = DOC.read_text()
    for rule in (*DISPATCH_RULES, *SCAN_RULES, *RELEASE_CHECKS, "l2-remote-fetch"):
        assert f"`{rule}`" in text, f"docs/data-protection.md must name rule `{rule}`"


def test_the_cli_exit_table_has_78_for_a_data_refusal():
    rows = [line for line in (ROOT / "docs" / "cli.md").read_text().splitlines()
            if line.startswith("| `78` |")]
    assert rows, "docs/cli.md exit table has no row for 78"
    assert "data" in rows[0].lower()
    assert agent_run.DATA_EXIT == 78


def test_security_and_privacy_exist_and_link_the_doc():
    for name in ("SECURITY.md", "PRIVACY.md"):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing"
        assert "docs/data-protection.md" in path.read_text(), f"{name} must link the doc"


def test_security_says_how_to_report_privately():
    text = (ROOT / "SECURITY.md").read_text().lower()
    assert "private" in text and "security advisor" in text
    assert "never a public issue" in text


def test_privacy_says_no_telemetry_and_names_the_egress_table():
    text = (ROOT / "PRIVACY.md").read_text().lower()
    assert "telemetry" in text
    assert "egress" in text


def test_agents_md_carries_rule_18():
    assert "18. **Data protection** (`docs/data-protection.md`)." in (ROOT / "AGENTS.md").read_text()


def test_the_doc_states_the_honest_limits():
    text = DOC.read_text()
    for phrase in ("heuristic", "sandbox network", "imaging", "tabular"):
        assert phrase in text, f"docs/data-protection.md must mention {phrase!r}"
