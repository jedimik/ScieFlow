"""M-H t6 shared scenario (plan 5.12): the round-1 and round-2 search, the title-abstract and full-text
screening and the human adjudications, all run through the real writers and the real gate answer.

Every source is a fake module (synthetic bibliographic records, no network, no agent). Dispatch evidence
is a job record (no agent is launched). Synthetic data only; no patient data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import types
from pathlib import Path

import yaml

from scieflow.core import gates, jobs, service, store
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.core.run import status
from scieflow.research.lib import papers
from scieflow.research.search import fanout, registry
from scieflow.research.sr import adjudication, screening, search

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"
SLUG = "r1"
TA, FT = "title-abstract", "full-text"
AGENTS = ("codex", "claude")


def _title(key: int) -> str:
    a = ["amber", "basalt", "cobalt", "delta", "ember", "fjord", "granite", "harbor", "island", "jasper",
         "kelp", "lagoon", "meadow", "nickel", "orchid", "pebble", "quartz", "river", "saffron", "tundra",
         "umber", "valley", "willow", "xenon", "yarrow", "zephyr", "acorn", "birch", "cedar", "dune",
         "elm", "fern", "gorge", "heron", "iris", "juniper", "krill", "lichen", "maple", "nettle", "oak",
         "pine", "quail", "reef", "sable", "thistle", "upland", "vine", "walnut", "yew", "zinnia",
         "anchor", "beacon", "canyon", "drift", "eddy", "flint", "glacier", "hollow", "inlet", "jetty"]
    b = [a[(i * 7) % 60] for i in range(60)]
    return f"{a[key]} {b[key]} cohort"


def _doi(prefix: str, n: int) -> str:
    return f"10.9/{prefix}{n:02d}"


def _paper(doi: str, key: int, year: int = 2020) -> dict:
    return papers.record(doi=doi, title=_title(key), year=year, venue="Synthetic Journal",
                         abstract=f"Synthetic abstract {doi}.", url=f"https://x.test/{doi}")


OPENALEX_1 = [_paper(_doi("o", i), i - 1) for i in range(1, 31)]
EUROPEPMC_1 = ([_paper(_doi("o", i), i - 1) for i in range(1, 9)]
               + [_paper(_doi("e", j), 29 + j) for j in range(1, 13)])
CROSSREF_1 = ([_paper(_doi("e", j), 29 + j) for j in range(1, 5)]
              + [_paper(_doi("c", k), 41 + k) for k in range(1, 7)])
ROUND_1 = {"openalex": OPENALEX_1, "europepmc": EUROPEPMC_1, "crossref": CROSSREF_1}
ROUND_2 = {"openalex": [_paper(_doi("o", 1), 0), _paper(_doi("o", 2), 1),
                        _paper(_doi("n", 1), 48), _paper(_doi("n", 2), 49), _paper(_doi("n", 3), 50)]}


class FakeGW:
    class policy:
        @staticmethod
        def allows(name):
            return True

        @staticmethod
        def reason(name):
            return None


def install_sources(monkeypatch):
    """Route every search source to a fake module whose answers the test sets on the returned box."""
    box = types.SimpleNamespace(answers={}, calls=[], fail={})

    def make(name):
        def search_fn(gw, checked, *, limit=25, from_year=None):
            box.calls.append({"source": name, "limit": limit, "from_year": from_year})
            return [dict(r) for r in box.answers.get(name, [])][:limit]
        return types.SimpleNamespace(search=search_fn)

    for name in fanout.ORDER:
        monkeypatch.setitem(fanout.MODULES, name, make(name))
    monkeypatch.setattr(registry, "gateway", lambda *a, **k: FakeGW())
    return box


def make_root(base: Path) -> Path:
    """A temporary project: schemas, reporting checklists, agent config, and run r1 with the SR design."""
    shutil.copytree(REPO / "schemas", base / "schemas")
    shutil.copytree(REPO / "config" / "reporting", base / "config" / "reporting")
    (base / "config").mkdir(exist_ok=True)
    for name in ("agents.yml", "defaults.yml"):
        shutil.copy(REPO / "config" / name, base / "config" / name)
    ws = base / "workspace" / SLUG
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status(SLUG, "autonomous"))
    (ws / "design").mkdir()
    doc = yaml.safe_load((FIX / "systematic-review.yml").read_text(encoding="utf-8"))
    doc["review"]["strategies"][0]["limit"] = 200
    doc["review"]["strategies"].append({"id": "s2", "query": "synthetic protocol query",
                                        "sources": ["crossref"], "limit": 200})
    doc["review"]["living"] = True
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return base


def lock_protocol(root: Path):
    proj = Project(root)
    ws = root / "workspace" / SLUG
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, SLUG, gate["id"], "approve")
    return proj, ws


def batch_doc(ws: Path, stage: str, batch: str) -> dict:
    return json.loads((ws / "sr" / "screening" / stage / batch / "batch.json").read_text(encoding="utf-8"))


def fake_job(proj: Project, ws: Path, agent: str, started: str) -> None:
    jid = store.new_id()
    jobs.save(jobs.Job(id=jid, kind="agent", argv=["synthetic"], cwd=str(ws), label=agent,
                       run_dir=str(ws), state="done", started=started, log=str(ws / "jobs" / f"{jid}.log")))


def screen(proj, ws, *, stage: str, size: int | None, decide):
    """Prepare one batch, write both screeners' outputs and their dispatch evidence, import them."""
    from datetime import datetime, timedelta

    prep = screening.prepare(proj, ws, stage=stage, size=size)
    doc = batch_doc(ws, stage, prep["batch"])
    stamp = datetime.fromisoformat(doc["prepared_at"]) + timedelta(seconds=1)
    for agent in AGENTS:
        rows = [{"record_id": rid, "decision": decide(agent, rid)[0], "reason": decide(agent, rid)[1]}
                for rid in doc["record_ids"]]
        path = ws / "sr" / "screening" / stage / prep["batch"] / f"{agent}.json"
        path.write_text(json.dumps({"format": 1, "screener": agent, "batch": prep["batch"], "decisions": rows},
                                   ensure_ascii=False), encoding="utf-8")
        fake_job(proj, ws, agent, stamp.isoformat())
    return screening.import_batch(proj, ws, stage=stage, batch=prep["batch"])


