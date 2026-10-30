"""Checklist judgements (M-F 5.10): the reviewer prompt and the merge of its answers. Synthetic only."""

import json
import re

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.research.manuscript import tex
from tests.research.manuscript.checklist_support import (
    QUOTE_INTRO, article, check_cli, finding_keys, findings_of, guideline_record, item, judgement_path,
    report_doc, write_design, write_judgement, write_main)
from tests.research.manuscript.conftest import RUN


def _prompt_cli(*extra):
    return CliRunner().invoke(main, ["manuscript", "checklist-prompt", RUN,
                                     "--guideline", "consort-2010", *extra], catch_exceptions=False)


def _ready(synth, **kwargs):
    write_main(synth, article(**kwargs))
    write_design(synth)


def test_prompt_lists_exactly_the_items_the_check_leaves_to_a_reader(synth):
    _ready(synth)
    check_cli("--guideline", "consort-2010")
    result = _prompt_cli("--write")
    assert result.exit_code == 0, result.output
    text = (synth.run_dir / "prompts" / "checklist-consort-2010.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^- `([^`]+)`", text, re.M))
    expected = {row["id"] for row in guideline_record(report_doc(synth))["items"]
                if row["status"] == "needs-judgement"}
    assert expected and listed == expected
    assert tex.source_tree_hash(synth.ms) in text
    assert "report/checklist/consort-2010.judgement.json" in text


def test_without_write_the_prompt_is_printed_and_nothing_is_written(synth):
    _ready(synth)
    result = _prompt_cli()
    assert result.exit_code == 0, result.output
    assert "consort-2010" in result.output
    assert not (synth.run_dir / "prompts").exists()


def test_an_unknown_guideline_is_refused(synth):
    _ready(synth)
    result = CliRunner().invoke(main, ["manuscript", "checklist-prompt", RUN,
                                       "--guideline", "no-such-guideline"])
    assert result.exit_code == 1


def test_a_verbatim_quote_makes_a_statement_judged_present(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "2a", "status": "present", "section": "Introduction",
                             "quote": QUOTE_INTRO}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "2a")["status"] == "judged-present"
    assert findings_of(doc, "judgement-invalid") == []


def test_a_fabricated_quote_is_ignored_with_a_warning(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "2a", "status": "present", "quote": "This sentence does not appear anywhere."}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "2a")["status"] == "needs-judgement"
    invalid = findings_of(doc, "judgement-invalid")
    assert len(invalid) == 1 and invalid[0]["severity"] == "warning" and "2a" in invalid[0]["message"]


def test_a_quote_under_twenty_characters_is_invalid(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "2a", "status": "present", "quote": "Synthetic."}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "2a")["status"] == "needs-judgement"
    assert "2a" in findings_of(doc, "judgement-invalid")[0]["message"]


def test_absent_with_a_reason_is_a_judged_absent_warning(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "24", "status": "absent",
                             "reason": "The manuscript does not say where the protocol can be read."}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "24")["status"] == "judged-absent"
    assert ("guideline-item-judged-absent", "warning", "24") in finding_keys(doc)


def test_not_applicable_with_a_reason_is_not_a_finding(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "24", "status": "not-applicable", "reason": "No protocol is planned."}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "24")["status"] == "not-applicable"
    assert findings_of(doc, "guideline-item-judged-absent") == []


def test_a_reason_over_three_hundred_characters_is_invalid(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "24", "status": "absent", "reason": "x" * 301}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "24")["status"] == "needs-judgement"
    assert "24" in findings_of(doc, "judgement-invalid")[0]["message"]


def test_a_stale_manuscript_hash_is_ignored_with_a_warning(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "2a", "status": "present", "quote": QUOTE_INTRO}], sha="0" * 64)
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "2a")["status"] == "needs-judgement"
    stale = findings_of(doc, "judgement-stale")
    assert len(stale) == 1 and stale[0]["severity"] == "warning"


def test_a_judgement_never_overrides_a_missing_item(synth):
    _ready(synth)
    write_judgement(synth, [{"id": "13a", "status": "present", "quote": QUOTE_INTRO}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "13a")["status"] == "missing"
    assert "13a" in findings_of(doc, "judgement-invalid")[0]["message"]


def test_a_judgement_never_overrides_a_present_item(synth):
    _ready(synth, figure_in_results=True)
    write_judgement(synth, [{"id": "13a", "status": "absent", "reason": "Not shown in this article."}])
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert item(doc, "13a")["status"] == "present"
    assert "13a" in findings_of(doc, "judgement-invalid")[0]["message"]


def test_an_unreadable_judgement_file_is_a_warning(synth):
    _ready(synth)
    path = judgement_path(synth)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    check_cli("--guideline", "consort-2010")
    assert findings_of(report_doc(synth), "judgement-invalid")


def test_a_symlinked_judgement_file_is_refused(synth, tmp_path):
    _ready(synth)
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"version": 1, "guideline": "consort-2010", "manuscript_sha256": "x",
                                  "items": []}), encoding="utf-8")
    path = judgement_path(synth)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    check_cli("--guideline", "consort-2010")
    doc = report_doc(synth)
    assert findings_of(doc, "judgement-invalid")
    assert item(doc, "2a")["status"] == "needs-judgement"
