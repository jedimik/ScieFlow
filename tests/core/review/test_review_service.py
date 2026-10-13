"""The web/CLI-facing review API (ticket 05, plan T0.7/T0.8): `core/review/service.py`.

Every function is exercised on the fixture run (`tests/fixtures/review/run/`:
3 authors, 1 extra reviewer, one complete round with a blocker, no decisions)
and on an empty run, where readers return None/[] and never raise. Reader
outputs are pinned by golden JSON under `tests/fixtures/review/golden/`
(regenerate deliberately with SCIEFLOW_REGEN_GOLDEN=1).
"""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from scieflow.core import events
from scieflow.core.project import Project
from scieflow.core.review import assign, decisions as rd, service
from scieflow.core.review.cli import review
from scieflow.core.review.settings import SettingsError

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "tests" / "fixtures" / "review" / "run"
GOLDEN = ROOT / "tests" / "fixtures" / "review" / "golden"
SCHEMAS = ROOT / "src" / "scieflow" / "research" / "schemas"
FX, EMPTY = "fixture-run", "empty-run"
R1 = Path("review") / "round-1"


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for name in ("agents.yml", "defaults.yml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(RUN, tmp_path / "workspace" / FX)
    (tmp_path / "workspace" / EMPTY).mkdir()
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path)


def ws(project, slug=FX) -> Path:
    return project.run_dir(slug)


def golden(name: str, value) -> None:
    path = GOLDEN / f"{name}.json"
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("SCIEFLOW_REGEN_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert path.exists(), f"missing golden file {path} (SCIEFLOW_REGEN_GOLDEN=1 writes it)"
    assert json.loads(text) == json.loads(path.read_text()), name


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text()))


def decided_events(project, slug=FX):
    return events.read(ws(project, slug), types=("review.decided",))


PAIRS = [("agy", "claude-paper"), ("codex-review", "claude-paper"),
         ("claude-paper", "codex-paper"), ("codex-review", "codex-paper"),
         ("codex-paper", "agy"), ("codex-review", "agy")]


# --- the fixture run itself -------------------------------------------------------


def _schema_for(path: Path) -> str:
    if path.name == "assignment.json":
        return "review-assignment"
    if path.name == "matrix.json":
        return "review-matrix"
    if path.name == "decisions.json":
        return "review-decisions"
    if path.name == "bundle.json":
        return "evidence-bundle"
    return "manuscript-review"


def test_every_fixture_file_validates():
    files = sorted(RUN.rglob("*.json"))
    assert len(files) == 1 + 6 + 6 + 1 + 1     # assignment, 6 blind, 6 evidence, bundle, matrix
    for path in files:
        errors = list(validator(_schema_for(path)).iter_errors(json.loads(path.read_text())))
        assert not errors, f"{path.relative_to(RUN)}: {errors[0].message}"
    block = yaml.safe_load((RUN / "config.yml").read_text())["review"]
    from scieflow.core.review import settings
    assert settings.validate(block) == block
    assert not (RUN / R1 / "decisions.json").exists()


def test_fixture_shape_three_authors_one_extra_reviewer():
    data = json.loads((RUN / R1 / "assignment.json").read_text())
    assert [a["agent"] for a in data["authors"]] == ["claude-paper", "codex-paper", "agy"]
    extra = [r for r in data["reviewers"] if r["agent"] not in
             {a["agent"] for a in data["authors"]}]
    assert [(r["agent"], r["covers"]) for r in extra] == [("codex-review", "all")]
    assert sorted((p["reviewer"], p["author"]) for p in data["pairs"]) == sorted(PAIRS)
    for rv, au in PAIRS:
        assert (RUN / R1 / f"{rv}-on-{au}.blind.json").exists()
        assert (RUN / R1 / f"{rv}-on-{au}.evidence.json").exists()


def test_fixture_assignment_is_what_the_planner_writes(project):
    plan = service.preview_assignment(project, FX)
    stored = json.loads((RUN / R1 / "assignment.json").read_text())
    assert assign.dumps({**plan, "round": 1}) == (RUN / R1 / "assignment.json").read_text()
    assert stored["version"] == 1


