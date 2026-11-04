"""M-H t5 (plan 5.7, 5.6 last paragraph, 5.16 row 1-2): blinded conflicts and the adjudication gate.

Synthetic data only; no network, no agent. Screening decisions are written through the same vendored
writer the import uses, and dispatch evidence is a job record (no agent is launched).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from scieflow._vendor.researcher_core import screen as vscreen
from scieflow.core import events, gates, jobs, service, store
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.research.sr import adjudication, ledger, protocol, screening, verify
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr.errors import SRError

REPO = Path(__file__).resolve().parents[3]
SLUG = "r1"
TA, FT = "title-abstract", "full-text"
AGENTS = ("codex", "claude")


def record(n: int) -> dict:
    return {"id": f"R{n:05d}", "round": 1, "hits": [f"1/s1/openalex:{n}"], "doi": f"10.9/syn{n:03d}",
            "title": f"Synthetic title {n}", "year": 2020, "venue": "Synthetic Journal",
            "abstract": f"Synthetic abstract {n}.", "url": None, "sources": ["openalex"]}


@pytest.fixture
def sroot(srroot):
    (srroot / "config").mkdir(exist_ok=True)
    for name in ("agents.yml", "defaults.yml"):
        shutil.copy(REPO / "config" / name, srroot / "config" / name)
    return srroot


def setup(root: Path, n: int = 12):
    """A corpus of n synthetic records (one committed search round) and a locked protocol."""
    ws = root / "workspace" / SLUG
    path = ws / "sr" / "rounds" / "1" / "records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record(i), ensure_ascii=False, sort_keys=True) + "\n"
                            for i in range(1, n + 1)), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    proj = Project(root)
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, SLUG, gate["id"], "approve")
    # t6: the commit names the in-force lock hash (sr verify refuses a hash that matches no lock record).
    events.emit(ws, "sr.committed", "human", op=store.new_id(), kind="search", round=1,
                files={"sr/rounds/1/records.jsonl": digest}, protocol_version=1,
                protocol_hash=lock.state(proj, ws).in_force_hash)
    return proj, ws


def batch_doc(ws: Path, stage: str, batch: str) -> dict:
    return json.loads((ws / "sr" / "screening" / stage / batch / "batch.json").read_text(encoding="utf-8"))


def write_output(ws: Path, stage: str, batch: str, agent: str, doc: dict) -> None:
    path = ws / "sr" / "screening" / stage / batch / f"{agent}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def fake_job(proj: Project, ws: Path, agent: str, started: str) -> None:
    jid = store.new_id()
    jobs.save(jobs.Job(id=jid, kind="agent", argv=["synthetic"], cwd=str(ws), label=agent,
                       run_dir=str(ws), state="done", started=started, finished=started,
                       log=str(ws / "jobs" / f"{jid}.log")))


def later(stamp: str, seconds: int = 1) -> str:
    return (datetime.fromisoformat(stamp) + timedelta(seconds=seconds)).isoformat()


def dec(rid: str, decision: str, reason: str = "") -> dict:
    return {"record_id": rid, "decision": decision, "reason": reason}


def screen(proj, ws, *, stage=TA, size=None, decide):
    """Prepare a batch, write both screeners' outputs and their dispatch evidence, then import."""
    prep = screening.prepare(proj, ws, stage=stage, size=size)
    doc = batch_doc(ws, stage, prep["batch"])
    for agent in AGENTS:
        rows = [dec(rid, *decide(agent, rid)) for rid in doc["record_ids"]]
        write_output(ws, stage, prep["batch"], agent,
                     {"format": 1, "screener": agent, "batch": prep["batch"], "decisions": rows})
        fake_job(proj, ws, agent, later(doc["prepared_at"]))
    return screening.import_batch(proj, ws, stage=stage, batch=prep["batch"]), prep


def decide_5_12(agent: str, rid: str) -> tuple[str, str]:
    """The plan 5.12 title-abstract scenario: 10 agreed include, 30 agreed exclude, 8 conflicts (R41-R48)."""
    n = int(rid[1:])
    if n <= 10:
        return ("include", "")
    if n <= 40:
        return ("exclude", "e1")
    if n <= 45:
        return ("include", "") if agent == "codex" else ("exclude", "e1")
    return ("exclude", "e1") if agent == "codex" else ("include", "")