def decide_title_abstract(agent: str, rid: str) -> tuple[str, str]:
    """Plan 5.12 round one, title-abstract: 10 agreed include, 30 agreed exclude, 8 conflicts (R41-R48)."""
    n = int(rid[1:])
    if n <= 10:
        return ("include", "")
    if n <= 40:
        return ("exclude", "e1")
    if n <= 45:
        return ("include", "") if agent == "codex" else ("exclude", "e1")
    return ("exclude", "e1") if agent == "codex" else ("include", "")


def decide_full_text(agent: str, rid: str) -> tuple[str, str]:
    """Plan 5.12 full-text: 5 agreed include, 6 agreed exclude (e1 x4, e2 x2), 2 conflicts (R45, R47)."""
    if rid in ("R00002", "R00003", "R00004", "R00005", "R00006"):
        return ("include", "")
    if rid in ("R00007", "R00008", "R00009", "R00010"):
        return ("exclude", "e1")
    if rid in ("R00041", "R00043"):
        return ("exclude", "e2")
    if rid == "R00045":
        return ("include", "") if agent == "codex" else ("exclude", "e2")
    if rid == "R00047":
        return ("exclude", "e1") if agent == "codex" else ("include", "")
    raise AssertionError(f"record outside the full-text queue: {rid}")


TA_ANSWERS = {"R00041": ("include", ""), "R00042": ("exclude", "e1"), "R00043": ("include", ""),
              "R00044": ("exclude", "e1"), "R00045": ("include", ""), "R00046": ("exclude", "e1"),
              "R00047": ("include", ""), "R00048": ("exclude", "e1")}
FT_ANSWERS = {"R00045": ("include", ""), "R00047": ("exclude", "e2")}


def resolve(proj, ws, *, stage: str, answers: dict) -> str:
    """Open the stage's blinded conflicts, answer every one as the human, then resolve the gate."""
    opened = adjudication.open_conflicts(proj, ws, stage=stage)
    gid = opened["gate"]
    for rid, (decision, reason) in answers.items():
        adjudication.adjudicate(proj, ws, gate_id=gid, record_id=rid, decision=decision, reason=reason,
                                rationale=f"synthetic rationale for {rid}")
    gates.answer(proj, ws, gid, "resolved", actor="human")
    return gid


def scenario(base: Path, monkeypatch, *, round2: bool = False):
    """Run the plan 5.12 pipeline on a fresh project. Returns (project, workspace, source box)."""
    root = make_root(base)
    proj, ws = lock_protocol(root)
    box = install_sources(monkeypatch)
    box.answers = ROUND_1
    outcome = search.run_round(proj, ws, slug=SLUG)
    assert outcome.ok, outcome.summary
    screen(proj, ws, stage=TA, size=48, decide=decide_title_abstract)
    resolve(proj, ws, stage=TA, answers=TA_ANSWERS)
    # One of the fourteen title-abstract includes has no report: 13 reports are on disk.
    reports = ws / "sr" / "reports"
    reports.mkdir(parents=True)
    for n in range(2, 11):
        (reports / f"R{n:05d}.pdf").write_bytes(b"synthetic report")
    for rid in ("R00041", "R00043", "R00045", "R00047"):
        (reports / f"{rid}.pdf").write_bytes(b"synthetic report")
    screen(proj, ws, stage=FT, size=None, decide=decide_full_text)
    resolve(proj, ws, stage=FT, answers=FT_ANSWERS)
    if round2:
        box.answers = ROUND_2
        second = search.run_round(proj, ws, slug=SLUG)
        assert second.ok, second.summary
    return proj, ws, box


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