def test_fixture_matrix_covers_every_comment_and_flag_exactly_once():
    matrix = json.loads((RUN / R1 / "matrix.json").read_text())
    bundle = json.loads((RUN / R1 / "evidence" / "bundle.json").read_text())
    comments = set()
    for rv, au in PAIRS:
        for pass_ in ("blind", "evidence"):
            data = json.loads((RUN / R1 / f"{rv}-on-{au}.{pass_}.json").read_text())
            for c in data["major"] + data["minor"]:
                comments.add((rv, au, pass_, c["id"]))
    members = [(m["reviewer"], m["author"], m["pass"], m["id"])
               for c in matrix["clusters"] for m in c["members"]]
    assert sorted(members) == sorted(comments)          # each comment exactly once
    flags = [f for c in matrix["clusters"] for f in c.get("flags", [])]
    assert sorted(flags) == sorted(f["id"] for f in bundle["flags"])
    blocker_flags = {f["id"] for f in bundle["flags"] if f["blocker"]}
    blocker_clusters = [c for c in matrix["clusters"] if c["blocker"]]
    assert [c["cluster"] for c in blocker_clusters] == ["K1"]
    assert set(blocker_clusters[0]["flags"]) == blocker_flags == {"F1"}


# --- readers on the fixture run -------------------------------------------------------


def test_review_settings(project):
    golden("review_settings", service.review_settings(project, FX))


def test_assignment_latest_and_by_round(project):
    stored = json.loads((RUN / R1 / "assignment.json").read_text())
    assert service.assignment(project, FX) == stored
    assert service.assignment(project, FX, 1) == stored
    assert service.assignment(project, FX, 2) is None


def test_rounds(project):
    golden("rounds", service.rounds(project, FX))


@pytest.mark.parametrize("pair", PAIRS)
@pytest.mark.parametrize("pass_", ["blind", "evidence"])
def test_review_pass_reads_every_file(project, pair, pass_):
    got = service.review_pass(project, FX, 1, pair[0], pair[1], pass_)
    assert got["pass"] == pass_ and got["agent"] == pair[0] and got["reviewed"] == pair[1]


def test_review_pass_golden(project):
    golden("review_pass", service.review_pass(project, FX, 1, "codex-review", "agy", "evidence"))


def test_review_pass_without_pass_field_reads_as_blind(project):
    path = ws(project) / R1 / "agy-on-claude-paper.blind.json"
    data = json.loads(path.read_text())
    del data["pass"]
    path.write_text(json.dumps(data))
    assert service.review_pass(project, FX, 1, "agy", "claude-paper", "blind")["pass"] == "blind"


def test_evidence_summary(project):
    golden("evidence_summary", service.evidence_summary(project, FX, 1))


def test_matrix(project):
    assert service.matrix(project, FX, 1) == json.loads((RUN / R1 / "matrix.json").read_text())


def test_blockers(project):
    got = service.blockers(project, FX)
    golden("blockers", got)
    assert [b["cluster"] for b in got] == ["K1"]
    assert got[0]["reason"] == "retracted"


def test_scite_status_is_not_configured_without_config_scite_yml(project):
    got = service.scite_status(project)
    golden("scite_status", got)
    assert got["configured"] is False


def test_decisions_absent_on_fixture(project):
    assert service.decisions(project, FX, 1) is None


# --- empty and unknown runs ---------------------------------------------------------


@pytest.mark.parametrize("slug", [EMPTY, "no-such-run"])
def test_readers_on_empty_or_unknown_run_never_raise(project, slug):
    assert service.assignment(project, slug) is None
    assert service.assignment(project, slug, 1) is None
    assert service.rounds(project, slug) == []
    assert service.review_pass(project, slug, 1, "agy", "claude-paper", "blind") is None
    assert service.evidence_summary(project, slug, 1) is None
    assert service.matrix(project, slug, 1) is None
    assert service.decisions(project, slug, 1) is None
    assert service.blockers(project, slug) == []
    assert service.scite_status(project)["configured"] is False


def test_settings_on_empty_run_are_the_defaults(project):
    got = service.review_settings(project, EMPTY)
    assert got["scite"] == "ask" and got["reviews_per_draft"] == 2 and got["pool"] is None


def test_preview_assignment_on_empty_run_uses_default_roles(project):
    plan = service.preview_assignment(project, EMPTY)
    assert plan["pairs"] and "round" not in plan
    assert not (ws(project, EMPTY) / "review").exists()


