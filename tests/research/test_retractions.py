"""research/lib/retractions.py: Crossref metadata + update-to retraction /
correction detection; unresolvable DOIs (doi.org handle check). Recorded
fixtures, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scieflow.research.lib import retractions
from scieflow.research.lib.http import HTTPFailure

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sources"
CR = "https://api.crossref.org/works/"
HANDLE = "https://doi.org/api/handles/"


def fx(*parts):
    return json.loads(FIX.joinpath(*parts).read_text())


class NoSleep:
    def __init__(self):
        self.sleeps = []

    def __call__(self, s):
        self.sleeps.append(s)


class DictCache:
    """Duck-typed stand-in for integrations.scite.cache.Cache."""

    def __init__(self):
        self.d = {}

    def get(self, key, ttl_class):
        return self.d.get((key, ttl_class))

    def put(self, key, ttl_class, value):
        self.d[(key, ttl_class)] = value


def check(doi, **kw):
    kw.setdefault("sleep", NoSleep())
    return retractions.check(doi, **kw)


@responses.activate
def test_retracted_via_updated_by_retraction_watch():
    responses.get(CR + "10.1016/s0140-6736(97)11096-0",
                  json=fx("crossref", "work_retracted.json"))
    r = check("https://doi.org/10.1016/S0140-6736(97)11096-0")
    assert r["status"] == "retracted"
    assert r["doi"] == "10.1016/s0140-6736(97)11096-0"
    assert r["registered"] == "crossref"
    assert r["updates"] == [
        {"type": "correction", "notice_doi": "10.1016/s0140-6736(04)15715-2",
         "date": "2004-03-06", "source": "retraction-watch"},
        {"type": "retraction", "notice_doi": "10.1016/s0140-6736(10)60175-4",
         "date": "2010-02-06", "source": "retraction-watch"},
    ]
    assert r["title"].startswith("RETRACTED: Ileal-lymphoid")
    assert r["year"] == 1998 and r["venue"] == "The Lancet"
    assert r["is_notice"] is False


@responses.activate
def test_corrected_publisher_correction():
    responses.get(CR + "10.1371/journal.pone.0161231", json=fx("crossref", "work_corrected.json"))
    r = check("10.1371/journal.pone.0161231")
    assert r["status"] == "corrected"
    assert r["updates"] == [{"type": "correction", "notice_doi": "10.1371/journal.pone.0301214",
                             "date": "2024-03-21", "source": "publisher"}]


@responses.activate
def test_ok_without_updates():
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    r = check("10.1371/journal.pone.0110232")
    assert r["status"] == "ok" and r["updates"] == []
    assert r["year"] == 2014 and r["venue"] == "PLoS ONE"


@responses.activate
def test_citing_the_retraction_notice_itself():
    responses.get(CR + "10.1016/s0140-6736(10)60175-4", json=fx("crossref", "work_notice.json"))
    r = check("10.1016/s0140-6736(10)60175-4")
    assert r["is_notice"] is True
    assert r["notice_of"] == [{"type": "retraction",
                               "doi": "10.1016/s0140-6736(97)11096-0"}]
    assert r["status"] == "ok"           # the notice is not itself retracted


@pytest.mark.parametrize("typ,status", [
    ("retraction", "retracted"), ("withdrawal", "retracted"), ("removal", "retracted"),
    ("Retraction", "retracted"), ("partial_retraction", "corrected"),
    ("correction", "corrected"), ("erratum", "corrected"), ("corrigendum", "corrected"),
    ("expression_of_concern", "corrected"), ("expression-of-concern", "corrected"),
    ("new_version", "ok"), ("something-else", "ok"),
])
def test_update_type_classification(typ, status):
    msg = {"DOI": "10.1000/x", "updated-by": [{"DOI": "10.1000/n", "type": typ,
                                               "source": "publisher"}]}
    assert retractions.classify(msg)["status"] == status


def test_retraction_outranks_correction_regardless_of_order():
    msg = {"DOI": "10.1000/x", "updated-by": [
        {"DOI": "10.1000/r", "type": "retraction"}, {"DOI": "10.1000/c", "type": "correction"}]}
    assert retractions.classify(msg)["status"] == "retracted"


def _upd(doi, typ, *date):
    u = {"DOI": doi, "type": typ, "source": "retraction-watch"}
    if date:
        u["updated"] = {"date-parts": [list(date)]}
    return u


def test_later_reinstatement_clears_retraction():
    msg = {"DOI": "10.1000/x", "updated-by": [_upd("10.1000/r", "retraction", 2015, 3, 1),
                                             _upd("10.1000/i", "reinstatement", 2016, 1, 5)]}
    r = retractions.classify(msg)
    assert r["status"] == "corrected"           # advisory, not a blocker
    assert [u["type"] for u in r["updates"]] == ["retraction", "reinstatement"]


def test_reinstatement_before_a_later_retraction_does_not_clear_it():
    msg = {"DOI": "10.1000/x", "updated-by": [_upd("10.1000/i", "reinstatement", 2014),
                                             _upd("10.1000/r", "retraction", 2020, 6)]}
    assert retractions.classify(msg)["status"] == "retracted"


def test_undated_reinstatement_is_conservative():
    msg = {"DOI": "10.1000/x", "updated-by": [_upd("10.1000/r", "retraction", 2015),
                                             _upd("10.1000/i", "reinstatement")]}
    assert retractions.classify(msg)["status"] == "retracted"


def test_same_day_reinstatement_clears():
    msg = {"DOI": "10.1000/x", "updated-by": [_upd("10.1000/i", "reinstatement", 2015, 3, 1),
                                             _upd("10.1000/r", "retraction", 2015, 3, 1)]}
    assert retractions.classify(msg)["status"] == "corrected"


@pytest.mark.parametrize("spelling", ["https://www.doi.org/10.1371/journal.pone.0110232",
                                      "doi.org/10.1371/journal.pone.0110232",
                                      "DOI 10.1371/journal.pone.0110232"])
@responses.activate
def test_common_doi_spellings_are_looked_up_not_unresolvable(spelling):
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    assert check(spelling)["status"] == "ok"


def test_malformed_update_entries_ignored():
    msg = {"DOI": "10.1000/x", "updated-by": ["junk", {"type": 5}, None,
                                             {"DOI": "10.1000/c", "type": "correction"}]}
    r = retractions.classify(msg)
    assert r["status"] == "corrected" and len(r["updates"]) == 1


@responses.activate
def test_unresolvable_doi_not_in_crossref_not_registered():
    responses.get(CR + "10.9999/definitely-not-a-doi-xyz", status=404, body="Resource not found.")
    responses.get(HANDLE + "10.9999/definitely-not-a-doi-xyz", status=404,
                  json=fx("doi", "handle_not_found.json"))
    r = check("10.9999/definitely-not-a-doi-xyz")
    assert r["status"] == "unresolvable"
    assert r["registered"] is None
    assert "not registered" in r["reason"]


@responses.activate
def test_registered_elsewhere_is_ok_not_unresolvable():
    responses.get(CR + "10.1038/nature12373", status=404)
    responses.get(HANDLE + "10.1038/nature12373", json=fx("doi", "handle_found.json"))
    r = check("10.1038/nature12373")
    assert r["status"] == "ok"
    assert r["registered"] == "other"      # resolves, but Crossref has no update data
    assert r["updates"] == []


@pytest.mark.parametrize("bad", ["not-a-doi", "10.12/x", "10.1234/../../etc", "", None])
@responses.activate
def test_malformed_doi_unresolvable_without_request(bad):
    r = check(bad)
    assert r["status"] == "unresolvable"
    assert r["reason"] == "malformed DOI"
    assert len(responses.calls) == 0


@responses.activate
def test_transient_failure_is_not_unresolvable():
    for _ in range(3):
        responses.get(CR + "10.1000/x", status=503)
    with pytest.raises(HTTPFailure) as exc:
        check("10.1000/x", retries=3)
    assert exc.value.classification == "TRANSIENT"


@responses.activate
def test_handle_check_failure_is_not_unresolvable():
    responses.get(CR + "10.1000/x", status=404)
    for _ in range(3):
        responses.get(HANDLE + "10.1000/x", status=500)
    with pytest.raises(HTTPFailure):
        check("10.1000/x")


@responses.activate
def test_handle_404_with_unexpected_body_is_not_unresolvable():
    # only an explicit "handle not found" (responseCode 100) proves unresolvable
    responses.get(CR + "10.1000/x", status=404)
    responses.get(HANDLE + "10.1000/x", status=404, body="<html>gateway</html>")
    with pytest.raises(HTTPFailure) as exc:
        check("10.1000/x")
    assert exc.value.classification == "MALFORMED"


@responses.activate
def test_malformed_crossref_body():
    responses.get(CR + "10.1000/x", body="not json")
    with pytest.raises(HTTPFailure) as exc:
        check("10.1000/x")
    assert exc.value.classification == "MALFORMED"


@responses.activate
def test_crossref_429_retry_after():
    s = NoSleep()
    responses.get(CR + "10.1371/journal.pone.0110232", status=429, headers={"Retry-After": "4"})
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    assert check("10.1371/journal.pone.0110232", sleep=s)["status"] == "ok"
    assert 4.0 in s.sleeps


@responses.activate
def test_cache_hit_makes_no_request():
    cache = DictCache()
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    a = check("10.1371/journal.pone.0110232", cache=cache)
    b = check("10.1371/JOURNAL.PONE.0110232", cache=cache)
    assert a == b and len(responses.calls) == 1
    assert all(len(k[0]) == 64 and k[1] == "metadata" for k in cache.d)


@responses.activate
def test_check_many_dedupes_and_maps_failures():
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    for _ in range(3):
        responses.get(CR + "10.1000/down", status=503)
    out = retractions.check_many(["10.1371/journal.pone.0110232", "10.1371/JOURNAL.pone.0110232",
                                  "10.1000/down", "junk"], sleep=NoSleep())
    assert list(out) == ["10.1371/journal.pone.0110232", "10.1000/down", "junk"]
    assert out["10.1371/journal.pone.0110232"]["status"] == "ok"
    assert out["10.1000/down"] == {"doi": "10.1000/down", "status": "unavailable",
                                   "error": "TRANSIENT"}
    assert out["junk"]["status"] == "unresolvable"
    assert sum(1 for c in responses.calls if "0110232" in c.request.url) == 1


@responses.activate
def test_mailto_in_user_agent(monkeypatch):
    monkeypatch.setenv("SCIEFLOW_MAILTO", "lab@example.org")
    responses.get(CR + "10.1371/journal.pone.0110232", json=fx("crossref", "work_ok.json"))
    check("10.1371/journal.pone.0110232")
    assert "mailto:lab@example.org" in responses.calls[0].request.headers["User-Agent"]
