"""M-H t4 (plan 5.6, 5.12, Part B t4): dual screening, the screeners' prompts and import, resolved
verdicts, Cohen's kappa. Every corpus record, decision and agent output here is synthetic; no agent
is launched, no network is used, and job records are written through scieflow.core.jobs."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from scieflow._vendor.researcher_core.sr_prisma import derive_prisma_flow
from scieflow.core import events, jobs, service, store
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.core.run import status
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr import ledger, screening
from scieflow.research.sr.errors import SRError

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"
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


def setup(root: Path, n: int = 12, *, locked: bool = True):
    """A corpus of n synthetic records (one committed search round) and, by default, a locked protocol."""
    ws = root / "workspace" / SLUG
    path = ws / "sr" / "rounds" / "1" / "records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record(i), ensure_ascii=False, sort_keys=True) + "\n"
                            for i in range(1, n + 1)), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    events.emit(ws, "sr.committed", "human", op=store.new_id(), kind="search", round=1,
                files={"sr/rounds/1/records.jsonl": digest}, protocol_version=1, protocol_hash="synthetic")
    proj = Project(root)
    if locked:
        gate = lock.lock(proj, ws)
        service.answer_gate(proj, SLUG, gate["id"], "approve")
    return proj, ws


def batch_doc(ws: Path, stage: str, batch: str) -> dict:
    return json.loads((ws / "sr" / "screening" / stage / batch / "batch.json").read_text(encoding="utf-8"))


def write_output(ws: Path, stage: str, batch: str, agent: str, doc: dict) -> None:
    path = ws / "sr" / "screening" / stage / batch / f"{agent}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def fake_job(proj: Project, ws: Path, agent: str, started: str, state: str = "done") -> None:
    """A job record as scieflow.core.jobs writes it; no agent is launched."""
    jid = store.new_id()
    jobs_dir = ws / "jobs"
    jobs.save(jobs.Job(id=jid, kind="agent", argv=["synthetic"], cwd=str(ws), label=agent,
                       run_dir=str(ws), state=state, started=started,
                       log=str(jobs_dir / f"{jid}.log")))


def later(stamp: str, seconds: int = 1) -> str:
    return (datetime.fromisoformat(stamp) + timedelta(seconds=seconds)).isoformat()


def dec(rid: str, decision: str, reason: str = "") -> dict:
    return {"record_id": rid, "decision": decision, "reason": reason}


def screen(proj, ws, *, stage=TA, size=None, decide=lambda agent, rid: ("include", "")):
    """Prepare a batch, write both screeners' outputs and their dispatch evidence, then import."""
    prep = screening.prepare(proj, ws, stage=stage, size=size)
    doc = batch_doc(ws, stage, prep["batch"])
    for agent in AGENTS:
        rows = []
        for rid in doc["record_ids"]:
            decision, reason = decide(agent, rid)
            rows.append(dec(rid, decision, reason))
        write_output(ws, stage, prep["batch"], agent,
                     {"format": 1, "screener": agent, "batch": prep["batch"], "decisions": rows})
        fake_job(proj, ws, agent, later(doc["prepared_at"]))
    return screening.import_batch(proj, ws, stage=stage, batch=prep["batch"]), prep


def decision_events(ws: Path) -> list:
    return [r for r in events.read(ws, types=("sr.screening_decision",))]


def commits(ws: Path) -> list:
    return [r for r in events.read(ws, types=("sr.committed",)) if (r.get("data") or {}).get("kind") == "screening"]


# ---------------------------------------------------------------- prepare


def test_prepare_writes_a_batch_and_two_prompts_that_differ_only_in_the_output_line(sroot):
    proj, ws = setup(sroot)
    prep = screening.prepare(proj, ws, stage=TA, size=5)
    doc = batch_doc(ws, TA, prep["batch"])
    assert doc["record_ids"] == ["R00001", "R00002", "R00003", "R00004", "R00005"]
    assert doc["screeners"] == list(AGENTS)
    assert doc["protocol_version"] == 1 and doc["protocol_hash"]
    a, b = (ws / p for p in prep["prompts"])
    lines_a, lines_b = a.read_text(encoding="utf-8").splitlines(), b.read_text(encoding="utf-8").splitlines()
    assert len(lines_a) == len(lines_b)
    differing = [(x, y) for x, y in zip(lines_a, lines_b) if x != y]
    assert len(differing) == 1
    assert "codex.json" in differing[0][0] and "claude.json" in differing[0][1]