def test_empty_review_dir_and_partial_round(project):
    (ws(project, EMPTY) / "review" / "round-1").mkdir(parents=True)
    (ws(project, EMPTY) / "review" / "notes").mkdir()
    assert service.rounds(project, EMPTY) == [{
        "round": 1, "assignment": False, "pairs": [], "evidence": False,
        "scite_status": None, "matrix": False, "decisions": False}]
    assert service.blockers(project, EMPTY) == []


def test_decide_on_empty_run_is_refused_without_writing(project):
    with pytest.raises(rd.DecisionError, match="no matrix"):
        service.decide(project, EMPTY, 1, {"K1": {"decision": "accept"}}, "human")
    assert not (ws(project, EMPTY) / "review").exists()
    assert decided_events(project, EMPTY) == []


# --- input validation ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["", " ", "..", ".", "a/b", "../x", "a\\b", "a b", "x\n",
                                  "x\u202e", 3, None])
def test_path_shaped_names_are_refused(project, name):
    with pytest.raises(service.ReviewError):
        service.review_pass(project, FX, 1, name, "agy", "blind")
    with pytest.raises(service.ReviewError):
        service.review_pass(project, FX, 1, "agy", name, "blind")


@pytest.mark.parametrize("pass_", ["", "both", "../blind", None])
def test_unknown_pass_is_refused(project, pass_):
    with pytest.raises(service.ReviewError, match="pass"):
        service.review_pass(project, FX, 1, "agy", "claude-paper", pass_)


@pytest.mark.parametrize("round_", [0, -1, True, "1", 1.0, None])
def test_bad_round_is_refused(project, round_):
    for call in (lambda: service.matrix(project, FX, round_),
                 lambda: service.evidence_summary(project, FX, round_),
                 lambda: service.review_pass(project, FX, round_, "agy", "claude-paper", "blind"),
                 lambda: service.decisions(project, FX, round_),
                 lambda: service.decide(project, FX, round_, {"K2": {"decision": "accept"}},
                                        "human")):
        with pytest.raises(ValueError, match="round"):
            call()


@pytest.mark.parametrize("slug", ["../x", "a/b", ""])
def test_path_shaped_slug_is_refused(project, slug):
    with pytest.raises(ValueError):
        service.rounds(project, slug)


def test_symlinked_round_outside_the_run_is_not_read(project, tmp_path):
    outside = tmp_path / "outside"
    shutil.copytree(RUN / R1, outside)
    (ws(project) / "review" / "round-2").symlink_to(outside)
    assert [r["round"] for r in service.rounds(project, FX)] == [1]
    with pytest.raises(service.ReviewError, match="outside"):
        service.matrix(project, FX, 2)


def test_corrupt_json_is_an_error_not_none(project):
    (ws(project) / R1 / "matrix.json").write_text("{not json")
    with pytest.raises(service.ReviewError, match="matrix.json"):
        service.matrix(project, FX, 1)


def test_invalid_matrix_is_an_error(project):
    path = ws(project) / R1 / "matrix.json"
    data = json.loads(path.read_text())
    data["clusters"][0]["recommendation"] = "maybe"
    path.write_text(json.dumps(data))
    with pytest.raises(service.ReviewError, match="review-matrix"):
        service.matrix(project, FX, 1)


# --- settings and assignment preview --------------------------------------------------


def test_set_review_settings_writes_and_refuses_agents(project):
    got = service.set_review_settings(project, FX, {"reviews_per_draft": 2}, "human")
    assert got["reviews_per_draft"] == 2
    assert service.review_settings(project, FX)["reviews_per_draft"] == 2
    with pytest.raises(SettingsError, match="only the user"):
        service.set_review_settings(project, FX, {"allow_self_review": True}, "agent")
    with pytest.raises(SettingsError, match="only the user"):
        service.set_review_settings(project, FX, {"scite": "approved"}, "agent")
    assert service.set_review_settings(project, FX, {"scite": "off"}, "agent")["scite"] == "off"


