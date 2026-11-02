"""M-H t3 (plan 5.5, M-H-D13): `fanout.collect` is the loop of `run_all` moved into a function.

Every source is a fake (synthetic answers, no network). The byte-identity test compares the
`search all` payload with a golden recorded on the pre-refactor code (tests/research/sr/golden),
so the extraction is proven not to change the output. Synthetic bibliographic data only.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from scieflow.core import sanitize
from scieflow.research.lib import papers
from scieflow.research.search import fanout, registry

GOLDEN = Path(__file__).parent / "golden" / "search_all_before.json"


class FakePolicy:
    def allows(self, name):
        return True

    def reason(self, name):
        return None


class FakeGW:
    policy = FakePolicy()


def rec(title, doi=None, year=2020, venue="Synthetic Journal"):
    url = f"https://x.test/{doi}" if doi else None
    return papers.record(doi=doi, title=title, year=year, venue=venue,
                         abstract=f"Synthetic abstract of {title}.", url=url)


GOLDEN_ANSWERS = {
    "openalex": [rec("Alpha study of synthetic markers", "10.1/a", 2020),
                 rec("Beta study of synthetic cohorts", None, 2019)],
    "crossref": [rec("Alpha study of synthetic markers", "10.1/a", 2020),
                 rec("Gamma study of synthetic trials", "10.1/g", 2021)],
    "europepmc": None,          # fails on purpose: the `failed` status path
    "arxiv": [rec("Delta study of synthetic signals", None, 2022)],
}


def _fake(answers, calls):
    def make(name):
        def search(gw, checked, *, limit=25, from_year=None):
            calls.append((name, limit, from_year))
            answer = answers.get(name)
            if answer is None:
                raise RuntimeError("synthetic failure")
            return [dict(r) for r in answer][:limit]
        return types.SimpleNamespace(search=search)
    return make


@pytest.fixture
def fake_sources(monkeypatch):
    calls: list = []
    make = _fake(GOLDEN_ANSWERS, calls)
    for name in list(fanout.MODULES):
        monkeypatch.setitem(fanout.MODULES, name, make(name))
    return calls


def _golden_bytes() -> str:
    payloads = [
        fanout.run_all(FakeGW(), "synthetic golden query", limit=25),
        fanout.run_all(FakeGW(), "synthetic golden query", sources=("crossref", "openalex"),
                       limit=1, top=2),
    ]
    return "".join(json.dumps(sanitize.sanitize_json_strings(p), indent=2, ensure_ascii=False) + "\n"
                   for p in payloads)


def test_search_all_payload_is_byte_identical_to_the_recorded_golden(fake_sources):
    assert _golden_bytes() == GOLDEN.read_text(encoding="utf-8")


def test_collect_asks_only_the_chosen_sources_in_canonical_order(fake_sources):
    chosen, statuses, answers = fanout.collect(FakeGW(), "synthetic query",
                                               sources=("arxiv", "openalex"), limit=3)
    assert chosen == ["openalex", "arxiv"]
    assert [s["source"] for s in statuses] == ["openalex", "arxiv"]
    assert set(answers) == {"openalex", "arxiv"}
    assert [name for name, _limit, _year in fake_sources] == ["openalex", "arxiv"]
    assert all(limit == 3 for _name, limit, _year in fake_sources)


def test_collect_reports_a_failed_source_without_sinking_the_others(fake_sources):
    chosen, statuses, answers = fanout.collect(FakeGW(), "synthetic query",
                                               sources=("openalex", "europepmc"))
    by_name = {s["source"]: s for s in statuses}
    assert by_name["openalex"]["status"] == "ok" and by_name["openalex"]["count"] == 2
    assert by_name["europepmc"]["status"] == "failed"
    assert "europepmc" not in answers


def test_collect_refuses_an_empty_query_before_any_source_is_asked(fake_sources):
    with pytest.raises(registry.LeakError):
        fanout.collect(FakeGW(), "!!!", sources=("openalex",))
    assert fake_sources == []
