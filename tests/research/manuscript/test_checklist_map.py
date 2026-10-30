"""Reporting-checklist mapping (M-F 5.10): section kinds, item statuses, the filled checklist. Synthetic only."""

import pytest

from scieflow.research.manuscript import checklist
from tests.research.manuscript.checklist_support import (
    article, check_cli, checklist_file, finding_keys, guideline_record, item, report_doc,
    verify_checklist, write_design, write_main)


@pytest.mark.parametrize("title, kinds", [
    ("Introduction", {"introduction"}),
    ("Background", {"introduction"}),
    ("Methods", {"methods"}),
    ("Method", {"methods"}),
    ("Materials and Methods", {"methods"}),
    ("Patients and Methods", {"methods"}),
    ("Participants and methods", {"methods"}),
    ("Methodology", {"methods"}),
    ("Study design", {"methods"}),
    ("Experimental procedures", {"methods"}),
    ("Statistical analysis", {"methods"}),
    ("Results", {"results"}),
    ("Findings", {"results"}),
    ("Discussion", {"discussion"}),
    ("Conclusion", {"discussion"}),
    ("Conclusions", {"discussion"}),
    ("Limitations", {"discussion"}),
    ("Discussion and conclusions", {"discussion"}),
    ("Results and Discussion", {"results", "discussion"}),
    ("Acknowledgements", {"other"}),
])
def test_each_listed_title_maps_to_its_kinds(title, kinds):
    assert checklist.section_kinds(title) == frozenset(kinds)


def test_missing_flow_figure_and_registry_are_flagged_with_their_section(synth):
    write_main(synth, article())
    write_design(synth)
    result = check_cli("--guideline", "consort-2010")
    assert result.exit_code == 0, result.output
    doc = report_doc(synth)
    flow = item(doc, "13a")
    assert flow["status"] == "missing" and flow["where"] == "results"
    registry = item(doc, "23")
    assert registry["status"] == "missing" and registry["where"] == "other"
    keys = finding_keys(doc)
    assert ("guideline-item-missing", "warning", "13a") in keys
    assert ("guideline-item-missing", "warning", "23") in keys
    assert ("guideline-unverified", "warning", "consort-2010") in keys


def test_verified_checklist_makes_missing_items_errors(synth):
    write_main(synth, article())
    write_design(synth)
    verify_checklist(synth)
    result = check_cli("--guideline", "consort-2010")
    assert result.exit_code == 1, result.output
    keys = finding_keys(report_doc(synth))
    assert ("guideline-item-missing", "error", "13a") in keys
    assert ("guideline-item-missing", "error", "23") in keys
    assert not any(rule == "guideline-unverified" for rule, _, _ in keys)


def test_flow_figure_and_registry_id_are_present_with_file_and_line(synth):
    write_main(synth, article(figure_in_results=True, registry_in_methods=True))
    write_design(synth)
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    flow = item(doc, "13a")
    assert flow["status"] == "present"
    assert flow["where"].startswith("Results (main.tex:")
    registry = item(doc, "23")
    assert registry["status"] == "present"
    assert registry["where"].startswith("Methods (main.tex:")


def test_missing_methods_section_marks_every_methods_item_missing(synth):
    write_main(synth, article(methods=False))
    write_design(synth)
    check_cli("--guideline", "consort-2010")
    methods_items = [row for row in guideline_record(report_doc(synth))["items"]
                     if row["sections"] == ["methods"]]
    assert methods_items
    assert all(row["status"] == "missing" and row["where"] == "methods" for row in methods_items)


def test_filled_checklist_has_the_unverified_banner_and_one_row_per_item(synth):
    write_main(synth, article())
    write_design(synth)
    check_cli("--guideline", "consort-2010")
    lines = checklist_file(synth).read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Reporting checklist: CONSORT 2010 (consort-2010)"
    assert lines[2].startswith("This checklist is an unverified skeleton "
                               "(config/reporting/consort-2010.yml); check every item against Schulz KF")
    assert lines[2].endswith("before submission.")
    assert "| 13a | Participant flow per group | results | missing | results |" in lines
    rows = [line for line in lines if line.startswith("| ") and not line.startswith(("| Item", "| ---"))]
    assert len(rows) == 37
