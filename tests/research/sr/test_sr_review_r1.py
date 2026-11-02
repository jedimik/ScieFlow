"""M-H t3 review round 1 fixes (reviews/m-h-t3-review-r1.md, findings 1-6): the plan's t3 items that
had no test, and the corrupted-corpus hardening of records._read_jsonl. Every source is a fake
module; the L2 test uses the real Gateway with mocked HTTP only. Synthetic data only; no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import responses
import yaml
from click.testing import CliRunner

from scieflow.core import events, network, service
from scieflow.core.design import lock
from scieflow.research.search import fanout, registry
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr import records, search
from scieflow.research.sr.errors import SRError
from tests.research.sr.test_sr_search import (REPO, ROUND_1, ROUND_2, SLUG, _title, lock_protocol,
                                              make_root, sources)  # noqa: F401  (fixture)

SR_EVENTS = ("sr.retrieval", "sr.dedup_decision", "sr.committed")


def _edit_limit(ws, limit: int) -> None:
    """Change the first strategy's limit in the working design.yml (a modification after the lock)."""
    path = ws / "design" / "design.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["review"]["strategies"][0]["limit"] = limit
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _miss(name: str) -> network.NetworkMiss:
    return network.NetworkMiss(name, "search", "0123456789abcdef0123", "replay", SLUG)


def test_the_schema_review_sources_are_the_fanout_order():
    schema = yaml.safe_load((REPO / "schemas" / "design.yml").read_text(encoding="utf-8"))
    assert tuple(schema["enums"]["review_sources"]) == fanout.ORDER


def test_a_replay_miss_in_every_source_exits_six_and_writes_nothing(tmp_path, sources):
    proj, ws = lock_protocol(make_root(tmp_path / "miss"))
    sources.fail = {name: _miss(name) for name in ("openalex", "europepmc", "crossref")}
    out = search.run_round(proj, ws, slug=SLUG)
    assert not out.ok and out.exit_code == 6
    assert {row["status"] for row in out.summary["statuses"]} == {"offline-miss"}
    assert not (ws / "sr").exists()
    assert events.read(ws, types=SR_EVENTS) == []


def test_sr_search_replay_miss_exits_six_through_the_cli(tmp_path, sources, monkeypatch):
    base = make_root(tmp_path / "miss-cli")
    monkeypatch.setattr(sr_cli, "_root", lambda: base)
    lock_protocol(base)
    sources.fail = {name: _miss(name) for name in ("openalex", "europepmc", "crossref")}
    result = CliRunner().invoke(sr_cli.sr, ["search", SLUG])
    assert result.exit_code == 6, result.output
    assert not (base / "workspace" / SLUG / "sr").exists()


def test_a_refused_second_strategy_sends_nothing_for_the_first(tmp_path, sources, monkeypatch):
    proj, ws = lock_protocol(make_root(tmp_path / "second"))
    real_check = registry.check_query
    seen: list[str] = []

    def refuse_second(query):
        seen.append(query)
        if len(seen) == 2:                          # strategy s2 (the first check passes)
            raise registry.LeakError("refused: synthetic leak check")
        return real_check(query)

    monkeypatch.setattr(registry, "check_query", refuse_second)
    with pytest.raises(registry.LeakError):
        search.run_round(proj, ws, slug=SLUG)
    assert len(seen) == 2
    assert sources.calls == []
    assert not (ws / "sr").exists()


def test_a_modified_protocol_is_refused_before_any_request(tmp_path, sources):
    proj, ws = lock_protocol(make_root(tmp_path / "modified"))
    _edit_limit(ws, 150)
    with pytest.raises(SRError, match="differs from its lock"):
        search.run_round(proj, ws, slug=SLUG)
    assert sources.calls == []
    assert not (ws / "sr").exists()