def test_prompt_fences_the_records_and_states_the_locked_criteria(sroot):
    proj, ws = setup(sroot)
    prep = screening.prepare(proj, ws, stage=TA, size=3)
    text = (ws / prep["prompts"][0]).read_text(encoding="utf-8")
    assert "i1" in text and "Synthetic inclusion criterion" in text
    assert "Synthetic exclusion criterion" in text
    assert "Synthetic title 1" in text
    assert "untrusted" in text.lower()
    assert "do not open any file under sr/" in text.lower()


def test_prepare_refuses_an_unlocked_protocol(sroot):
    proj, ws = setup(sroot, locked=False)
    with pytest.raises(SRError, match="lock the protocol first"):
        screening.prepare(proj, ws, stage=TA)


def test_prepare_refuses_while_a_batch_of_the_stage_is_open(sroot):
    proj, ws = setup(sroot)
    screening.prepare(proj, ws, stage=TA, size=4)
    with pytest.raises(SRError, match="prepared but not committed"):
        screening.prepare(proj, ws, stage=TA, size=4)


@pytest.mark.parametrize("names", [["codex"], ["codex", "codex"]])
def test_prepare_refuses_a_one_agent_or_same_agent_assignment(sroot, names):
    proj, ws = setup(sroot)
    (ws / "config.yml").write_text(yaml.safe_dump({"assignments": {"research.sr-screeners": names}}),
                                   encoding="utf-8")
    with pytest.raises(SRError, match="two"):
        screening.prepare(proj, ws, stage=TA, size=3)


def test_same_model_family_is_allowed_with_a_warning(sroot):
    proj, ws = setup(sroot)
    (ws / "config.yml").write_text(yaml.safe_dump({"assignments": {"research.sr-screeners": ["codex", "codex-review"]}}),
                                   encoding="utf-8")
    prep = screening.prepare(proj, ws, stage=TA, size=3)
    assert prep["screeners"] == ["codex", "codex-review"]
    assert any("openai" in w for w in prep["warnings"])


def test_full_text_queue_needs_a_report_that_is_a_regular_file(sroot):
    proj, ws = setup(sroot)

    def decide(agent, rid):
        n = int(rid[1:])
        return ("include", "") if n <= 4 else ("exclude", "e1")

    screen(proj, ws, stage=TA, size=12, decide=decide)
    reports = ws / "sr" / "reports"
    reports.mkdir(parents=True)
    (reports / "R00001.md").write_text("synthetic report one", encoding="utf-8")
    target = ws / "scratch" / "report-two.md"
    target.parent.mkdir(parents=True)
    target.write_text("synthetic report two", encoding="utf-8")
    (reports / "R00002.md").symlink_to(target)
    prep = screening.prepare(proj, ws, stage=FT, size=10)
    assert prep["record_ids"] == ["R00001"]


def test_prioritise_reorders_the_queue_without_dropping_a_record(sroot):
    proj, ws = setup(sroot)
    screen(proj, ws, stage=TA, size=4,
           decide=lambda agent, rid: ("include", "") if rid == "R00001" else ("exclude", "e1"))
    prep = screening.prepare(proj, ws, stage=TA, prioritise=True)
    remaining = [f"R{i:05d}" for i in range(5, 13)]
    assert sorted(prep["record_ids"]) == remaining
    assert len(prep["record_ids"]) == len(set(prep["record_ids"])) == 8


# ---------------------------------------------------------------- import


