"""Which reporting guidelines a check uses (M-F 5.10): design, profile, --guideline. Synthetic only."""

from tests.research.manuscript.checklist_support import (
    article, check_cli, finding_keys, findings_of, guideline_ids, profile_with_guidelines, report_doc,
    write_design, write_main)


def test_the_design_guideline_is_checked_without_a_flag(synth):
    write_main(synth, article(figure_in_results=True, registry_in_methods=True))
    write_design(synth)
    result = check_cli()
    assert result.exit_code == 0, result.output
    doc = report_doc(synth)
    assert guideline_ids(doc) == ["consort-2010"]
    assert doc["guidelines"] == ["consort-2010"]


def test_profile_guideline_missing_from_the_design_is_a_warning_and_is_checked(synth):
    write_main(synth, article())
    write_design(synth, guidelines=("consort-2010",))
    profile_with_guidelines(synth, {"rct": ["consort-2010", "strobe"]})
    check_cli()
    doc = report_doc(synth)
    assert guideline_ids(doc) == ["consort-2010", "strobe"]
    assert ("guideline-not-in-design", "warning", "strobe") in finding_keys(doc)


def test_a_flagged_guideline_is_checked_without_a_design_warning(synth):
    write_main(synth, article())
    write_design(synth, guidelines=("consort-2010",))
    check_cli("--guideline", "stard-2015")
    doc = report_doc(synth)
    assert guideline_ids(doc) == ["consort-2010", "stard-2015"]
    assert findings_of(doc, "guideline-not-in-design") == []


def test_an_unknown_guideline_id_is_an_error(synth):
    write_main(synth, article())
    write_design(synth)
    result = check_cli("--guideline", "no-such-guideline")
    assert result.exit_code == 1
    doc = report_doc(synth)
    assert ("guideline-unknown", "error", "no-such-guideline") in finding_keys(doc)
    assert "no-such-guideline" in doc["guidelines"]


def test_a_todo_placeholder_in_the_design_is_not_a_guideline(synth):
    write_main(synth, article())
    write_design(synth, guidelines=("TODO: name the guideline",))
    result = check_cli()
    assert result.exit_code == 0, result.output
    doc = report_doc(synth)
    assert not any(gid.startswith("TODO") for gid in doc["guidelines"])
    assert findings_of(doc, "guideline-unknown") == []
    # the default synthetic profile names consort-2010 for rct; the design does not, so D12 warns
    assert doc["guidelines"] == ["consort-2010"]
    assert ("guideline-not-in-design", "warning", "consort-2010") in finding_keys(doc)


def test_an_unreadable_design_is_a_warning(synth):
    write_main(synth, article())
    folder = synth.run_dir / "design"
    folder.mkdir()
    (folder / "design.yml").write_text("- one\n- two\n", encoding="utf-8")
    result = check_cli()
    assert result.exit_code == 0, result.output
    assert ("design-unreadable", "warning", "design/design.yml") in finding_keys(report_doc(synth))


def test_without_a_design_no_profile_guideline_is_checked(synth):
    write_main(synth, article())
    profile_with_guidelines(synth, {"rct": ["consort-2010"]})
    check_cli()
    doc = report_doc(synth)
    assert doc["reporting"] == [] and doc["guidelines"] == []


def test_an_unverified_checklist_is_a_warning_per_guideline(synth):
    write_main(synth, article())
    write_design(synth)
    check_cli()
    doc = report_doc(synth)
    assert doc["reporting"][0]["verified"] is False
    assert ("guideline-unverified", "warning", "consort-2010") in finding_keys(doc)