def test_round_two_after_an_amendment_of_a_non_living_review(tmp_path, sources):
    proj, ws = lock_protocol(make_root(tmp_path / "amended", living=False))
    sources.answers = ROUND_1
    assert search.run_round(proj, ws, slug=SLUG).ok
    _edit_limit(ws, 150)
    gate = lock.amend(proj, ws, "Raise the first strategy limit", "Synthetic amendment for the test")
    service.answer_gate(proj, SLUG, gate["id"], "approve")
    assert lock.state(proj, ws).version == "2.0"
    sources.answers = ROUND_2
    out = search.run_round(proj, ws, slug=SLUG)
    assert out.ok and out.summary["round"] == 2 and out.summary["protocol_version"] == 2
    assert out.summary["new_records"] == 3
    assert [r["id"] for r in records.load_corpus(ws)][-3:] == ["R00049", "R00050", "R00051"]


def test_two_corpus_records_that_dedupe_together_are_warned_and_the_first_is_kept(tmp_path, sources,
                                                                                 monkeypatch):
    proj, ws = lock_protocol(make_root(tmp_path / "pair"))
    sources.answers = ROUND_1
    assert search.run_round(proj, ws, slug=SLUG).ok
    real_dedupe = search.dedupe

    def dedupe_with_a_corpus_pair(recs):
        # The vendored dedupe found no corpus pair to merge; report one, as a title match would.
        result = real_dedupe(recs)
        pair = SimpleNamespace(kept_id="R00001", duplicate_id="R00002")
        return SimpleNamespace(records=result.records, decisions=[*result.decisions, pair])

    monkeypatch.setattr(search, "dedupe", dedupe_with_a_corpus_pair)
    sources.answers = ROUND_2
    out = search.run_round(proj, ws, slug=SLUG)
    assert out.ok and out.summary["new_records"] == 3
    warning = "R00001, R00002 were one group; R00001 is kept"
    assert out.summary["warnings"] == [warning]
    doc = json.loads((ws / "sr" / "rounds" / "2" / "round.json").read_text(encoding="utf-8"))
    assert doc["warnings"] == [warning]


def test_round_events_carry_no_record_title_or_abstract(tmp_path, sources):
    proj, ws = lock_protocol(make_root(tmp_path / "events"))
    sources.answers = ROUND_1
    assert search.run_round(proj, ws, slug=SLUG).ok
    dumped = json.dumps(events.read(ws), ensure_ascii=False)
    assert events.read(ws, types=SR_EVENTS)                 # the events exist, so the check is real
    assert _title(0) not in dumped and _title(42) not in dumped
    assert "Synthetic abstract" not in dumped


def test_a_failed_round_prints_no_query_or_record_text(tmp_path, sources, monkeypatch):
    base = make_root(tmp_path / "failed-cli")
    monkeypatch.setattr(sr_cli, "_root", lambda: base)
    lock_protocol(base)
    sources.answers = ROUND_1
    sources.fail = {"crossref": RuntimeError("synthetic failure")}
    result = CliRunner().invoke(sr_cli.sr, ["search", SLUG])
    assert result.exit_code == 1, result.output
    assert "crossref: failed" in result.output
    assert "synthetic protocol query" not in result.output
    assert "Synthetic abstract" not in result.output and _title(0) not in result.output


def test_a_corrupted_committed_round_is_refused_with_its_path(tmp_path, sources, monkeypatch):
    base = make_root(tmp_path / "corrupt")
    monkeypatch.setattr(sr_cli, "_root", lambda: base)
    proj, ws = lock_protocol(base)
    sources.answers = ROUND_1
    assert search.run_round(proj, ws, slug=SLUG).ok
    (ws / "sr" / "rounds" / "1" / "records.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(SRError, match=r"sr/rounds/1/records\.jsonl is not valid JSON lines"):
        records.load_corpus(ws)
    result = CliRunner().invoke(sr_cli.sr, ["records", SLUG])
    assert result.exit_code == 1, result.output
    assert "sr/rounds/1/records.jsonl is not valid JSON lines" in result.output
    assert "Traceback" not in result.output


@responses.activate
def test_an_l2_run_refuses_every_source_before_any_request(tmp_path, monkeypatch):
    base = make_root(tmp_path / "l2")
    (base / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(base)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    proj, ws = lock_protocol(base)
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    out = search.run_round(proj, ws, slug=SLUG)
    assert not out.ok and out.exit_code == 3
    assert {row["status"] for row in out.summary["statuses"]} == {"refused"}
    assert len(responses.calls) == 0
    assert not (ws / "sr").exists()
