"""M-C2 t5 (plan 5.5, 5.8; D18): identity blockers, their human-only gates and overrules.

verify-bib --round N records the check (review/round-N/identity.json and one review.identity_checked
event); every refusal-grade reference (mismatch, unresolvable) opens one human-only review-blocker
gate; `review merge-check` and service.blockers() read the event. All HTTP is the offline gold
transport (tests/research/kernel2/identity_support.py); the synthetic documents use reserved DOIs.
Test data: published bibliographic metadata from the gold set, or invented reserved identifiers.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
from pathlib import Path

import pytest
import responses
import yaml
from click.testing import CliRunner

from scieflow.core import events, gates
from scieflow.core.project import Project
from scieflow.core.review import service
from scieflow.core.review.cli import review
from scieflow.research import verify_bib
from scieflow.research.lib import providers
from tests.research.kernel2.identity_support import (
    BASELINE_FILE,
    GOLD_YAML,
    IDENTITY_DIR,
    IDENTITY_URL,
    GoldTransport,
)

ROOT = Path(__file__).resolve().parents[3]
BIB_TEXT = (IDENTITY_DIR / "references.bib").read_text(encoding="utf-8")
GOLD_ITEMS = yaml.safe_load(GOLD_YAML.read_text(encoding="utf-8"))["items"]
BASELINE = {i["doi"]: i for i in json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["items"]}
BLOCKER_OPTIONS = ["overrule", "fixed"]


def gold_entry(expected: str, nth: int = 0) -> tuple[str, str]:
    """(bib key, entry text) of the nth gold item whose expected and baseline verdict are `expected`."""
    index = [i for i, item in enumerate(GOLD_ITEMS)
             if item["expected"] == expected and BASELINE[item["doi"]]["verdict"] == expected
             ][nth] + 1
    key = f"gold{index:03d}"
    blocks = re.split(r"\n(?=@)", BIB_TEXT.strip())
    return key, next(b for b in blocks if b.startswith("@article{" + key + ","))


def gold_title(key: str) -> str:
    return GOLD_ITEMS[int(key[4:]) - 1]["title"]


MIS = gold_entry("mismatch", 0)
UNR = gold_entry("unresolvable", 0)
VER = gold_entry("verified", 0)
INC = gold_entry("inconclusive", 0)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A synthetic project with one run `demo` that has review round 1 but no evidence."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    shutil.copytree(ROOT / "schemas", root / "schemas")
    ws = root / "workspace" / "demo"
    (ws / "review" / "round-1").mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.chdir(root)
    return Project(root), ws


@pytest.fixture
def fast(monkeypatch):
    real = providers.http.send

    def send(*args, **kwargs):
        kwargs["retries"] = 1
        kwargs["sleep"] = lambda seconds: None
        return real(*args, **kwargs)

    monkeypatch.setattr(providers.http, "send", send)
    monkeypatch.setattr(providers.http.RateLimiter, "wait", lambda self: None)


@pytest.fixture
def gold_http(fast):
    transport = GoldTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=transport)
        yield transport


def write_bib(ws: Path, text: str) -> None:
    (ws / "manuscript").mkdir(exist_ok=True)
    (ws / "manuscript" / "references.bib").write_text(text, encoding="utf-8")


def vb(capsys, *args):
    try:
        verify_bib.main(list(args))
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def merge(slug: str = "demo"):
    return CliRunner().invoke(review, ["merge-check", slug])


def identity_open(ws: Path) -> list[dict]:
    return [g for g in gates.list_gates(ws, "open") if g["question"].startswith("[identity ")]


def checks(ws: Path) -> list[dict]:
    return events.read(ws, types=("review.identity_checked",))


def overrules(ws: Path) -> list[dict]:
    return events.read(ws, types=("review.identity_overruled",))


def gate_for(ws: Path, key: str) -> dict:
    found = [g for g in identity_open(ws) if key in g["question"]]
    assert len(found) == 1, [g["question"] for g in identity_open(ws)]
    return found[0]


# --- the check and its gates (RED items 1, 2, 4, 9-half) -------------------------------------


def test_a_round_check_writes_the_file_one_event_and_one_gate_per_refusal(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, "\n\n".join([MIS[1], UNR[1], VER[1]]) + "\n")
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 4, err
    assert (ws / "review" / "round-1" / "identity.json").is_file()
    evs = checks(ws)
    assert len(evs) == 1
    data = evs[0]["data"]
    assert data["round"] == 1
    assert sorted((r["key"], r["verdict"]) for r in data["refusal"]) == sorted(
        [(MIS[0], "mismatch"), (UNR[0], "unresolvable")])
    opened = identity_open(ws)
    assert len(opened) == 2
    for g in opened:
        assert g["kind"] == "review-blocker" and g["requires_human"] is True
        assert g["options"] == BLOCKER_OPTIONS and g["opened_by"] == "system"
    res = merge()
    assert res.exit_code == 1
    assert "identity: mismatch" in res.output and "identity: unresolvable" in res.output
    blockers = service.blockers(project, "demo")
    assert len(blockers) == 2
    assert all(b["cluster"] is None and b["reason"].startswith("identity-") for b in blockers)
    assert all(b["gate"] for b in blockers)


def test_an_inconclusive_reference_never_blocks(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, INC[1] + "\n\n" + VER[1] + "\n")
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 0, err
    assert identity_open(ws) == []
    assert service.blockers(project, "demo") == []
    assert merge().exit_code == 0


def test_an_unchanged_recheck_opens_no_second_gate(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    first = sorted(g["id"] for g in identity_open(ws))
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 4, err
    assert sorted(g["id"] for g in identity_open(ws)) == first
    assert len(checks(ws)) == 2


def test_fixing_the_reference_withdraws_its_gate_and_a_clean_check_clears_the_blockers(
        proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    unr_gate = gate_for(ws, UNR[0])
    write_bib(ws, MIS[1] + "\n\n" + VER[1] + "\n")          # the invented reference is replaced
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 4, err
    assert gates.get(ws, unr_gate["id"])["state"] == "withdrawn"
    assert [g["question"] for g in identity_open(ws)] and gate_for(ws, MIS[0])
    write_bib(ws, VER[1] + "\n")
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 0, err
    assert identity_open(ws) == []
    assert service.blockers(project, "demo") == []
    assert merge().exit_code == 0


# --- overrules and answers (RED items 3, 5, 6) -------------------------------------------------


def test_a_human_overrule_with_a_note_clears_the_blocker_and_is_recorded(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    gates.answer(project, ws, gate_for(ws, MIS[0])["id"], "overrule", "human", "",
                 note="Checked by hand: the printed DOI is right")
    evs = overrules(ws)
    assert len(evs) == 1 and evs[0]["actor"] == "human"
    data = evs[0]["data"]
    assert (data["round"], data["key"], data["verdict"]) == (1, MIS[0], "mismatch")
    assert [b["references"][0]["key"] for b in service.blockers(project, "demo")] == [UNR[0]]


def test_an_overrule_without_a_note_is_refused_and_the_gate_stays_open(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n" + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    gid = gate_for(ws, MIS[0])["id"]
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, gid, "overrule", "human")
    assert gates.get(ws, gid)["state"] == "open"
    assert overrules(ws) == []


def test_no_agent_can_answer_an_identity_gate(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    gid = gate_for(ws, MIS[0])["id"]
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, gid, "overrule", actor="agent", rationale="looks fine", note="ok")
    assert gates.get(ws, gid)["state"] == "open"
    assert len(service.blockers(project, "demo")) == 1


def test_a_look_alike_identity_gate_opened_by_an_agent_is_refused(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    real = gate_for(ws, MIS[0])
    fake = gates.open_gate(project, ws, "review-blocker", real["question"], options=BLOCKER_OPTIONS,
                           actor="agent")
    with pytest.raises(gates.GateError):
        gates.answer(project, ws, fake["id"], "overrule", "human", "", note="reviewed")
    assert gates.get(ws, fake["id"])["state"] == "open"
    assert overrules(ws) == []


def test_a_gate_from_an_older_check_is_refused_and_says_to_re_run(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    old_question = gate_for(ws, MIS[0])["question"]
    write_bib(ws, MIS[1] + "\n\n" + VER[1] + "\n")          # a newer check, another bibliography
    vb(capsys, "demo", "--round", "1", "--json")
    stale = gates.open_gate(project, ws, "review-blocker", old_question, options=BLOCKER_OPTIONS,
                            actor="system")
    with pytest.raises(gates.GateError, match="re-run verify-bib"):
        gates.answer(project, ws, stale["id"], "overrule", "human", "", note="old check")
    assert overrules(ws) == []


def test_fixed_records_nothing_and_the_blocker_stays(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    gates.answer(project, ws, gate_for(ws, MIS[0])["id"], "fixed", "human")
    assert overrules(ws) == []
    assert len(service.blockers(project, "demo")) == 1


def test_an_overrule_holds_in_a_later_round_for_the_same_verdict(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    gates.answer(project, ws, gate_for(ws, MIS[0])["id"], "overrule", "human", "", note="reviewed")
    (ws / "review" / "round-2").mkdir()
    code, out, err = vb(capsys, "demo", "--round", "2", "--json")
    assert code == 4, err
    blockers = service.blockers(project, "demo")
    assert [b["references"][0]["key"] for b in blockers] == [UNR[0]]
    assert blockers[0]["round"] == 2


def test_a_different_verdict_for_the_same_reference_blocks_again(proj):
    project, ws = proj
    ig = importlib.import_module("scieflow.core.review.identity_gates")
    doi = "10.5555/synthetic-identity-t5"
    first = {"version": 1, "bib": "manuscript/references.bib", "bib_sha256": "0" * 64,
             "sources": [{"source": "openalex", "status": "ok"}],
             "report": {"entries": [{"key": "ref1", "verdict": "mismatch",
                                     "reference": {"doi": doi}}]}}
    ig.record(project, ws, 1, first)
    gates.answer(project, ws, gate_for(ws, "ref1")["id"], "overrule", "human", "", note="reviewed")
    (ws / "review" / "round-2").mkdir()
    second = json.loads(json.dumps(first))
    second["report"]["entries"][0]["verdict"] = "unresolvable"
    ig.record(project, ws, 2, second)
    blockers = service.blockers(project, "demo")
    assert [b["reason"] for b in blockers] == ["identity-unresolvable"]


# --- tampering and the event as the authority (RED item 6) -----------------------------------


def test_editing_the_identity_file_changes_no_blocker(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    path = ws / "review" / "round-1" / "identity.json"
    path.write_text(path.read_text().replace("mismatch", "verified").replace(
        "unresolvable", "verified"), encoding="utf-8")
    assert len(service.blockers(project, "demo")) == 2
    assert merge().exit_code == 1


# --- round rules and the stale warning (RED items 7, 8, 9) ------------------------------------


def test_round_is_refused_inside_a_dispatch(proj, capsys, gold_http, monkeypatch):
    project, ws = proj
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    write_bib(ws, MIS[1] + "\n\n")
    code, out, err = vb(capsys, "demo", "--round", "1", "--json")
    assert code == 1
    assert not (ws / "review" / "round-1" / "identity.json").exists()
    assert checks(ws) == []


def test_round_needs_an_existing_review_directory(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    code, out, err = vb(capsys, "demo", "--round", "2", "--json")
    assert code == 1
    assert not (ws / "review" / "round-2").exists()
    assert checks(ws) == []


def test_without_round_nothing_is_written_and_no_gate_opens(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    code, out, err = vb(capsys, "demo", "--json")
    assert code == 4, err
    assert not (ws / "review" / "round-1" / "identity.json").exists()
    assert identity_open(ws) == [] and checks(ws) == []


def test_a_changed_bibliography_is_reported_by_merge_check_without_changing_its_exit(
        proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    write_bib(ws, MIS[1] + "\n\n" + UNR[1] + "\n\n" + VER[1] + "\n")
    res = merge()
    assert res.exit_code == 1
    assert ("warning: references.bib changed since the identity check of round 1; "
            "re-run verify-bib") in res.output
    write_bib(ws, VER[1] + "\n")
    vb(capsys, "demo", "--round", "1", "--json")
    write_bib(ws, VER[1] + "\n\n")
    res = merge()
    assert res.exit_code == 0
    assert "warning: references.bib changed" in res.output


# --- blockers without an evidence round, and the order of entries (RED item 10) --------------


def test_identity_blockers_are_returned_without_an_evidence_round(proj, capsys, gold_http):
    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    assert not (ws / "review" / "round-1" / "matrix.json").exists()
    blockers = service.blockers(project, "demo")
    assert len(blockers) == 1 and blockers[0]["reason"] == "identity-mismatch"
    assert merge().exit_code == 1


def test_the_web_merge_guard_refuses_on_an_identity_blocker(proj, capsys, gold_http):
    from scieflow.core import service as core_service

    project, ws = proj
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    with pytest.raises(core_service.ServiceError, match="merge refused"):
        core_service.merge_round(project, "demo")


@pytest.fixture
def fixture_project(tmp_path, monkeypatch):
    """The review fixture run (round 1 has an evidence blocker K1) in a synthetic project."""
    import shutil

    (tmp_path / "config").mkdir()
    for name in ("agents.yml", "defaults.yml", "scite-prompts.yml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    shutil.copytree(ROOT / "tests" / "fixtures" / "review" / "run", tmp_path / "workspace" / "fixture-run")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path), tmp_path / "workspace" / "fixture-run"


def test_an_evidence_blocker_is_listed_before_the_identity_blockers(fixture_project, capsys, gold_http):
    project, ws = fixture_project
    write_bib(ws, MIS[1] + "\n\n")
    code, out, err = vb(capsys, "fixture-run", "--round", "1", "--json")
    assert code == 4, err
    blockers = service.blockers(project, "fixture-run")
    assert blockers[0]["cluster"] == "K1"
    assert [b["reason"] for b in blockers[1:]] == ["identity-mismatch"]


# --- privacy and the vocabulary (RED items 10-11, plan 5.8) ----------------------------------


def test_events_and_gates_carry_keys_and_dois_but_no_titles(proj, capsys, gold_http):
    project, ws = proj
    title = gold_title(MIS[0])
    write_bib(ws, MIS[1] + "\n\n")
    vb(capsys, "demo", "--round", "1", "--json")
    text = (ws / "events.jsonl").read_text(encoding="utf-8") + json.dumps(identity_open(ws))
    assert title not in text
    assert MIS[0] in text


def test_the_refusal_verdicts_are_the_vendored_refusal_grade():
    ig = importlib.import_module("scieflow.core.review.identity_gates")
    verify = importlib.import_module("scieflow._vendor.researcher_core.verify")
    assert ig.REFUSAL_VERDICTS == verify.REFUSAL_GRADE == frozenset({"mismatch", "unresolvable"})
