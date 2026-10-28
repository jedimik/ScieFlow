"""M-D1 t5 (plan 5.8, 6 t5): the study-design docs, the skills and agent rule 19 match the code.

Rule ids named here are the literals the code emits; a renamed id fails this test. The rule 19 text is
the operator-approved protected edit, pinned byte for byte.
"""

from pathlib import Path

from scieflow.experiments.power import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "experiments" / "design.md"
CLI_DOC = ROOT / "docs" / "cli.md"
DESIGN_SRC = ROOT / "src" / "scieflow" / "core" / "design"

SUBCOMMANDS = ("init", "check", "render", "show", "lock", "amend", "guidelines")
CHECK_ERRORS = ("keys", "json-value", "enum", "todo", "id", "ref", "primary", "test", "rct",
                "sample-size", "power-mismatch", "metric", "alpha", "exploratory", "guideline",
                "levels")
CHECK_WARNINGS = ("no-sample-size", "power-unverified", "guideline-fit", "no-guideline",
                  "guideline-unverified", "level-undeclared", "missing-data", "multiplicity",
                  "outcome-role")
CAMPAIGN_RULES = ("design-ledger-invalid", "design-modified", "design-invalid", "design-unlocked",
                  "out-of-sap")
RULE19 = """\
19. **Study design** (`docs/experiments/design.md`). A run that holds
    `design/design.yml` is bound by it once a human answers its
    `design-lock` gate (`uv run scieflow design lock <slug>`): campaigns
    whose metrics are not outcomes of the locked analysis plan are refused,
    and so is every campaign while the file differs from its lock. Change a
    locked design only through `uv run scieflow design amend <slug>
    --summary … --rationale …` (a new human-only gate); never edit
    `design/lock.jsonl` or `design/locked/`, and never answer a
    `design-lock` gate yourself. Sample sizes come from `uv run scieflow
    experiment power`, never from your own arithmetic.
"""


def _source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(DESIGN_SRC.glob("*.py")))


def _experiments_cli() -> str:
    return (ROOT / "src" / "scieflow" / "experiments" / "cli.py").read_text(encoding="utf-8")


def test_the_doc_exists_and_is_in_the_nav():
    assert DOC.is_file()
    assert "Study Design and SAP: experiments/design.md" in (ROOT / "mkdocs.yml").read_text()


def test_the_doc_names_every_design_subcommand():
    text = DOC.read_text(encoding="utf-8")
    for command in SUBCOMMANDS:
        assert f"scieflow design {command}" in text, command


def test_the_cli_reference_has_a_design_section_naming_every_subcommand():
    text = CLI_DOC.read_text(encoding="utf-8")
    section = text.split("\n## `scieflow design`\n", 1)[1].split("\n## ", 1)[0]
    for command in SUBCOMMANDS:
        assert f"scieflow design {command}" in section, command
    assert "experiments/design.md" in section


def test_the_doc_names_every_power_family_and_the_unverified_status():
    text = DOC.read_text(encoding="utf-8")
    for family in FAMILIES:
        assert f"`{family}`" in text, family
    assert "unverified" in text


def test_the_doc_credits_researcher_and_statsmodels():
    text = DOC.read_text(encoding="utf-8")
    assert "researcher" in text and "MIT" in text
    assert "statsmodels" in text and "BSD-3-Clause" in text


def test_the_doc_states_residuals_r1_to_r6():
    text = DOC.read_text(encoding="utf-8")
    for n in range(1, 7):
        assert f"**R{n} " in text, n


def test_every_check_rule_id_in_the_doc_is_emitted_by_the_code():
    text = DOC.read_text(encoding="utf-8")
    source = _source()
    for rule in CHECK_ERRORS + CHECK_WARNINGS:
        assert f"`{rule}`" in text, f"doc does not name {rule}"
        assert f'"{rule}"' in source, f"code does not emit {rule}"


def test_every_campaign_rule_id_is_named_in_the_doc_and_emitted_by_the_code():
    text = DOC.read_text(encoding="utf-8")
    source = _source()
    for rule in CAMPAIGN_RULES:
        assert rule in text, f"doc does not name {rule}"
        assert f'"{rule}"' in source, f"code does not emit {rule}"


def test_the_campaign_guard_is_in_the_run_and_sweep_commands():
    cli = _experiments_cli()
    assert cli.count("_design_guard(budget_run, campaign)") == 2
    assert 'reason="design"' in cli and "note.design_warning" in cli


def test_rule_19_is_the_approved_text_exactly_once_in_agents_md():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(RULE19) == 1


def test_the_skills_name_the_lock_and_the_sap_metric_rule():
    research = (ROOT / "skills" / "research-loop" / "SKILL.md").read_text(encoding="utf-8")
    cycle = (ROOT / "skills" / "experiment-cycle" / "SKILL.md").read_text(encoding="utf-8")
    designer = (ROOT / "src" / "scieflow" / "experiments" / "skills" / "experiment-designer"
                / "SKILL.md").read_text(encoding="utf-8")
    assert "scieflow design lock" in research and "## Study design" in research
    assert "## Locked design" in cycle and "scieflow design amend" in cycle
    assert "sap_metrics" in designer
