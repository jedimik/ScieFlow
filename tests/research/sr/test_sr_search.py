"""M-H t3 (plan 5.5, 5.12, Part B t3 items 3-7): search rounds and the corpus.

Every source is a fake module (synthetic records, no network, no agent). The protocol is locked
through the real design lock. Round 1 reproduces the plan's 5.12 numbers: identified 60 (by source
30/20/10), duplicates 12, records R00001-R00048. Round 2 (living) reproduces 65/14/51 and the new
ids R00049-R00051. Synthetic bibliographic data only; no patient data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import types
from pathlib import Path

import pytest
import yaml

from scieflow.core import events, service
from scieflow.core.design import lock
from scieflow.core.project import Project
from scieflow.core.run import status
from scieflow.research.lib import papers
from scieflow.research.search import fanout, registry
from scieflow.research.sr import ledger, records, search
from scieflow.research.sr.errors import SRError

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"
SLUG = "r1"

_A = ["amber", "basalt", "cobalt", "delta", "ember", "fjord", "granite", "harbor", "island", "jasper",
      "kelp", "lagoon", "meadow", "nickel", "orchid", "pebble", "quartz", "river", "saffron", "tundra",
      "umber", "valley", "willow", "xenon", "yarrow", "zephyr", "acorn", "birch", "cedar", "dune",
      "elm", "fern", "gorge", "heron", "iris", "juniper", "krill", "lichen", "maple", "nettle", "oak",
      "pine", "quail", "reef", "sable", "thistle", "upland", "vine", "walnut", "yew", "zinnia",
      "anchor", "beacon", "canyon", "drift", "eddy", "flint", "glacier", "hollow", "inlet", "jetty"]
_B = [_A[(i * 7) % 60] for i in range(60)]


def _title(key: int) -> str:
    return f"{_A[key]} {_B[key]} cohort"


def _doi(prefix: str, n: int) -> str:
    return f"10.9/{prefix}{n:02d}"


def _paper(doi: str, key: int, year: int = 2020) -> dict:
    return papers.record(doi=doi, title=_title(key), year=year, venue="Synthetic Journal",
                         abstract=f"Synthetic abstract {doi}.", url=f"https://x.test/{doi}")


# Round 1 keys: o01-o30 are 0-29, e01-e12 are 30-41, c01-c06 are 42-47; round 2 n01-n03 are 48-50.
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


@pytest.fixture
def sources(monkeypatch):
    box = types.SimpleNamespace(answers={}, calls=[], fail={})

    def make(name):
        def search_fn(gw, checked, *, limit=25, from_year=None):
            box.calls.append({"source": name, "limit": limit, "from_year": from_year})
            if name in box.fail:
                raise box.fail[name]
            return [dict(r) for r in box.answers.get(name, [])][:limit]
        return types.SimpleNamespace(search=search_fn)

    for name in fanout.ORDER:
        monkeypatch.setitem(fanout.MODULES, name, make(name))
    monkeypatch.setattr(registry, "gateway", lambda *a, **k: FakeGW())
    return box


def make_root(base: Path, *, living: bool = True, s1_limit: int = 200) -> Path:
    """A temporary project with run r1 holding the synthetic SR design (two strategies)."""
    shutil.copytree(REPO / "schemas", base / "schemas")
    shutil.copytree(REPO / "config" / "reporting", base / "config" / "reporting")
    ws = base / "workspace" / SLUG
    ws.mkdir(parents=True)
    status.write_status(ws, status.new_status(SLUG, "autonomous"))
    (ws / "design").mkdir()
    doc = yaml.safe_load((FIX / "systematic-review.yml").read_text(encoding="utf-8"))
    doc["review"]["strategies"][0]["limit"] = s1_limit
    doc["review"]["strategies"].append({"id": "s2", "query": "synthetic protocol query",
                                        "sources": ["crossref"], "limit": 200})
    doc["review"]["living"] = living
    (ws / "design" / "design.yml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return base


def lock_protocol(root: Path):
    proj = Project(root)
    ws = root / "workspace" / SLUG
    gate = lock.lock(proj, ws)
    service.answer_gate(proj, SLUG, gate["id"], "approve")
    return proj, ws


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def root(tmp_path):
    return make_root(tmp_path / "a")


def test_round_one_reproduces_the_plan_scenario(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    out = search.run_round(proj, ws, slug=SLUG)
    assert out.ok and out.exit_code == 0
    summary = out.summary
    assert summary["round"] == 1
    assert summary["identified"] == 60
    assert summary["by_source"] == {"openalex": 30, "europepmc": 20, "crossref": 10}
    assert summary["duplicates"] == 12
    assert summary["new_records"] == 48
    recs = read_jsonl(ws / "sr" / "rounds" / "1" / "records.jsonl")
    assert [r["id"] for r in recs] == [f"R{n:05d}" for n in range(1, 49)]
    assert recs[0]["doi"] == "10.9/o01" and recs[29]["doi"] == "10.9/o30"
    assert recs[-1]["doi"] == "10.9/c06"
    hits = read_jsonl(ws / "sr" / "rounds" / "1" / "hits.jsonl")
    assert len(hits) == 60
    assert hits[0]["id"] == "1/s1/openalex:0"
    assert len(hits) - summary["duplicates"] == len(recs)


def test_the_three_round_files_and_the_commit_event_are_written(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    rdir = ws / "sr" / "rounds" / "1"
    assert {p.name for p in rdir.iterdir()} == {"hits.jsonl", "records.jsonl", "round.json"}
    doc = json.loads((rdir / "round.json").read_text(encoding="utf-8"))
    assert doc["round"] == 1 and doc["identified"] == 60 and doc["duplicates"] == 12
    assert doc["new_records"] == 48
    assert doc["strategies"]["s1"]["openalex"] == {"status": "ok", "count": 30, "capped": False}
    committed = events.read(ws, types=("sr.committed",))
    assert len(committed) == 1
    data = committed[0]["data"]
    assert data["kind"] == "search" and data["round"] == 1
    assert data["files"] == {f"sr/rounds/1/{name}": sha(rdir / name)
                             for name in ("hits.jsonl", "records.jsonl", "round.json")}


def test_the_retrieval_and_dedup_events_precede_the_commit(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    rows = events.read(ws, types=("sr.retrieval", "sr.dedup_decision", "sr.committed"))
    assert [row["type"] for row in rows] == ["sr.retrieval"] * 3 + ["sr.dedup_decision", "sr.committed"]
    retrievals = [row["data"] for row in rows[:3]]
    assert [(r["strategy"], r["source"]) for r in retrievals] == [
        ("s1", "openalex"), ("s1", "europepmc"), ("s2", "crossref")]
    assert all(r["query"] == "synthetic protocol query" for r in retrievals)
    assert [len(r["record_ids"]) for r in retrievals] == [30, 20, 10]
    assert len(rows[3]["data"]["losers"]) == 12
    op = rows[4]["data"]["op"]
    assert all(row["data"]["op"] == op for row in rows)
    view = list(ledger.view(ws).iter_events())
    assert sum(1 for e in view if e.type == "retrieval") == 3
    assert sum(1 for e in view if e.type == "dedup_decision") == 1


def test_same_input_twice_gives_identical_files(root, sources, tmp_path):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    other_root = make_root(tmp_path / "b")
    other_proj, other_ws = lock_protocol(other_root)
    search.run_round(other_proj, other_ws, slug=SLUG)
    for name in ("hits.jsonl", "records.jsonl", "round.json"):
        assert (ws / "sr" / "rounds" / "1" / name).read_bytes() == \
            (other_ws / "sr" / "rounds" / "1" / name).read_bytes(), name


def test_round_two_of_a_living_review_matches_the_plan_numbers(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    sources.answers = ROUND_2
    out = search.run_round(proj, ws, slug=SLUG)
    assert out.ok
    assert out.summary["round"] == 2
    assert out.summary["identified"] == 5 and out.summary["duplicates"] == 2
    assert out.summary["new_records"] == 3
    new = read_jsonl(ws / "sr" / "rounds" / "2" / "records.jsonl")
    assert [r["id"] for r in new] == ["R00049", "R00050", "R00051"]
    assert {r["doi"] for r in new} == {"10.9/n01", "10.9/n02", "10.9/n03"}
    rounds = [json.loads((ws / "sr" / "rounds" / str(n) / "round.json").read_text(encoding="utf-8"))
              for n in (1, 2)]
    assert sum(r["identified"] for r in rounds) == 65
    assert sum(r["duplicates"] for r in rounds) == 14
    assert len(records.load_corpus(ws)) == 51


def test_a_non_living_review_searches_once(tmp_path, sources):
    root = make_root(tmp_path / "c", living=False)
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    with pytest.raises(SRError, match="a non-living review searches once"):
        search.run_round(proj, ws, slug=SLUG)


def test_a_failed_source_writes_nothing_and_exits_with_the_status_code(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    sources.fail = {"europepmc": RuntimeError("synthetic failure")}
    out = search.run_round(proj, ws, slug=SLUG)
    assert not out.ok
    assert out.exit_code == 1
    assert not (ws / "sr").exists()
    assert events.read(ws, types=("sr.retrieval", "sr.dedup_decision", "sr.committed")) == []


def test_a_refused_query_sends_nothing_for_any_strategy(root, sources, monkeypatch):
    proj, ws = lock_protocol(root)

    def refuse(query):
        raise registry.LeakError("refused: synthetic leak check")

    monkeypatch.setattr(registry, "check_query", refuse)
    with pytest.raises(registry.LeakError):
        search.run_round(proj, ws, slug=SLUG)
    assert sources.calls == []
    assert not (ws / "sr").exists()


def test_an_unlocked_protocol_is_refused_before_any_request(tmp_path, sources):
    root = make_root(tmp_path / "d")
    proj = Project(root)
    ws = root / "workspace" / SLUG
    with pytest.raises(SRError, match="lock the protocol first"):
        search.run_round(proj, ws, slug=SLUG)
    assert sources.calls == []


def test_a_source_that_returns_its_limit_is_capped(tmp_path, sources):
    root = make_root(tmp_path / "e", s1_limit=30)
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    doc = json.loads((ws / "sr" / "rounds" / "1" / "round.json").read_text(encoding="utf-8"))
    assert doc["strategies"]["s1"]["openalex"]["capped"] is True
    assert doc["strategies"]["s1"]["europepmc"]["capped"] is False
    assert doc["strategies"]["s2"]["crossref"]["capped"] is False


def test_the_protocol_limit_and_from_year_reach_the_source(root, sources):
    proj, ws = lock_protocol(root)
    sources.answers = ROUND_1
    search.run_round(proj, ws, slug=SLUG)
    s1 = [c for c in sources.calls if c["source"] == "openalex"]
    assert s1 and s1[0]["limit"] == 200 and s1[0]["from_year"] == 2000