CONFLICT_IDS = [f"R{n:05d}" for n in range(41, 49)]
# A complete, valid set of human answers for the eight conflicts of plan 5.12.
ANSWERS = {"R00041": ("include", ""), "R00042": ("exclude", "e1"), "R00043": ("include", ""),
           "R00044": ("exclude", "e1"), "R00045": ("include", ""), "R00046": ("exclude", "e1"),
           "R00047": ("include", ""), "R00048": ("exclude", "e1")}


def scenario(root: Path):
    proj, ws = setup(root, 48)
    screen(proj, ws, stage=TA, size=48, decide=decide_5_12)
    return proj, ws


def open_gate(proj, ws, as_agent: bool = False) -> dict:
    return adjudication.open_conflicts(proj, ws, stage=TA, as_agent=as_agent)


def resolve(proj, ws, gid: str, answers: dict = ANSWERS):
    for rid, (decision, reason) in answers.items():
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale=f"synthetic rationale for {rid}")
    return gates.answer(proj, ws, gid, "resolved", actor="human")


def adjudication_events(ws: Path) -> list:
    return events.read(ws, types=("sr.adjudication",))


def commits(ws: Path, kind: str) -> list:
    return [r for r in events.read(ws, types=("sr.committed",)) if (r.get("data") or {}).get("kind") == kind]


def criteria_texts(proj, ws) -> list[str]:
    locked = protocol.require_locked(proj, ws)
    return [str(c.get("text")) for c in list(locked.criteria_include) + list(locked.criteria_exclude)]


# ---------------------------------------------------------------- the gate kind (schema and hook)


def test_the_sr_adjudication_kind_is_schema_data_and_needs_a_human(sroot):
    proj, _ws = setup(sroot, 4)
    table = gates.kinds(proj)
    assert table["sr-adjudication"]["requires_human"] is True
    assert table["sr-adjudication"]["help"].strip()


# ---------------------------------------------------------------- sr conflicts (blinded)


def test_conflicts_are_blinded_and_one_gate_opens(sroot):
    proj, ws = scenario(sroot)
    doc = open_gate(proj, ws)
    assert doc["conflicts"] == 8
    gid = doc["gate"]
    folder = ws / "sr" / "adjudication" / TA / gid
    assert folder.name == gid
    conflicts_path, md_path = folder / "conflicts.json", folder / "conflicts.md"
    assert conflicts_path.is_file() and md_path.is_file()
    gate = gates.get(ws, gid)
    assert gate["kind"] == "sr-adjudication" and gate["state"] == "open"
    assert gate["options"] == ["resolved", "reject"]
    ids = sorted(c["record_id"] for c in json.loads(conflicts_path.read_text())["conflicts"])
    assert ids == CONFLICT_IDS
    md = md_path.read_text(encoding="utf-8")
    for name in AGENTS:
        assert name not in md and name not in gate["question"] and name not in conflicts_path.read_text()
    digest = hashlib.sha256(conflicts_path.read_bytes()).hexdigest()
    assert f"sha256 {digest[:12]}" in gate["question"]
    assert "blinded title-abstract conflicts" in gate["question"]
    # Outside the locked criteria text, the blinded file never says include or exclude (no votes, no reasons).
    texts = criteria_texts(proj, ws)
    body = "\n".join(line for line in md.splitlines() if not any(t and t in line for t in texts))
    assert "include" not in body.lower() and "exclude" not in body.lower()
    # Structure of the machine file: only the record fields, the profile and the linkage ids; no votes.
    doc = json.loads(conflicts_path.read_text(encoding="utf-8"))
    for row in doc["conflicts"]:
        assert set(row) == {"record_id", "stage", "record", "eligibility_profile", "decision_event_ids"}
        assert set(row["record"]) == {"id", "title", "year", "venue", "doi", "abstract"}


def test_a_second_call_refuses_while_the_gate_is_open(sroot):
    proj, ws = scenario(sroot)
    open_gate(proj, ws)
    with pytest.raises(SRError, match="open"):
        open_gate(proj, ws)


def test_no_conflicts_prints_none_and_opens_no_gate(sroot):
    proj, ws = setup(sroot, 12)
    screen(proj, ws, stage=TA, size=12, decide=lambda agent, rid: ("include", ""))
    doc = open_gate(proj, ws)
    assert doc["conflicts"] == 0 and doc["gate"] is None
    assert gates.list_gates(ws, "open") == []


