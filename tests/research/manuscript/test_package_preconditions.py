"""`scieflow manuscript package` refusals (M-F 5.13). Each refusal leaves every file of the run unchanged.

Synthetic data only; the review blocker is the fixture under tests/fixtures/review/run/.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core.gates import open_gate
from scieflow.core.review import service
from scieflow.research.manuscript import package as package_mod
from tests.research.manuscript.claims.t2_support import make_inputs
from tests.research.manuscript.conftest import RUN, SLUG, make_profile
from tests.research.manuscript.t8_support import (
    OVERRULE, add_review, make_build, project_of, snapshot, submission,
)


def invoke(*args):
    return CliRunner().invoke(main, ["manuscript", "package", RUN, *args], catch_exceptions=False)


def assert_refused(synth, result, before: dict, *needles: str) -> None:
    assert result.exit_code == 1, result.output
    for needle in needles:
        assert needle in result.output, (needle, result.output)
    assert snapshot(synth.run_dir) == before, "a refused package must write nothing"
    assert not (synth.run_dir / "report").exists()


def test_an_open_review_blocker_refuses_and_names_the_merge_check(synth):
    add_review(synth)
    make_build(synth)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "blocker", "scieflow review merge-check")


def test_an_open_review_blocker_gate_alone_refuses(synth):
    make_build(synth)
    open_gate(project_of(synth), synth.run_dir, "review-blocker", "Overrule the retracted reference?")
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "blocker")


@pytest.mark.parametrize("error", [ValueError("bad review file"), OSError("unreadable"),
                                   RecursionError("too deep"), ImportError("no review layer")])
def test_a_blocker_read_that_fails_refuses_closed(synth, monkeypatch, error):
    make_build(synth)

    def broken(project, slug):
        raise error

    monkeypatch.setattr(service, "blockers", broken)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "fail closed")


def test_a_human_overrule_lets_the_package_proceed(synth):
    add_review(synth)
    make_build(synth)
    service.decide(project_of(synth), RUN, 1, OVERRULE, "human")
    result = invoke()
    assert result.exit_code == 0, result.output
    assert (submission(synth) / "package.json").is_file()


def test_the_blocker_refusal_comes_before_the_build_check(synth):
    add_review(synth)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "blocker")
    assert "build first" not in result.output


def test_a_legacy_profile_refuses(synth):
    make_build(synth)
    (synth.profiles_dir / f"{SLUG}.yml").unlink()
    (synth.profiles_dir / f"{SLUG}.md").write_text("# Hand-written page\n", encoding="utf-8")
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "profile-legacy")


def test_a_missing_profile_refuses(synth):
    make_build(synth)
    (synth.profiles_dir / f"{SLUG}.yml").unlink()
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "profile-missing")


def test_a_check_error_refuses_and_lists_the_rule(synth):
    synth.set_abstract("Synthetic " + " ".join(["word"] * 300))
    make_build(synth)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "abstract-words")


def test_no_build_refuses(synth):
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "run scieflow manuscript build first")


def test_a_failed_build_record_refuses(synth):
    make_build(synth, ok=False)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "run scieflow manuscript build first")


def test_a_stale_build_refuses(synth):
    make_build(synth)
    synth.edit("methods.tex", "Synthetic methods text", "Changed methods text")
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "run scieflow manuscript build first")


def test_a_pdf_that_differs_from_its_record_refuses(synth):
    build = make_build(synth)
    (build / "main.pdf").write_bytes(b"%PDF-1.4 replaced after the build\n")
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "run scieflow manuscript build first")


def test_a_symlink_in_the_manuscript_refuses(synth, tmp_path):
    make_build(synth)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the manuscript\n", encoding="utf-8")
    os.symlink(outside, synth.ms / "alias.txt")
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "symbolic link")


def test_a_symlinked_report_folder_refuses_and_writes_outside_nothing(synth, tmp_path):
    make_build(synth)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (synth.run_dir / "report").symlink_to(outside, target_is_directory=True)
    result = invoke()
    assert result.exit_code == 1, result.output
    assert "symbolic link" in result.output
    assert list(outside.iterdir()) == []


def test_refusals_name_their_rule_in_the_message(synth):
    make_build(synth)
    synth.edit("methods.tex", "Synthetic methods text", "Changed methods text")
    with pytest.raises(package_mod.PackageError) as caught:
        package_mod.package_run(project_of(synth), synth.run_dir)
    assert caught.value.rule == "build-stale"


# ---- M-G t3: the claim check at package time (M-G 5.8) ----------------------------------------------------

PLANTED_SENTENCE = "Timing changed \\cite{lee2010} as Figure~\\ref{fig:one} shows."


def plant_wrong_number(synth) -> None:
    """A Results sentence whose number the data package does not hold (C002), in the default article."""
    make_inputs(synth.run_dir, [{"id": "tbl-metrics", "file": "metrics.csv",
                                 "content": "metric,value\nSSIM,0.9134\n"}])
    synth.edit("main.tex", PLANTED_SENTENCE,
               PLANTED_SENTENCE + " SSIM was 0.93 in the Results. % source: [data:tbl-metrics]")


def test_a_claim_error_refuses_the_package_and_writes_nothing(synth):
    plant_wrong_number(synth)
    make_build(synth)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "claim error", "claims check")


def test_a_claim_check_that_cannot_run_refuses_closed(synth, monkeypatch):
    from scieflow.research.manuscript.claims import compile as claims_compile
    make_build(synth)

    def broken(project, run, main="main.tex"):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(claims_compile, "check_run", broken)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "RuntimeError", "fail closed")


def test_an_unreadable_status_at_package_time_refuses_closed(synth, monkeypatch):
    make_build(synth)
    calls = {"n": 0}

    def flaky(project, slug):
        calls["n"] += 1
        if calls["n"] == 1:
            return []                       # the M-F blocker check reads cleanly
        raise ValueError("bad review file")  # the claim check's status read fails

    monkeypatch.setattr(service, "blockers", flaky)
    before = snapshot(synth.run_dir)
    result = invoke()
    assert_refused(synth, result, before, "fail closed")


def test_a_waived_claim_error_lets_the_package_proceed(synth):
    from scieflow.research.manuscript.claims import waivers
    plant_wrong_number(synth)
    make_build(synth)
    from scieflow.research.manuscript.claims import ledger as claims_ledger
    claims_ledger.build(synth.run_dir)
    claim = [r for r in claims_ledger.read(synth.run_dir) if "0.93" in r["text"]][0]["id"]
    waivers.waive(synth.run_dir, project_of(synth), claim, "C002",
                  "The reviewer accepted the rounding of this synthetic value.")
    result = invoke()
    assert result.exit_code == 0, result.output