CASES = {
    "missing-file": "missing",
    "wrong-screener": "screener",
    "wrong-batch": "batch",
    "missing-record": "missing-record",
    "extra-record": "extra-record",
    "duplicate-record": "duplicate",
    "bad-decision": "bad-decision",
    "include-with-reason": "include-reason",
    "unknown-criterion": "unknown-criterion",
    "wrong-format": "format",
    "no-finished-job": "no-job",
    "job-before-prepared": "early-job",
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_import_refusals_write_nothing(sroot, case):
    proj, ws = setup(sroot)
    prep = screening.prepare(proj, ws, stage=TA, size=4)
    doc = batch_doc(ws, TA, prep["batch"])
    rows = [dec(rid, "include" if i % 2 else "exclude", "" if i % 2 else "e1")
            for i, rid in enumerate(doc["record_ids"])]
    good = {"format": 1, "screener": "codex", "batch": prep["batch"], "decisions": list(rows)}
    if case == "wrong-screener":
        good["screener"] = "claude"
    elif case == "wrong-batch":
        good["batch"] = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    elif case == "missing-record":
        good["decisions"] = rows[:-1]
    elif case == "extra-record":
        good["decisions"] = rows + [dec("R00099", "include")]
    elif case == "duplicate-record":
        good["decisions"] = rows + [rows[0]]
    elif case == "bad-decision":
        good["decisions"][0] = dec(rows[0]["record_id"], "maybe")
    elif case == "include-with-reason":
        good["decisions"][0] = dec(rows[0]["record_id"], "include", "e1")
    elif case == "unknown-criterion":
        good["decisions"][1] = dec(rows[1]["record_id"], "exclude", "zz9")
    elif case == "wrong-format":
        good["format"] = 2
    write_output(ws, TA, prep["batch"], "codex", good)
    write_output(ws, TA, prep["batch"], "claude",
                 {"format": 1, "screener": "claude", "batch": prep["batch"], "decisions": rows})
    if case == "missing-file":
        (ws / "sr" / "screening" / TA / prep["batch"] / "claude.json").unlink()
    prepared = doc["prepared_at"]
    if case == "no-finished-job":
        fake_job(proj, ws, "claude", later(prepared))
        fake_job(proj, ws, "codex", later(prepared), state="running")
    elif case == "job-before-prepared":
        fake_job(proj, ws, "codex", later(prepared, seconds=-1))
        fake_job(proj, ws, "claude", later(prepared))
    else:
        fake_job(proj, ws, "codex", later(prepared))
        fake_job(proj, ws, "claude", later(prepared))
    before = len(decision_events(ws))
    with pytest.raises(SRError):
        screening.import_batch(proj, ws, stage=TA, batch=prep["batch"])
    assert len(decision_events(ws)) == before
    assert commits(ws) == []


def test_a_valid_pair_writes_2n_decisions_and_one_commit(sroot):
    proj, ws = setup(sroot)
    result, prep = screen(proj, ws, stage=TA, size=4,
                          decide=lambda agent, rid: ("include", "") if rid in ("R00001", "R00002")
                          else ("exclude", "e1"))
    assert result["decisions"] == 8
    assert len(decision_events(ws)) == 8
    assert len(commits(ws)) == 1
    data = commits(ws)[0]["data"]
    assert data["batch"] == prep["batch"] and data["stage"] == TA
    assert set(data["jobs"]) == set(AGENTS)


def test_reimport_of_a_committed_batch_is_refused(sroot):
    proj, ws = setup(sroot)
    _result, prep = screen(proj, ws, stage=TA, size=3)
    with pytest.raises(SRError, match="already committed"):
        screening.import_batch(proj, ws, stage=TA, batch=prep["batch"])


def test_an_interrupted_import_is_ignored_and_a_new_import_succeeds(sroot):
    proj, ws = setup(sroot)
    prep = screening.prepare(proj, ws, stage=TA, size=3)
    doc = batch_doc(ws, TA, prep["batch"])
    rows = [dec(rid, "include") for rid in doc["record_ids"]]
    for agent in AGENTS:
        write_output(ws, TA, prep["batch"], agent,
                     {"format": 1, "screener": agent, "batch": prep["batch"], "decisions": rows})
        fake_job(proj, ws, agent, later(doc["prepared_at"]))
    # A crash after the detail events and before the commit: the events exist but do not count.
    led = ledger.EventsLedger(ws, actor="human", op=store.new_id(), extra={"batch": prep["batch"],
                                                                         "protocol_version": 1})
    from scieflow._vendor.researcher_core.screen import record_screening_decision
    record_screening_decision(led, led.run_context(), screener_id="codex", record_id="R00001",
                              stage=TA, decision="exclude", ts="2026-11-03T00:00:00+00:00", reason="e1")
    assert screening.resolved(ws, TA) == {}
    result = screening.import_batch(proj, ws, stage=TA, batch=prep["batch"])
    assert result["decisions"] == 6
    assert screening.resolved(ws, TA)["R00001"] == "include"


def test_resolved_counts_equal_the_vendored_flow_stage_summary(sroot):
    proj, ws = setup(sroot)

    def decide(agent, rid):
        n = int(rid[1:])
        if n <= 6 or n == 12:
            return "include", ""
        if n <= 9:
            return "exclude", "e1"
        if n == 10:
            return ("include", "") if agent == "codex" else ("exclude", "e2")
        return ("exclude", "e2") if agent == "codex" else ("include", "")

    screen(proj, ws, stage=TA, size=12, decide=decide)
    verdicts = screening.resolved(ws, TA)
    counts = {v: list(verdicts.values()).count(v) for v in ("include", "exclude", "pending")}
    assert counts == {"include": 7, "exclude": 3, "pending": 2}
    flow = derive_prisma_flow(ledger.view(ws).iter_events(), run_id=SLUG)
    stage = next(s for s in flow.stages if s.stage == TA)
    assert (stage.screened, stage.included, stage.excluded, stage.pending) == (12, 7, 3, 2)


# ---------------------------------------------------------------- kappa


KAPPA_ROWS = [
    # (records, a include/b include, a include/b exclude, a exclude/b include, both exclude, expected kappa)
    (48, 10, 5, 3, 30, 95 / 159),
    (13, 5, 1, 1, 6, 58 / 84),
]


@pytest.mark.parametrize("n,a,b,c,d,expected", KAPPA_ROWS)
def test_kappa_reproduces_the_plan_tables_from_imported_decisions(sroot, n, a, b, c, d, expected):
    proj, ws = setup(sroot, n)

    def decide(agent, rid):
        i = int(rid[1:])
        a_inc = i <= a + b
        b_inc = i <= a or (a + b < i <= a + b + c)
        if agent == "codex":
            return ("include", "") if a_inc else ("exclude", "e1")
        return ("include", "") if b_inc else ("exclude", "e1")

    screen(proj, ws, stage=TA, size=n, decide=decide)
    result = screening.kappa(ws, TA)
    assert result["n"] == n
    # The vendored table is ordered by sorted screener id: rows are claude, columns are codex.
    assert result["screener_ids"] == ["claude", "codex"]
    assert result["table"]["include"]["include"] == a
    assert result["table"]["include"]["exclude"] == c   # claude include, codex exclude
    assert result["table"]["exclude"]["include"] == b   # claude exclude, codex include
    assert result["table"]["exclude"]["exclude"] == d
    assert abs(result["kappa"] - expected) < 1e-9


def test_kappa_is_undefined_when_both_screeners_use_one_category(sroot):
    proj, ws = setup(sroot, 10)
    screen(proj, ws, stage=TA, size=10, decide=lambda agent, rid: ("include", ""))
    result = screening.kappa(ws, TA)
    assert result["kappa"] is None
    assert "undefined" in result["note"]


# ---------------------------------------------------------------- CLI


@pytest.fixture
def cli_root(sroot, monkeypatch):
    monkeypatch.setattr(sr_cli, "_root", lambda: sroot)
    return sroot


def test_cli_prepare_status_and_kappa(cli_root):
    proj, ws = setup(cli_root, 6)
    runner = CliRunner()
    prep = runner.invoke(sr_cli.sr, ["screen", "prepare", SLUG, "--stage", TA, "--size", "3", "--json"])
    assert prep.exit_code == 0, prep.output
    doc = json.loads(prep.output)
    assert doc["record_ids"] == ["R00001", "R00002", "R00003"]
    status_run = runner.invoke(sr_cli.sr, ["screen", "status", SLUG, "--json"])
    assert status_run.exit_code == 0, status_run.output
    kap = runner.invoke(sr_cli.sr, ["kappa", SLUG, "--stage", TA])
    assert kap.exit_code == 0, kap.output
    assert "undefined" in kap.output


def test_cli_refuses_with_exit_1_on_an_unlocked_protocol(cli_root):
    setup(cli_root, 3, locked=False)
    result = CliRunner().invoke(sr_cli.sr, ["screen", "prepare", SLUG, "--stage", TA])
    assert result.exit_code == 1
    assert "lock the protocol first" in result.output
