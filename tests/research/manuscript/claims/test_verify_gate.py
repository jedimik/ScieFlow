"""M-G2 t2: the verify gate on M-G1's real claim check (M-G2 5.4). Synthetic data only.

The planted number (0.93) is wrong against the synthetic evidence (0.9134); the marker values must never reach a
message or an event (M-G2 5.10).
"""

import shutil

import pytest
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import events
from scieflow.core.run import actions, status
from scieflow.research.manuscript.claims import compile as compile_mod
from scieflow.research.manuscript.claims import ledger, waivers
from tests.research.manuscript.claims.t3_support import MARKERS, body, results_run
from tests.research.manuscript.claims.test_blocked_reference import blocker, stub_blockers
from tests.research.manuscript.t8_support import project_of

CLEAN = body("SSIM was 0.91 in the Results. % source: [data:tbl-metrics]")
PLANTED = body("SSIM was 0.93 in the Results. % source: [data:tbl-metrics]")
REASON = "The reviewer accepted the rounding of this synthetic value."


@pytest.fixture(autouse=True)
def fresh_status_vocabulary(monkeypatch):
    monkeypatch.setattr(status, "_VOCAB", None)


def start(synth, workflow: str = "paper-draft"):
    status.write_status(synth.run_dir, status.new_status(synth.run_dir.name, "per-campaign", workflow))
    return synth.run_dir


def gate_events(run):
    return events.read(run, types=("note.phase_gate",))


def test_a_clean_paper_draft_passes_verify(synth):
    run = start(synth)
    results_run(synth, CLEAN)
    actions.mark_phase(run, "verify", "done", "agent")
    assert status.read_status(run)["phases"]["verify"] == "done"
    (ev,) = gate_events(run)
    assert ev["actor"] == "agent"
    assert ev["data"]["outcome"] == "passed" and ev["data"]["errors"] == 0
    assert not (run / "report" / "claims").exists(), "the gate writes nothing under report/claims/"


def test_a_planted_wrong_number_refuses_and_leaves_status_untouched(synth):
    run = start(synth)
    results_run(synth, PLANTED)
    before = (run / "status.yml").read_bytes()
    with pytest.raises(ValueError) as info:
        actions.mark_phase(run, "verify", "done", "agent")
    msg = str(info.value)
    assert "1 claim error(s)" in msg
    assert f"scieflow manuscript claims build {run.name}" in msg
    assert f"scieflow manuscript claims check {run.name}" in msg
    assert (run / "status.yml").read_bytes() == before
    (ev,) = gate_events(run)
    assert ev["data"]["outcome"] == "refused" and ev["data"]["errors"] == 1
    written = (run / "events.jsonl").read_text(encoding="utf-8")
    for marker in MARKERS:
        assert marker not in msg and marker not in written


def test_the_same_run_passes_once_the_number_is_fixed(synth):
    run = start(synth)
    results_run(synth, PLANTED)
    with pytest.raises(ValueError):
        actions.mark_phase(run, "verify", "done", "agent")
    results_run(synth, CLEAN)
    actions.mark_phase(run, "verify", "done", "agent")
    assert status.read_status(run)["phases"]["verify"] == "done"
    assert [e["data"]["outcome"] for e in gate_events(run)] == ["refused", "passed"]


def test_a_human_waiver_of_the_planted_number_lets_verify_pass(synth):
    run = start(synth)
    results_run(synth, PLANTED)
    ledger.build(run)
    claim = compile_mod.assemble(run).records[0]["id"]
    waivers.waive(run, project_of(synth), claim, "C002", REASON)
    actions.mark_phase(run, "verify", "done", "agent")
    assert status.read_status(run)["phases"]["verify"] == "done"
    assert gate_events(run)[-1]["data"]["outcome"] == "passed"


def test_a_blocked_reference_refuses_verify(synth, monkeypatch):
    stub_blockers(monkeypatch, [blocker("retracted", [{"doi": "https://doi.org/10.5555/SYN.2010.1"}])])
    run = start(synth)
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    with pytest.raises(ValueError, match=r"verify refused: \d+ claim error\(s\)"):
        actions.mark_phase(run, "verify", "done", "agent")
    assert gate_events(run)[0]["data"]["outcome"] == "refused"


def test_a_missing_main_tex_refuses_with_the_claims_rule(synth):
    run = start(synth)
    results_run(synth, CLEAN)
    (synth.ms / "main.tex").unlink()
    with pytest.raises(ValueError, match=r"verify refused: claims \["):
        actions.mark_phase(run, "verify", "done", "agent")
    (ev,) = gate_events(run)
    assert ev["data"]["outcome"] == "refused" and ev["data"]["rule"]


def test_a_paper_draft_without_a_manuscript_passes_as_no_manuscript(synth):
    run = start(synth, "paper-draft")
    shutil.rmtree(synth.ms)
    actions.mark_phase(run, "verify", "done", "agent")
    assert status.read_status(run)["phases"]["verify"] == "done"
    assert gate_events(run)[0]["data"]["outcome"] == "no-manuscript"


def test_a_paper_revision_without_a_manuscript_is_refused(synth):
    run = start(synth, "paper-revision")
    shutil.rmtree(synth.ms)
    with pytest.raises(ValueError, match="a paper-revision run needs its manuscript/ folder"):
        actions.mark_phase(run, "verify", "done", "agent")


def test_a_clean_paper_revision_passes_verify(synth):
    run = start(synth, "paper-revision")
    results_run(synth, CLEAN)
    actions.mark_phase(run, "verify", "done", "agent")
    assert status.read_status(run)["phases"]["verify"] == "done"
    assert gate_events(run)[0]["data"]["workflow"] == "paper-revision"


def test_run_mark_verify_done_over_the_cli_exits_one_on_a_planted_number_then_zero(synth):
    start(synth)
    results_run(synth, PLANTED)
    refused = CliRunner().invoke(main, ["run", "mark", "ms-run", "verify", "done", "--as-agent"])
    assert refused.exit_code == 1, refused.output
    assert "1 claim error(s)" in refused.output
    results_run(synth, CLEAN)
    passed = CliRunner().invoke(main, ["run", "mark", "ms-run", "verify", "done", "--as-agent"])
    assert passed.exit_code == 0, passed.output
    assert [e["data"]["outcome"] for e in gate_events(synth.run_dir)] == ["refused", "passed"]
