"""M-G t3: waivers (M-G 5.7): human-only, refused in a dispatch, refused for C005 and data-unreleased, the
text hash voids a waiver, agent-actor events are ignored. Synthetic only."""

from __future__ import annotations

import pytest

from scieflow.core import events
from scieflow.research.manuscript.claims import compile as compile_mod
from scieflow.research.manuscript.claims import waivers
from tests.research.manuscript.claims.t3_support import body, built_check, of_code, results_run, rules, write
from tests.research.manuscript.t8_support import project_of

PLANTED = body("SSIM was 0.93 in the Results. % source: [data:tbl-metrics]")
REASON = "The reviewer accepted the rounding of this synthetic value."


def planted(synth) -> str:
    results_run(synth, PLANTED)
    built_check(synth)
    records = compile_mod.assemble(synth.run_dir).records
    return records[0]["id"]


def waive(synth, claim_id, code="C002", reason=REASON):
    return waivers.waive(synth.run_dir, project_of(synth), claim_id, code, reason)


def check(synth):
    return compile_mod.check_ledger(project_of(synth), synth.run_dir)


def test_a_human_waiver_moves_the_finding_to_waived_and_clears_the_error(synth):
    claim = planted(synth)
    assert check(synth).errors, "the planted number is an error before the waiver"
    waive(synth, claim)
    result = check(synth)
    assert result.errors == []
    assert [w["code"] for w in result.waived] == ["C002"]
    assert of_code(result, "C002") == []
    record = [e for e in events.read(synth.run_dir, types=("note.claims_waived",))]
    assert record and record[-1]["actor"] == "human"


def test_a_waiver_is_refused_inside_a_dispatch(synth, monkeypatch):
    claim = planted(synth)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    with pytest.raises(waivers.WaiverError) as info:
        waive(synth, claim)
    assert info.value.rule == "dispatch"
    assert events.read(synth.run_dir, types=("note.claims_waived",)) == []


def test_an_unknown_claim_is_refused(synth):
    planted(synth)
    with pytest.raises(waivers.WaiverError) as info:
        waive(synth, "c-000000000000")
    assert info.value.rule == "claim-unknown"


def test_a_code_the_claim_does_not_have_is_refused(synth):
    claim = planted(synth)
    with pytest.raises(waivers.WaiverError) as info:
        waive(synth, claim, code="C003")
    assert info.value.rule == "no-finding"


@pytest.mark.parametrize("code", ["C005", "C004", "data-unreleased"])
def test_blocked_references_and_unreleased_data_are_never_waivable(synth, code):
    claim = planted(synth)
    with pytest.raises(waivers.WaiverError) as info:
        waive(synth, claim, code=code)
    assert info.value.rule in ("not-waivable", "unknown-code")


@pytest.mark.parametrize("reason", ["too short", "x" * 501])
def test_the_reason_length_is_checked(synth, reason):
    claim = planted(synth)
    with pytest.raises(waivers.WaiverError) as info:
        waive(synth, claim, reason=reason)
    assert info.value.rule == "reason"


def test_editing_the_sentence_voids_the_waiver(synth):
    claim = planted(synth)
    waive(synth, claim)
    assert check(synth).errors == []
    edited = body("SSIM was 0.93 in the Results, re-measured. % source: [data:tbl-metrics]")
    write(synth.ms / "main.tex", (synth.ms / "main.tex").read_text(encoding="utf-8").replace(
        PLANTED.rstrip("\n").split("\n")[-1], edited.rstrip("\n").split("\n")[-1]))
    from scieflow.research.manuscript.claims import ledger
    ledger.build(synth.run_dir)
    result = check(synth)
    assert result.errors, "a changed sentence voids its waiver"


def test_an_agent_actor_waiver_event_is_ignored(synth):
    claim = planted(synth)
    record = compile_mod.assemble(synth.run_dir).records[0]
    events.emit(synth.run_dir, "note.claims_waived", "agent", claim=claim, code="C002",
                text_sha256=record["text_sha256"], reason=REASON)
    assert check(synth).errors, "only actor human waivers count"