def test_preview_assignment_applies_unsaved_changes(project):
    plan = service.preview_assignment(project, FX, {"allow_same_family_review": False})
    assert ("codex-review", "codex-paper") not in {(p["reviewer"], p["author"])
                                                   for p in plan["pairs"]}
    # nothing written
    assert service.review_settings(project, FX)["allow_same_family_review"] is True
    with pytest.raises(SettingsError):
        service.preview_assignment(project, FX, {"reviews_per_draft": 0})
    with pytest.raises(SettingsError, match="unknown key"):
        service.preview_assignment(project, FX, {"bogus": 1})


def test_preview_assignment_reports_zero_eligible_error(project):
    with pytest.raises(assign.AssignmentError, match="allow_same_family_review"):
        service.preview_assignment(project, FX, {
            "pool": [{"agent": "codex-review"}], "allow_same_family_review": False,
            "reviews_per_draft": 1})


# --- decide -------------------------------------------------------------------------


def test_decide_writes_atomically_validated_and_emits_event(project):
    got = service.decide(project, FX, 1, {"K2": {"decision": "accept", "note": "agree"}},
                         "human")
    path = ws(project) / R1 / "decisions.json"
    on_disk = json.loads(path.read_text())
    assert got == on_disk
    assert not list(validator("review-decisions").iter_errors(on_disk))
    entry = on_disk["decisions"]["K2"]
    assert entry["decision"] == "accept" and entry["actor"] == "human" and entry["note"] == "agree"
    assert on_disk["matrix_hash"] == hashlib.sha256(
        (ws(project) / R1 / "matrix.json").read_bytes()).hexdigest()
    [ev] = decided_events(project)
    assert ev["actor"] == "human"
    assert ev["data"]["round"] == 1 and ev["data"]["clusters"] == ["K2"]
    assert ev["data"]["decisions_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert service.decisions(project, FX, 1) == on_disk
    assert [p.name for p in (ws(project) / R1).iterdir() if p.name.startswith(".")] == []


def test_decide_merges_with_earlier_decisions(project):
    service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    got = service.decide(project, FX, 1, {"K3": {"decision": "reject"},
                                          "K2": {"decision": "defer"}}, "human")
    assert {k: v["decision"] for k, v in got["decisions"].items()} == {"K2": "defer",
                                                                     "K3": "reject"}
    assert list(got["decisions"]) == ["K2", "K3"]       # matrix order
    assert [e["data"]["clusters"] for e in decided_events(project)] == [["K2"], ["K2", "K3"]]
    rounds = service.rounds(project, FX)
    assert rounds[0]["decisions"] is True


@pytest.mark.parametrize("bad, match", [
    ({}, "at least one"),
    ([], "mapping"),
    ({"K99": {"decision": "accept"}}, "unknown cluster"),
    ({"../K1": {"decision": "accept"}}, "unknown cluster"),
    ({"K2": {"decision": "maybe"}}, "accept, reject, defer"),
    ({"K2": "accept"}, "mapping"),
    ({"K2": {}}, "decision"),
    ({"K2": {"decision": "accept", "actor": "human"}}, "unknown field"),
    ({"K2": {"decision": "accept", "note": 3}}, "note"),
    ({"K2": {"decision": "accept", "rationale": ""}}, "rationale"),
    ({"K2": {"decision": "accept", "overrule_reason": "x"}}, "not a blocker"),
])
def test_decide_refuses_bad_input_and_writes_nothing(project, bad, match):
    with pytest.raises(rd.DecisionError, match=match):
        service.decide(project, FX, 1, bad, "human")
    assert not (ws(project) / R1 / "decisions.json").exists()
    assert decided_events(project) == []


def test_decide_refuses_unknown_actor(project):
    with pytest.raises(rd.DecisionError, match="actor"):
        service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "system")


@pytest.mark.parametrize("decision", ["accept", "reject", "defer"])
def test_blocker_decision_needs_a_human_overrule_reason(project, decision):
    with pytest.raises(rd.DecisionError, match="overrule_reason"):
        service.decide(project, FX, 1, {"K1": {"decision": decision}}, "human")
    with pytest.raises(rd.DecisionError, match="overrule_reason"):
        service.decide(project, FX, 1, {"K1": {"decision": decision,
                                               "overrule_reason": "   "}}, "human")
    assert not (ws(project) / R1 / "decisions.json").exists()
    assert decided_events(project) == []


