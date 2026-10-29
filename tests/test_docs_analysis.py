"""M-D2 t6 (plan 5.11, Part B t6 items 2-4): the analysis docs, the CLI reference, the concepts
page, the skills and experiments AGENTS.md rule 6 match the code.

The rule 6 text is the operator-applied, human-approved protected edit (plan section 3), pinned
byte for byte. Names checked here are the literals the code emits or accepts.
"""

from pathlib import Path

from scieflow.experiments import analysis, stats

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "src" / "scieflow" / "experiments"
DOC = ROOT / "docs" / "experiments" / "analysis.md"
CLI_DOC = ROOT / "docs" / "experiments" / "cli.md"
CONCEPTS = ROOT / "docs" / "experiments" / "concepts.md"
EXP_CYCLE = ROOT / "skills" / "experiment-cycle" / "SKILL.md"
EVALUATOR = EXP / "skills" / "evaluator" / "SKILL.md"
DESIGNER = EXP / "skills" / "experiment-designer" / "SKILL.md"
RUNNER = EXP / "skills" / "experiment-runner" / "SKILL.md"
EXP_AGENTS = EXP / "AGENTS.md"

SUBCOMMANDS = ("stats", "verify", "tables", "figures", "compare-campaigns")
SECTION_FLAGS = {
    "stats": ("--include-direct", "--json"),
    "verify": ("--json",),
    "tables": (),
    "figures": ("--metric", "--width", "--style-file"),
    "compare-campaigns": ("--metric", "--json", "--out", "--force"),
}
RULE6 = """\
6. **Statistics come from code.** Means, confidence intervals, tests,
   effect sizes and corrected p-values come from `scieflow experiment
   stats` (`stats.json`); quote them, never compute or re-round them
   yourself. Every replicate is one run of the `experiment_runs` budget.
"""
RULE3 = """\
3. **Never bypass `scieflow experiment` for run bookkeeping.** Do not
   hand-write run directories, metrics.json, results.json, manifest.json,
   results.csv, stats.json, or the generated tables and figures.
"""


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of a `## heading` section up to the next `## ` heading."""
    head = f"\n## {heading}\n"
    assert head in text, heading
    return text.split(head, 1)[1].split("\n## ", 1)[0]


def _supported_tests() -> set[str]:
    return set(analysis.KIND) | {"anova-oneway", "kruskal-wallis"}


def test_the_analysis_doc_exists_and_is_in_the_nav_after_the_cli_reference():
    assert DOC.is_file()
    nav = _text(ROOT / "mkdocs.yml")
    assert "Analysis and Statistics: experiments/analysis.md" in nav
    assert nav.index("CLI Reference: experiments/cli.md") < nav.index(
        "Analysis and Statistics: experiments/analysis.md")


def test_the_analysis_doc_names_every_new_subcommand_and_campaign_key():
    text = _text(DOC)
    for command in SUBCOMMANDS:
        assert f"scieflow experiment {command}" in text, command
    for key in ("replicates", "analysis", "accepts_seed", "seed", "baseline"):
        assert key in text, key


def test_the_analysis_doc_names_every_supported_test_and_correction():
    text = _text(DOC)
    for test in sorted(_supported_tests()):
        assert test in text, test
    for correction in stats.CORRECTIONS:
        assert correction in text, correction


def test_the_analysis_doc_states_the_budget_rule_and_the_pre_check():
    text = _text(DOC)
    assert "one replicate = one experiment run" in text
    assert "pre-check" in text


def test_the_analysis_doc_states_the_data_level_note_and_the_usable_value_counts():
    text = _text(DOC)
    assert "data_level" in text
    for name in ("n_missing", "n_nonfinite", "excluded"):
        assert name in text, name


def test_the_analysis_doc_states_the_manifest_verify_and_results_csv():
    text = _text(DOC)
    for name in ("manifest.json", "verify", "results.csv", "metrics"):
        assert name in text, name


def test_the_cli_reference_has_a_section_for_each_new_subcommand():
    text = _text(CLI_DOC)
    for command in SUBCOMMANDS:
        section = _section(text, f"scieflow experiment {command}")
        for flag in SECTION_FLAGS[command]:
            assert flag in section, (command, flag)


def test_the_cli_reference_documents_seed_and_replicate_ids_under_run_and_sweep():
    text = _text(CLI_DOC)
    assert "--seed" in _section(text, "scieflow experiment run")
    assert "replicate" in _section(text, "scieflow experiment sweep")


def test_the_concepts_page_shows_the_new_run_and_campaign_files():
    text = _text(CONCEPTS)
    for name in ("manifest.json", "results.csv", "stats.json", "tables/", "figures/",
                 "packages/", "accepts_seed", "replicates", "analysis"):
        assert name in text, name


def test_experiment_cycle_no_longer_asks_to_record_the_runs_again():
    text = _text(EXP_CYCLE)
    assert "experiment_runs: +K" not in text
    assert any("records its own runs" in line for line in text.splitlines())


def test_the_evaluator_names_stats_and_says_to_rerun_it_after_metrics():
    text = _text(EVALUATOR)
    assert "scieflow experiment stats" in text
    assert any("scieflow experiment metrics" in line and "stats" in line
               for line in text.splitlines())


def test_the_designer_schema_block_shows_replicates_and_analysis():
    text = _text(DESIGNER)
    assert "replicates:" in text
    assert "analysis:" in text


def test_the_runner_names_verify_after_a_sweep():
    assert "scieflow experiment verify" in _text(RUNNER)


def test_rule_6_is_the_approved_text_byte_for_byte_and_rule_3_is_updated():
    text = _text(EXP_AGENTS)
    assert text.count(RULE6) == 1
    assert text.count(RULE3) == 1
    assert text.count("6. **Statistics come from code.**") == 1
