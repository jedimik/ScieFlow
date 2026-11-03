"""M-G t3: the check C001-C004 (M-G 5.6): orphans, references, altered numbers, stale evidence, uncheckable
numbers, DOI-only claims, and the re-derived reference findings. Synthetic only."""

from __future__ import annotations

import pytest

from scieflow.research.manuscript.claims import compile as compile_mod
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import BIB_DOI, make_inputs, manuscript, write
from tests.research.manuscript.claims.t3_support import (
    MARKERS, body, built_check, of_code, of_rule, results_run, rules, text_of, write_bundle,
)
from tests.research.manuscript.t8_support import project_of


def test_a_planted_wrong_number_is_c002_at_its_line_and_names_no_value(synth):
    results_run(synth, body("SSIM was 0.93 in the Results. % source: [data:tbl-metrics]"))
    result = built_check(synth)
    found = of_rule(result, "number-missing")
    assert len(found) == 1, rules(result)
    finding = found[0]
    assert finding["code"] == "C002" and finding["severity"] == "error"
    assert finding["file"] == "main.tex" and finding["line"] == 4
    assert "0.93" in finding["message"]
    assert "inputs/metrics.csv" in finding["message"] or "tbl-metrics" in finding["message"]
    for marker in MARKERS:
        assert marker not in text_of(result), "a value read from evidence must never be printed"


def test_a_correct_number_has_no_finding(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]"))
    result = built_check(synth)
    assert of_code(result, "C002") == [], rules(result)
    assert result.errors == []


def test_an_orphan_number_is_an_error_in_results_and_a_warning_in_the_introduction(synth):
    manuscript_text = ("\\section{Introduction}\nThe cohort had 42 walkers.\n"
                       "\\section{Results}\nThe fit reached 0.5 in block two.\n")
    results_run(synth, manuscript_text)
    result = built_check(synth)
    orphans = of_rule(result, "orphan")
    severities = {f["line"]: f["severity"] for f in orphans}
    assert severities == {4: "warning", 6: "error"}, orphans
    assert all(f["code"] == "C001" for f in orphans)


def test_an_unresolved_data_reference_is_c001_error(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:no-such-table]"))
    result = built_check(synth)
    found = of_rule(result, "ref-unresolved")
    assert found and found[0]["code"] == "C001" and found[0]["severity"] == "error"


def test_evidence_changed_after_the_build_is_c003_and_repin_clears_it(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]"))
    ledger.build(synth.run_dir)
    write(synth.run_dir / "inputs" / "metrics.csv", "metric,value\nSSIM,0.91\nStride,7341.25\nNew,1\n")
    result = compile_mod.check_ledger(project_of(synth), synth.run_dir)
    changed = of_rule(result, "evidence-changed")
    assert changed and changed[0]["code"] == "C003" and changed[0]["severity"] == "error", rules(result)
    ledger.build(synth.run_dir, repin=True)
    again = compile_mod.check_ledger(project_of(synth), synth.run_dir)
    assert of_rule(again, "evidence-changed") == [], rules(again)


def test_a_corrected_cited_doi_is_a_c003_warning(synth):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    write_bundle(synth.run_dir, [{"id": "f1", "kind": "corrected", "doi": "10.5555/SYN.2010.1",
                                  "key": "lee2010", "blocker": False}])
    result = built_check(synth)
    found = of_rule(result, "source-corrected")
    assert found and found[0]["code"] == "C003" and found[0]["severity"] == "warning", rules(result)
    assert result.errors == []


def test_a_claim_with_no_checkable_evidence_is_number_uncheckable_not_c002(synth):
    make_inputs(synth.run_dir, [{"id": "fig-plot", "file": "plot.png", "content": b"\x89PNG synthetic\n"}])
    manuscript(synth, body("Accuracy was 0.9 in the Results. % source: [data:fig-plot]"))
    result = built_check(synth)
    found = of_rule(result, "number-uncheckable")
    assert found and found[0]["severity"] == "warning" and found[0]["code"] is None, rules(result)
    assert of_code(result, "C002") == []


def test_a_doi_only_claim_is_not_number_checked(synth):
    results_run(synth, body("Cohort of 42 walkers was described \\cite{lee2010}."))
    result = built_check(synth)
    assert of_code(result, "C002") == [], rules(result)
    assert of_rule(result, "orphan") == [], "a DOI reference is a reference: not an orphan"


def test_an_unmatched_number_with_data_and_a_cited_doi_is_a_warning(synth):
    results_run(synth, body("SSIM was 0.93 \\cite{lee2010}. % source: [data:tbl-metrics]"))
    result = built_check(synth)
    assert of_code(result, "C002") == [], rules(result)
    found = of_rule(result, "number-unmatched-cited")
    assert found and found[0]["severity"] == "warning"


def test_an_overlong_reference_is_re_derived_and_reported_by_the_check(synth):
    too_long = "x" * 201
    results_run(synth, body(f"SSIM was 0.91 in the Results. % source: [data:{too_long}]"))
    result = built_check(synth)
    found = of_rule(result, "ref-too-long")
    assert found and found[0]["code"] == "C001" and found[0]["severity"] == "error", rules(result)


def test_an_unsafe_cited_doi_is_re_derived_and_reported_by_the_check(synth):
    results_run(synth, body("A claim cites a path-like DOI \\cite{unsafe2020}."),
                bib=BIB_DOI + "@article{unsafe2020,\n  title = {Synthetic},\n  doi = {10.5555/../escape}\n}\n")
    result = built_check(synth)
    assert of_rule(result, "ref-unsafe-doi"), rules(result)
    assert of_rule(result, "ref-unsafe-doi")[0]["code"] == "C001"


def test_c004_is_never_emitted_and_codes_follow_the_table(synth):
    results_run(synth, body("SSIM was 0.93 in the Results. % source: [data:tbl-metrics]",
                            "A stray 0.5 in the Results."))
    result = built_check(synth)
    codes = {f["code"] for f in result.findings if f["code"]}
    assert "C004" not in codes
    assert codes <= {"C001", "C002", "C003", "C005", "C006"}


def test_the_check_re_derives_the_same_records_the_ledger_writes(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]",
                            "Stride was 7341.25 \\cite{lee2010} in the Results."))
    ledger.build(synth.run_dir)
    assembled = compile_mod.assemble(synth.run_dir)
    assert assembled.records == ledger.read(synth.run_dir)


def test_check_refuses_a_missing_ledger(synth):
    results_run(synth, body("SSIM was 0.91 in the Results."))
    with pytest.raises(ledger.ClaimsError) as info:
        compile_mod.check_ledger(project_of(synth), synth.run_dir)
    assert info.value.rule == "ledger-missing"


def test_check_refuses_a_ledger_built_from_an_older_manuscript(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]"))
    ledger.build(synth.run_dir)
    write(synth.ms / "main.tex", (synth.ms / "main.tex").read_text(encoding="utf-8") + "% a later edit\n")
    with pytest.raises(ledger.ClaimsError) as info:
        compile_mod.check_ledger(project_of(synth), synth.run_dir)
    assert info.value.rule == "ledger-stale"