def test_blocker_refusal_also_blocks_the_rest_of_the_batch(project):
    with pytest.raises(rd.DecisionError, match="K1"):
        service.decide(project, FX, 1, {"K2": {"decision": "accept"},
                                        "K1": {"decision": "reject"}}, "human")
    assert not (ws(project) / R1 / "decisions.json").exists()


def test_human_overrule_clears_the_blocker(project):
    service.decide(project, FX, 1, {"K1": {"decision": "reject",
                                           "overrule_reason": "Retraction concerns Fig. 3 only"}},
                   "human")
    assert service.blockers(project, FX) == []


def test_accepting_a_blocker_keeps_it_open_until_fixed(project):
    service.decide(project, FX, 1, {"K1": {"decision": "accept",
                                           "overrule_reason": "Replace the citation"}}, "human")
    [b] = service.blockers(project, FX)
    assert b["cluster"] == "K1" and b["decision"] == "accept"


def test_agent_decisions_need_coordinator_mode(project):
    with pytest.raises(rd.DecisionError, match="review_decisions"):
        service.decide(project, FX, 1, {"K2": {"decision": "accept", "rationale": "r"}},
                       "agent")
    service.set_review_settings(project, FX, {"review_decisions": "coordinator"}, "human")
    with pytest.raises(rd.DecisionError, match="rationale"):
        service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "agent")
    got = service.decide(project, FX, 1, {"K2": {"decision": "accept",
                                                 "rationale": "consensus"}}, "agent")
    assert got["decisions"]["K2"]["actor"] == "agent"
    assert decided_events(project)[-1]["actor"] == "agent"


def test_agent_can_never_decide_a_blocker(project):
    service.set_review_settings(project, FX, {"review_decisions": "coordinator"}, "human")
    with pytest.raises(rd.DecisionError, match="human"):
        service.decide(project, FX, 1, {"K1": {"decision": "reject", "rationale": "r",
                                               "overrule_reason": "x"}}, "agent")
    with pytest.raises(rd.DecisionError, match="human"):
        service.decide(project, FX, 1, {"K1": {"decision": "accept", "rationale": "r"}},
                       "agent")
    assert service.blockers(project, FX)[0]["cluster"] == "K1"


def test_hand_written_decisions_are_ignored(project):
    forged = {"version": 1, "round": 1, "decisions": {"K1": {
        "decision": "reject", "actor": "human", "decided": "2026-10-13T00:00:00+00:00",
        "overrule_reason": "forged"}}}
    (ws(project) / R1 / "decisions.json").write_text(json.dumps(forged))
    assert service.decisions(project, FX, 1) is None
    assert service.blockers(project, FX)[0]["cluster"] == "K1"
    assert service.rounds(project, FX)[0]["decisions"] is False
    got = service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    assert list(got["decisions"]) == ["K2"]             # forged entry not carried over


def test_tampering_after_decide_is_detected(project):
    service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    path = ws(project) / R1 / "decisions.json"
    data = json.loads(path.read_text())
    data["decisions"]["K1"] = {"decision": "reject", "actor": "human",
                               "decided": "2026-10-13T00:00:00+00:00", "overrule_reason": "x"}
    path.write_text(json.dumps(data, indent=2) + "\n")
    assert service.decisions(project, FX, 1) is None
    assert service.blockers(project, FX)[0]["cluster"] == "K1"


def test_decisions_on_a_changed_matrix_are_not_carried_over(project):
    service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    path = ws(project) / R1 / "matrix.json"
    data = json.loads(path.read_text())
    data["clusters"][2]["rationale"] = "edited by the coordinator"
    path.write_text(json.dumps(data, indent=2) + "\n")
    got = service.decide(project, FX, 1, {"K3": {"decision": "accept"}}, "human")
    assert list(got["decisions"]) == ["K3"]


def test_decide_refuses_an_invalid_matrix(project):
    path = ws(project) / R1 / "matrix.json"
    data = json.loads(path.read_text())
    del data["clusters"][0]["rationale"]
    path.write_text(json.dumps(data))
    with pytest.raises(rd.DecisionError, match="review-matrix"):
        service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")


