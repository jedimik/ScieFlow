"""M-H t3 (plan 5.5, 5.13): `scieflow sr search` and `scieflow sr records` through the sr group.

Exit codes: 0 ok, 1 refused or invalid (SRError or a partial round), 2 usage, 3 a refused query
(nothing sent), 6 replay miss. The sources are fake modules; the tests never reach the network.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scieflow.core import events
from scieflow.research.search import registry
from scieflow.research.sr import cli as sr_cli
from scieflow.research.sr import records
from tests.research.sr.test_sr_search import (ROUND_1, ROUND_2, SLUG, lock_protocol, make_root,
                                              sources)  # noqa: F401  (fixtures)


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = make_root(tmp_path / "cli")
    monkeypatch.setattr(sr_cli, "_root", lambda: base)
    return base


def _run(*args):
    return CliRunner().invoke(sr_cli.sr, list(args))


def test_sr_search_prints_the_round_summary_and_exits_zero(root, sources):
    lock_protocol(root)
    sources.answers = ROUND_1
    result = _run("search", SLUG, "--json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["round"] == 1 and doc["identified"] == 60 and doc["new_records"] == 48
    assert "synthetic protocol query" not in result.output


def test_sr_search_partial_round_exits_one_and_writes_nothing(root, sources):
    lock_protocol(root)
    sources.answers = ROUND_1
    sources.fail = {"crossref": RuntimeError("synthetic failure")}
    result = _run("search", SLUG)
    assert result.exit_code == 1, result.output
    assert not (root / "workspace" / SLUG / "sr").exists()


def test_sr_search_refused_query_exits_three_and_sends_nothing(root, sources, monkeypatch):
    lock_protocol(root)

    def refuse(query):
        raise registry.LeakError("refused: synthetic leak check")

    monkeypatch.setattr(registry, "check_query", refuse)
    result = _run("search", SLUG)
    assert result.exit_code == 3, result.output
    assert sources.calls == []


def test_sr_search_on_an_unlocked_protocol_exits_one(root, sources):
    result = _run("search", SLUG)
    assert result.exit_code == 1
    assert "lock the protocol first" in result.output
    assert sources.calls == []


def test_sr_records_lists_the_committed_corpus(root, sources):
    lock_protocol(root)
    sources.answers = ROUND_1
    assert _run("search", SLUG).exit_code == 0
    result = _run("records", SLUG, "--json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["id"] for r in rows] == [f"R{n:05d}" for n in range(1, 49)]


def test_sr_records_round_filter_and_uncommitted_rounds_are_not_listed(root, sources):
    lock_protocol(root)
    sources.answers = ROUND_1
    _run("search", SLUG)
    sources.answers = ROUND_2
    assert _run("search", SLUG).exit_code == 0
    ws = root / "workspace" / SLUG
    stray = ws / "sr" / "rounds" / "3"
    stray.mkdir(parents=True)
    (stray / "records.jsonl").write_text(json.dumps({"id": "R00052", "round": 3}) + "\n", encoding="utf-8")
    everything = json.loads(_run("records", SLUG, "--json").output)
    assert len(everything) == 51 and "R00052" not in {r["id"] for r in everything}
    only_two = json.loads(_run("records", SLUG, "--round", "2", "--json").output)
    assert [r["id"] for r in only_two] == ["R00049", "R00050", "R00051"]
    assert records.load_corpus(ws) == everything


def test_the_committed_rounds_are_the_only_round_numbers(root, sources):
    lock_protocol(root)
    sources.answers = ROUND_1
    _run("search", SLUG)
    ws = root / "workspace" / SLUG
    committed = [e["data"]["round"] for e in events.read(ws, types=("sr.committed",))]
    assert committed == [1]
