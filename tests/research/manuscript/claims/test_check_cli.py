"""M-G t3: `scieflow manuscript claims check` and `claims waive` (M-G 5.6, 5.7): exit codes, the report files,
determinism, --no-write, --json, one note.claims_checked event, ledger-stale. Synthetic only."""

from __future__ import annotations

import json

from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t3_support import MARKERS, body, results_run, write
from tests.research.manuscript.conftest import RUN
from tests.research.manuscript.t8_support import snapshot

PLANTED = body("SSIM was 0.93 in the Results. % source: [data:tbl-metrics]")


def invoke(*args):
    return CliRunner().invoke(main, ["manuscript", "claims", *args], catch_exceptions=False)


def report_dir(synth):
    return synth.run_dir / "report" / "claims"


def test_check_without_a_ledger_refuses_and_names_the_build(synth):
    results_run(synth, PLANTED)
    result = invoke("check", RUN)
    assert result.exit_code == 1 and "claims build" in result.output


def test_a_planted_error_exits_one_and_writes_the_report_files(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    result = invoke("check", RUN)
    assert result.exit_code == 1, result.output
    assert "C002" in result.output and "number-missing" in result.output
    doc = json.loads((report_dir(synth) / "check.json").read_text(encoding="utf-8"))
    assert doc["counts"]["errors"] >= 1 and doc["counts"]["by_code"]["C002"] == 1
    assert (report_dir(synth) / "check.md").is_file()
    for marker in MARKERS:
        assert marker not in result.output
        assert marker not in (report_dir(synth) / "check.md").read_text(encoding="utf-8")


def test_a_clean_manuscript_exits_zero(synth):
    results_run(synth, body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]"))
    invoke("build", RUN)
    result = invoke("check", RUN)
    assert result.exit_code == 0, result.output


def test_the_report_is_byte_identical_on_a_second_run(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    invoke("check", RUN)
    first = (report_dir(synth) / "check.json").read_bytes(), (report_dir(synth) / "check.md").read_bytes()
    invoke("check", RUN)
    assert first == ((report_dir(synth) / "check.json").read_bytes(),
                     (report_dir(synth) / "check.md").read_bytes())


def test_no_write_writes_nothing_and_records_no_event(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    before = snapshot(synth.run_dir)
    result = invoke("check", RUN, "--no-write")
    assert result.exit_code == 1
    assert snapshot(synth.run_dir) == before
    assert events.read(synth.run_dir, types=("note.claims_checked",)) == []


def test_a_written_check_records_one_counts_event(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    invoke("check", RUN)
    checked = events.read(synth.run_dir, types=("note.claims_checked",))
    assert len(checked) == 1
    assert "data" in checked[0] and checked[0]["data"].get("errors", 0) >= 1
    assert all(not isinstance(v, str) or not any(m in v for m in MARKERS) for v in checked[0]["data"].values())


def test_json_prints_the_document(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    result = invoke("check", RUN, "--json")
    document = json.loads(result.output)
    assert set(document) >= {"version", "manuscript_tree_sha256", "ledger_sha256", "counts", "findings", "waived"}


def test_a_stale_ledger_exits_one_with_the_rebuild_hint(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    write(synth.ms / "main.tex", (synth.ms / "main.tex").read_text(encoding="utf-8") + "% later\n")
    result = invoke("check", RUN)
    assert result.exit_code == 1 and "claims build" in result.output


def test_waive_then_check_exits_zero(synth):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    claim = ledger.read(synth.run_dir)[0]["id"]
    waived = CliRunner().invoke(main, ["manuscript", "claims", "waive", RUN, claim, "--code", "C002",
                                       "--reason", "The reviewer accepted the rounding of this value."],
                                catch_exceptions=False)
    assert waived.exit_code == 0, waived.output
    assert invoke("check", RUN).exit_code == 0


def test_waive_refused_inside_a_dispatch_exits_one(synth, monkeypatch):
    results_run(synth, PLANTED)
    invoke("build", RUN)
    claim = ledger.read(synth.run_dir)[0]["id"]
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    result = CliRunner().invoke(main, ["manuscript", "claims", "waive", RUN, claim, "--code", "C002",
                                       "--reason", "A synthetic reason that is long enough."],
                                catch_exceptions=False)
    assert result.exit_code == 1 and "waive refused" in result.output


def test_check_json_and_markdown_are_exact_for_the_planted_fixture(synth):
    """M-G t3 item 6: check.json and check.md are compared byte for byte with the expected report of the planted
    fixture (version, both hashes from the ledger, counts, the one finding, no waived entries, the table)."""
    results_run(synth, PLANTED)
    invoke("build", RUN)
    assert invoke("check", RUN).exit_code == 1
    claim = ledger.read(synth.run_dir)[0]["id"]
    ledger_doc = json.loads((report_dir(synth) / "ledger.json").read_text(encoding="utf-8"))
    message = "the number 0.93 is not in the evidence it cites (inputs/metrics.csv)"
    expected = {
        "version": 1,
        "manuscript_tree_sha256": ledger_doc["manuscript_tree_sha256"],
        "ledger_sha256": ledger_doc["claims_sha256"],
        "counts": {"by_code": {"C002": 1}, "errors": 1, "waived": 0, "warnings": 0},
        "findings": [{"claim": claim, "code": "C002", "file": "main.tex", "line": 4, "message": message,
                      "rule": "number-missing", "severity": "error"}],
        "waived": [],
    }
    expected_json = json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    expected_md = (
        "# Claim check\n\n"
        f"manuscript tree sha256: {ledger_doc['manuscript_tree_sha256']}\n"
        f"ledger sha256: {ledger_doc['claims_sha256']}\n"
        "errors: 1, warnings: 0, waived: 0\n\n"
        "| Severity | Code | Rule | Claim | Location | Message |\n"
        "|---|---|---|---|---|---|\n"
        f"| error | C002 | number-missing | {claim} | main.tex:4 | {message} |\n"
    )
    assert (report_dir(synth) / "check.json").read_bytes() == expected_json.encode("utf-8")
    assert (report_dir(synth) / "check.md").read_bytes() == expected_md.encode("utf-8")