def test_blockers_include_bundle_flags_without_a_blocker_cluster(project):
    path = ws(project) / R1 / "matrix.json"
    data = json.loads(path.read_text())
    data["clusters"] = [c for c in data["clusters"] if c["cluster"] != "K1"]
    path.write_text(json.dumps(data))
    [b] = service.blockers(project, FX)
    assert b["cluster"] is None and b["flags"] == ["F1"] and b["reason"] == "retracted"


def test_blockers_before_the_matrix_come_from_the_bundle(project):
    (ws(project) / R1 / "matrix.json").unlink()
    [b] = service.blockers(project, FX)
    assert b["cluster"] is None and b["references"][0]["doi"] == "10.5555/rbd.2020.014"


def test_blockers_follow_the_latest_round(project):
    r2 = ws(project) / "review" / "round-2" / "evidence"
    r2.mkdir(parents=True)
    bundle = json.loads((RUN / R1 / "evidence" / "bundle.json").read_text())
    bundle["round"] = 2
    bundle["flags"] = [f for f in bundle["flags"] if not f["blocker"]]
    bundle["references"][1]["status"] = "ok"
    (r2 / "bundle.json").write_text(json.dumps(bundle))
    assert service.blockers(project, FX) == []


# --- CLI ---------------------------------------------------------------------------