# ---------------------------------------------------------------- sr adjudicate (human only, validated)


def test_adjudicate_is_refused_in_a_dispatch(sroot, monkeypatch):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    with pytest.raises(SRError, match="refused"):
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id="R00041", decision="include",
                                reason="", rationale="synthetic")
    assert not (ws / "sr" / "adjudication" / TA / gid / "decisions.json").exists()


@pytest.mark.parametrize("case", [
    ("R00001", "include", "", "synthetic"),        # not a conflict
    ("R00041", "include", "e1", "synthetic"),      # an include carries no reason
    ("R00042", "exclude", "zz9", "synthetic"),     # not an exclude criterion of the locked protocol
    ("R00041", "include", "", "   "),              # empty rationale
    ("R00041", "include", "", "x" * 2001),         # rationale longer than 2000 characters
])
def test_adjudicate_refusals_write_nothing(sroot, case):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    rid, decision, reason, rationale = case
    with pytest.raises(SRError):
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale=rationale)
    assert not (ws / "sr" / "adjudication" / TA / gid / "decisions.json").exists()


def test_adjudicate_writes_decisions_and_counts_what_remains(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    first = adjudication.adjudicate(proj, ws, gate_id=gid, record_id="R00041", decision="include",
                                    reason="", rationale="synthetic")
    assert first["remaining"] == 7
    again = adjudication.adjudicate(proj, ws, gate_id=gid, record_id="R00041", decision="exclude",
                                    reason="e1", rationale="changed my mind")
    assert again["remaining"] == 7
    doc = json.loads((ws / "sr" / "adjudication" / TA / gid / "decisions.json").read_text())
    assert [(d["record_id"], d["decision"], d["reason"]) for d in doc["decisions"]] == [("R00041", "exclude", "e1")]


# ---------------------------------------------------------------- the answer hook (plan 5.7)


def test_an_agent_answer_is_refused_and_the_gate_stays_open(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    for rid, (decision, reason) in ANSWERS.items():
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale="synthetic")
    with pytest.raises(gates.GateError):
        gates.answer(proj, ws, gid, "resolved", actor="agent", rationale="synthetic")
    with pytest.raises(gates.GateError):
        adjudication.on_answer(proj, ws, gates.get(ws, gid), "resolved", "agent")
    assert gates.get(ws, gid)["state"] == "open"
    assert adjudication_events(ws) == []


def test_resolved_with_a_missing_decision_is_refused(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    partial = {k: v for k, v in ANSWERS.items() if k != "R00048"}
    with pytest.raises(gates.GateError, match="no decision"):
        resolve(proj, ws, gid, partial)
    assert gates.get(ws, gid)["state"] == "open"
    assert adjudication_events(ws) == []


def test_a_changed_screening_is_refused_and_the_gate_stays_open(sroot, monkeypatch):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    for rid, (decision, reason) in ANSWERS.items():
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale="synthetic")
    # A later codex decision on R00041 (a new screening event) changes the conflict's decision ids.
    led = ledger.EventsLedger(ws, actor="agent", op=store.new_id(), extra={"batch": "synthetic",
                                                                          "protocol_version": 1})
    vscreen.record_screening_decision(led, led.run_context(), screener_id="codex", record_id="R00041",
                                      stage=TA, decision="exclude", ts=screening._now(), reason="e1")
    led.commit("screening", stage=TA, batch="synthetic", files={}, jobs={}, protocol_hash="synthetic",
               decisions=1)
    # The injected commit is not a valid screening operation, so sr verify would refuse first. Hold verify
    # neutral for this test only, so the hook's own changed-screening check is the branch that refuses.
    monkeypatch.setattr(verify, "preflight", lambda project, root: {})
    with pytest.raises(gates.GateError, match="screening changed"):
        gates.answer(proj, ws, gid, "resolved", actor="human")
    assert gates.get(ws, gid)["state"] == "open"
    assert adjudication_events(ws) == []


def test_resolved_complete_records_eight_events_one_commit_and_answers_the_gate(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    gate = resolve(proj, ws, gid)
    assert gate["state"] == "answered" and gate["answer"] == "resolved"
    rows = adjudication_events(ws)
    assert len(rows) == 8
    assert {row["actor"] for row in rows} == {"human"}
    assert all(len(row["data"]["resolves"]) == 2 for row in rows)
    assert all(row["data"]["gate"] == gid for row in rows)
    by_rid = {row["data"]["record_id"]: row["data"] for row in rows}
    assert by_rid["R00042"]["reason"] == "e1" and by_rid["R00041"]["reason"] == ""
    adjudicated = commits(ws, "adjudication")
    assert len(adjudicated) == 1 and adjudicated[0]["data"]["gate"] == gid
    verdicts = screening.resolved(ws, TA, list(AGENTS))
    assert {rid: verdicts[rid] for rid in CONFLICT_IDS} == {rid: d for rid, (d, _r) in ANSWERS.items()}
    assert open_gate(proj, ws)["conflicts"] == 0


def test_repeating_the_hook_after_a_crash_records_nothing_new(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    gate = resolve(proj, ws, gid)
    before = (len(adjudication_events(ws)), len(commits(ws, "adjudication")))
    reopened = dict(gate, state="open")
    adjudication.on_answer(proj, ws, reopened, "resolved", "human")
    assert (len(adjudication_events(ws)), len(commits(ws, "adjudication"))) == before


def test_reject_records_nothing_and_keeps_the_files_for_audit(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    gate = gates.answer(proj, ws, gid, "reject", actor="human")
    assert gate["state"] == "answered" and gate["answer"] == "reject"
    assert adjudication_events(ws) == [] and commits(ws, "adjudication") == []
    assert (ws / "sr" / "adjudication" / TA / gid / "conflicts.json").is_file()


def test_the_hook_import_failing_becomes_a_gate_error_and_the_gate_stays_open(sroot, monkeypatch):
    import scieflow.research.sr as sr_package

    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    # `from pkg import mod` reads the package attribute first: remove it and block the module import.
    monkeypatch.delattr(sr_package, "adjudication", raising=False)
    monkeypatch.setitem(sys.modules, "scieflow.research.sr.adjudication", None)
    with pytest.raises(gates.GateError, match="research extra"):
        gates.answer(proj, ws, gid, "resolved", actor="human")
    assert gates.get(ws, gid)["state"] == "open"


# ---------------------------------------------------------------- full-text gate (plan 5.6, t4 deviation)


def test_full_text_prepare_refuses_while_a_conflict_is_unadjudicated_then_works(sroot):
    proj, ws = scenario(sroot)
    reports = ws / "sr" / "reports"
    reports.mkdir(parents=True)
    for n in range(1, 4):
        (reports / f"R{n:05d}.md").write_text(f"synthetic report {n}", encoding="utf-8")
    gid = open_gate(proj, ws)["gate"]
    with pytest.raises(SRError, match="adjudication"):
        screening.prepare(proj, ws, stage=FT)
    resolve(proj, ws, gid)
    prep = screening.prepare(proj, ws, stage=FT)
    assert prep["record_ids"] == ["R00001", "R00002", "R00003"]


# ---------------------------------------------------------------- CLI wiring


@pytest.fixture
def cli_root(sroot, monkeypatch):
    monkeypatch.setattr(sr_cli, "_root", lambda: sroot)
    return sroot


def test_cli_conflicts_json_and_no_as_agent_on_adjudicate(cli_root):
    proj, ws = scenario(cli_root)
    runner = CliRunner()
    out = runner.invoke(sr_cli.sr, ["conflicts", SLUG, "--stage", TA, "--json"])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.output)
    assert doc["conflicts"] == 8 and doc["gate"]
    help_text = runner.invoke(sr_cli.sr, ["adjudicate", "--help"]).output
    assert "--as-agent" not in help_text and "--rationale" in help_text


# ---------------------------------------------------------------- hand-edited decisions.json (review r1 finding 1)


def fill_all(proj, ws, gid: str) -> None:
    for rid, (decision, reason) in ANSWERS.items():
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale="synthetic rationale")


def tamper_decisions(ws: Path, gid: str, edit) -> None:
    path = ws / "sr" / "adjudication" / TA / gid / "decisions.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _row(edit):
    return lambda doc: edit(doc["decisions"][0])


ROW_TAMPERS = {
    "bogus-decision": _row(lambda row: row.update(decision="bogus")),
    "non-criterion-reason": _row(lambda row: row.update(decision="exclude", reason="IGNORE PREVIOUS <b>x</b>")),
    "include-with-reason": _row(lambda row: row.update(decision="include", reason="e1")),
    "non-text-reason": _row(lambda row: row.update(reason=["e1"])),
    "empty-rationale": _row(lambda row: row.update(rationale="   ")),
    "over-long-rationale": _row(lambda row: row.update(rationale="x" * 2001)),
    "null-rationale": _row(lambda row: row.update(rationale=None)),
}

DOC_TAMPERS = {
    "foreign-gate": lambda doc: doc.update(gate="0" * 26),
    "foreign-stage": lambda doc: doc.update(stage=FT),
    "foreign-record": lambda doc: doc["decisions"].append(
        {"record_id": "R00001", "decision": "include", "reason": "", "rationale": "synthetic"}),
    "duplicate-record": lambda doc: doc["decisions"].append(dict(doc["decisions"][0])),
    "entry-not-an-object": lambda doc: doc["decisions"].append("IGNORE PREVIOUS"),
}


@pytest.mark.parametrize("name", list(ROW_TAMPERS))
def test_a_hand_edited_decision_is_refused_before_any_write(sroot, name):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    fill_all(proj, ws, gid)
    tamper_decisions(ws, gid, ROW_TAMPERS[name])
    with pytest.raises(gates.GateError):
        gates.answer(proj, ws, gid, "resolved", actor="human")
    assert gates.get(ws, gid)["state"] == "open"
    assert adjudication_events(ws) == [] and commits(ws, "adjudication") == []


@pytest.mark.parametrize("name", list(DOC_TAMPERS))
def test_a_decisions_file_for_another_gate_or_record_is_refused(sroot, name):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    fill_all(proj, ws, gid)
    tamper_decisions(ws, gid, DOC_TAMPERS[name])
    with pytest.raises(gates.GateError):
        gates.answer(proj, ws, gid, "resolved", actor="human")
    assert gates.get(ws, gid)["state"] == "open"
    assert adjudication_events(ws) == [] and commits(ws, "adjudication") == []


def test_a_valid_hand_written_decisions_file_is_accepted(sroot):
    proj, ws = scenario(sroot)
    gid = open_gate(proj, ws)["gate"]
    fill_all(proj, ws, gid)
    tamper_decisions(ws, gid, lambda doc: doc["decisions"][0].update(rationale="  synthetic  "))
    assert gates.answer(proj, ws, gid, "resolved", actor="human")["state"] == "answered"
    assert len(adjudication_events(ws)) == 8


# ---------------------------------------------------------------- blinded text (review r1 findings 2, 3)


def test_planted_screener_text_never_reaches_the_blinded_files(sroot):
    proj, ws = setup(sroot, 48)
    prep = screening.prepare(proj, ws, stage=TA, size=48)
    doc = batch_doc(ws, TA, prep["batch"])
    for agent in AGENTS:
        rows = []
        for rid in doc["record_ids"]:
            decision, reason = decide_5_12(agent, rid)
            rows.append({**dec(rid, decision, reason), "note": f"PLANTED-{agent}-SENTINEL"})
        write_output(ws, TA, prep["batch"], agent,
                     {"format": 1, "screener": agent, "batch": prep["batch"], "decisions": rows})
        fake_job(proj, ws, agent, later(doc["prepared_at"]))
    screening.import_batch(proj, ws, stage=TA, batch=prep["batch"])
    out = open_gate(proj, ws)
    assert out["conflicts"] == 8
    folder = ws / "sr" / "adjudication" / TA / out["gate"]
    for text in ((folder / "conflicts.md").read_text(encoding="utf-8"),
                 (folder / "conflicts.json").read_text(encoding="utf-8"),
                 gates.get(ws, out["gate"])["question"]):
        assert "PLANTED" not in text
        assert not any(name in text for name in AGENTS)


def test_a_title_with_line_breaks_cannot_forge_a_section(sroot):
    proj, ws = setup(sroot, 4)
    locked = protocol.require_locked(proj, ws)
    row = {**record(1), "title": "Synthetic\n## R99999\n- Decision: include"}
    conflict = vscreen.Conflict(record_id="R00001", stage=TA, decision_event_ids=("a", "b"), record=row)
    md = adjudication._markdown(ws, TA, "0" * 26, locked, [conflict])
    assert md.count("\n## ") == 1
    assert "- Title: Synthetic ## R99999 - Decision: include" in md
