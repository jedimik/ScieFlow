"""M-D2 t5: report.md gains a Statistics section only from a fresh stats.json (plan 5.7).

tests/experiments/test_report.py itself stays unchanged; this module adds the statistics cases.
Synthetic data only.
"""

import importlib.util
from pathlib import Path

import pytest

from scieflow.experiments.report import generate_report

_SUPPORT = Path(__file__).with_name("tables_support.py")
_spec = importlib.util.spec_from_file_location("tables_support", _SUPPORT)
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

NOTE = "Statistics: not computed (run `scieflow experiment stats`)"


def _report(cdir):
    return generate_report(cdir, include_direct=True).read_text()


def test_fresh_stats_add_a_statistics_section_after_the_results(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct")
    ts.install(cdir)
    text = _report(cdir)
    assert "## Statistics" in text
    assert text.index("## Results") < text.index("## Statistics")
    section = text[text.index("## Statistics"):]
    assert "| configuration | ssim |" in section
    assert "| metric | configuration | test |" in section
    assert "- family `campaign`: correction holm, alpha 0.05, size 2" in section
    assert "- design: status `none`" in section
    assert NOTE not in text


def test_statistics_section_links_the_tables_and_figures_that_exist(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct")
    ts.install(cdir)
    (cdir / "tables").mkdir()
    (cdir / "tables" / "summary.md").write_text("x\n")
    text = _report(cdir)
    assert "`tables/summary.md`" in text
    assert "`tables/tests.md`" not in text
    assert "`figures/metrics.png`" not in text


def test_stale_stats_add_only_the_one_line(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct")
    ts.install(cdir)
    ts.edit_text(cdir / "results.json")
    text = _report(cdir)
    assert NOTE in text
    assert "## Statistics" not in text and "| configuration | ssim |" not in text


def test_missing_stats_add_only_the_one_line(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct")
    text = _report(cdir)
    assert NOTE in text
    assert "## Statistics" not in text


def test_hand_written_evaluation_is_still_preserved(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct")
    ts.install(cdir)
    _report(cdir)
    with (cdir / "report.md").open("a") as handle:
        handle.write("\n## Evaluation\n\nAgent notes here.\n")
    text = _report(cdir)
    assert "Agent notes here." in text
    assert text.count("# Campaign report:") == 1


def test_replicated_report_has_a_configuration_summary_before_the_run_rows(tmp_path):
    cdir = ts.campaign(tmp_path, mode="direct", replicated=True)
    ts.install(cdir)
    text = _report(cdir)
    assert "### Configurations" in text
    summary = text.index("### Configurations")
    first_run_row = text.index("| `000_sigma-0.5_r0`")
    assert summary < first_run_row
    assert "| 000_sigma-0.5 |" in text[summary:first_run_row]


def _drop_configs(doc):
    del doc["configs"]


def _nan_in_cell(doc):
    doc["configs"][0]["metrics"]["ssim"]["mean"] = float("nan")


def _p_as_text(doc):
    doc["tests"][0]["p"] = "x"


def _effect_as_number(doc):
    doc["tests"][0]["effect"] = 3


@pytest.mark.parametrize("edit", [_drop_configs, _nan_in_cell, _p_as_text, _effect_as_number],
                         ids=lambda f: f.__name__)
def test_malformed_stats_add_only_the_one_line_and_the_report_is_written(tmp_path, edit):
    """Finding 2: a hash-fresh but invalid stats.json falls back to the one-line note."""
    cdir = ts.campaign(tmp_path, mode="direct")
    doc = ts.fixed_doc()
    edit(doc)
    ts.write_raw(cdir, doc)
    text = _report(cdir)
    assert NOTE in text
    assert "## Statistics" not in text
    assert (cdir / "report.md").is_file()