def test_cli_status_json(project):
    out = CliRunner().invoke(review, ["status", FX, "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["rounds"] == service.rounds(project, FX)
    assert data["blockers"] == service.blockers(project, FX)
    assert data["scite"] == service.scite_status(project)


def test_cli_status_text(project):
    out = CliRunner().invoke(review, ["status", FX])
    assert out.exit_code == 0, out.output
    assert "round 1" in out.output and "K1" in out.output and "blocker" in out.output
    empty = CliRunner().invoke(review, ["status", EMPTY])
    assert empty.exit_code == 0 and "no review rounds" in empty.output


def test_cli_decide(project):
    cli = CliRunner()
    refused = cli.invoke(review, ["decide", FX, "--round", "1", "--decision", "K1=reject",
                                  "--yes"])
    assert refused.exit_code != 0 and "overrule_reason" in refused.output
    ok = cli.invoke(review, ["decide", FX, "--round", "1", "--decision", "K1=reject",
                             "--overrule", "K1=Retraction concerns Fig. 3 only",
                             "--decision", "K2=accept", "--note", "K2=agreed", "--yes"])
    assert ok.exit_code == 0, ok.output
    got = service.decisions(project, FX, 1)["decisions"]
    assert got["K1"]["overrule_reason"] == "Retraction concerns Fig. 3 only"
    assert got["K2"]["note"] == "agreed"
    agent = cli.invoke(review, ["decide", FX, "--round", "1", "--decision", "K3=accept",
                                "--as-agent", "--rationale", "r", "--yes"])
    assert agent.exit_code != 0 and "review_decisions" in agent.output


def test_cli_decide_rejects_malformed_pairs(project):
    out = CliRunner().invoke(review, ["decide", FX, "--round", "1", "--decision", "K2",
                                      "--yes"])
    assert out.exit_code != 0 and "CLUSTER=" in out.output


# --- review findings (ticket 05) ------------------------------------------------------


def _overrule_k1(project):
    service.decide(project, FX, 1, {"K1": {"decision": "reject",
                                           "overrule_reason": "Retraction concerns Fig. 3 only"}},
                   "human")


def test_overrule_does_not_survive_a_matrix_rewrite(project):
    _overrule_k1(project)
    assert service.blockers(project, FX) == []
    path = ws(project) / R1 / "matrix.json"
    data = json.loads(path.read_text())
    data["clusters"][0]["rationale"] = "rewritten after a new unresolvable reference"
    path.write_text(json.dumps(data, indent=2) + "\n")
    assert [b["cluster"] for b in service.blockers(project, FX)] == ["K1"]
    assert service.decisions(project, FX, 1) is None
    assert service.rounds(project, FX)[0]["decisions"] is False


@pytest.mark.parametrize("line", ["[1]", '"text"', "3",
                                  '{"type": "review.decided", "data": "x"}',
                                  '{"type": "review.decided", "data": {"round": 1}}'])
def test_malformed_event_lines_do_not_break_readers(project, line):
    service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    with (ws(project) / "events.jsonl").open("a") as fh:
        fh.write(line + "\n")
    service.rounds(project, FX)
    service.blockers(project, FX)
    service.decisions(project, FX, 1)
    got = service.decide(project, FX, 1, {"K3": {"decision": "accept"}}, "human")
    assert set(got["decisions"]) == {"K2", "K3"}


def _append_event(project, actor, **data):
    record = {"id": "X", "ts": "2026-10-13T00:00:00+00:00", "run": FX, "slug": FX,
              "type": "review.decided", "actor": actor, "data": data}
    with (ws(project) / "events.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def test_forged_event_without_matrix_hash_is_ignored(project):
    doc = {"version": 1, "round": 1, "decisions": {"K1": {
        "decision": "reject", "actor": "human", "decided": "2026-10-13T00:00:00+00:00",
        "overrule_reason": "forged"}}}
    text = json.dumps(doc, indent=2) + "\n"
    (ws(project) / R1 / "decisions.json").write_text(text)
    _append_event(project, "agent", round=1, clusters=["K1"],
                  decisions_hash=hashlib.sha256(text.encode()).hexdigest())
    assert service.decisions(project, FX, 1) is None
    assert [b["cluster"] for b in service.blockers(project, FX)] == ["K1"]


def test_forged_event_whose_actor_differs_from_the_entries_is_ignored(project):
    mhash = hashlib.sha256((ws(project) / R1 / "matrix.json").read_bytes()).hexdigest()
    doc = {"version": 1, "round": 1, "matrix_hash": mhash, "decisions": {"K1": {
        "decision": "reject", "actor": "human", "decided": "2026-10-13T00:00:00+00:00",
        "overrule_reason": "forged"}}}
    text = json.dumps(doc, indent=2) + "\n"
    (ws(project) / R1 / "decisions.json").write_text(text)
    _append_event(project, "agent", round=1, clusters=["K1"], matrix_hash=mhash,
                  decisions_hash=hashlib.sha256(text.encode()).hexdigest(),
                  decisions=doc["decisions"])
    assert service.decisions(project, FX, 1) is None
    assert [b["cluster"] for b in service.blockers(project, FX)] == ["K1"]


def test_a_failed_event_does_not_lose_earlier_decisions(project, monkeypatch):
    service.decide(project, FX, 1, {"K2": {"decision": "accept"}}, "human")
    real = events.emit

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(events, "emit", boom)
    with pytest.raises(OSError):
        service.decide(project, FX, 1, {"K3": {"decision": "accept"}}, "human")
    monkeypatch.setattr(events, "emit", real)
    got = service.decide(project, FX, 1, {"K4": {"decision": "accept"}}, "human")
    assert set(got["decisions"]) == {"K2", "K4"}


def _round2_bundle(project, *, drop_blocker=False, other_doi=False):
    r2 = ws(project) / "review" / "round-2" / "evidence"
    r2.mkdir(parents=True)
    bundle = json.loads((RUN / R1 / "evidence" / "bundle.json").read_text())
    bundle["round"] = 2
    if other_doi:
        for f in bundle["flags"]:
            if f["id"] == "F1":
                f["doi"], f["key"] = "10.5555/rbd.2018.099", "other2018"
    (r2 / "bundle.json").write_text(json.dumps(bundle))


def test_human_overrule_carries_into_later_rounds_for_the_same_reference(project):
    _overrule_k1(project)
    _round2_bundle(project)
    assert service.blockers(project, FX) == []


def test_overrule_does_not_cover_a_different_reference(project):
    _overrule_k1(project)
    _round2_bundle(project, other_doi=True)
    [b] = service.blockers(project, FX)
    assert b["round"] == 2 and b["references"][0]["doi"] == "10.5555/rbd.2018.099"


def test_decisions_path_that_is_a_directory_reads_as_absent(project):
    (ws(project) / R1 / "decisions.json").mkdir()
    assert service.decisions(project, FX, 1) is None
    assert service.rounds(project, FX)[0]["decisions"] is False
    assert [b["cluster"] for b in service.blockers(project, FX)] == ["K1"]


def test_settings_of_an_unknown_run_are_a_settings_error(project):
    with pytest.raises(SettingsError, match="no workspace"):
        service.review_settings(project, "no-such-run")
    with pytest.raises(SettingsError, match="no workspace"):
        service.preview_assignment(project, "no-such-run")
