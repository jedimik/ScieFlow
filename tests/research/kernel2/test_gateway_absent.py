"""M-C2 t2 (plan 5.2, items 1-2): the Gateway's `absent` statuses and the `absent` snapshot kind.

Synthetic data; every transport is a fake session. No network.
"""

from __future__ import annotations

import json

import pytest

from scieflow._vendor.researcher_core.model import canonical_json, sha256_hex
from scieflow.integrations.scite.cache import normalise_params, request_key
from scieflow.research.lib import providers, snapshots
from scieflow.research.lib.http import HTTPFailure

P = "search-crossref"
OP = "work"
URL = "https://api.crossref.org/works/10.5555/absent.1"
KEY = request_key(P, OP, {"url": URL, **normalise_params({})})


def test_an_absent_status_returns_none_once_then_serves_the_cache(make_gateway, fake_session):
    sess = fake_session(status=404)
    gw = make_gateway(sess)
    assert gw.get_json(P, OP, URL, absent=(404, 410)) is None
    assert gw.get_json(P, OP, URL, absent=(404, 410)) is None
    assert len(sess.calls) == 1, "the second call must be a cache hit"
    rows = [r for r in gw.ledger.rows("run") if r["source"] == P]
    assert [(r["status"], r["results"], r["cached"]) for r in rows] == [("ok", 0, False),
                                                                      ("ok", 0, True)]


def test_without_absent_a_404_still_raises_http_404(make_gateway, fake_session):
    sess = fake_session(status=404)
    gw = make_gateway(sess)
    with pytest.raises(HTTPFailure) as exc:
        gw.get_json(P, OP, URL)
    assert exc.value.classification == "HTTP_404"
    assert len(sess.calls) == 1


def test_only_the_listed_statuses_are_absent(make_gateway, fake_session):
    gw = make_gateway(fake_session(status=410))
    with pytest.raises(HTTPFailure) as exc:
        gw.get_json(P, OP, URL, absent=(404,))
    assert exc.value.classification == "HTTP_410"


@pytest.mark.parametrize("bad", [(200,), (500,), (404, 302), (99,),
                                 # transient, rate-limit and auth answers are never "no record"
                                 (408,), (425,), (429,), (401,), (403,), (404, 429)])
def test_absent_takes_client_errors_only(make_gateway, fake_session, bad):
    sess = fake_session(status=404)
    gw = make_gateway(sess)
    with pytest.raises(ValueError):
        gw.get_json(P, OP, URL, absent=bad)
    assert sess.calls == []


@pytest.mark.parametrize("bad", [(408,), (429,), (403,)])
def test_absent_refused_on_text_endpoints_too(make_gateway, fake_session, bad):
    sess = fake_session(status=404)
    gw = make_gateway(sess)
    with pytest.raises(ValueError):
        gw.get_text(P, OP, URL, count=lambda _b: 0, absent=bad)
    assert sess.calls == []


def test_get_text_absent_returns_none_once_then_serves_the_cache(make_gateway, fake_session):
    sess = fake_session(status=404)
    gw = make_gateway(sess)
    assert gw.get_text(P, OP, URL, count=lambda _b: 0, absent=(404,)) is None
    assert gw.get_text(P, OP, URL, count=lambda _b: 0, absent=(404,)) is None
    assert len(sess.calls) == 1, "the second call must be a cache hit"
    rows = [r for r in gw.ledger.rows("run") if r["source"] == P]
    assert [(r["status"], r["results"], r["cached"]) for r in rows] == [("ok", 0, False),
                                                                      ("ok", 0, True)]


def test_get_text_without_absent_a_404_still_raises(make_gateway, fake_session):
    gw = make_gateway(fake_session(status=404))
    with pytest.raises(HTTPFailure) as exc:
        gw.get_text(P, OP, URL, count=lambda _b: 0)
    assert exc.value.classification == "HTTP_404"


def test_a_recorded_text_absent_answer_replays_as_none_with_no_transport(project, make_gateway,
                                                                         fake_session):
    _root, ws, _home = project
    rec = make_gateway(fake_session(status=404), network="record")
    assert rec.get_text(P, OP, URL, count=lambda _b: 0, absent=(404,)) is None
    snap = snapshots.load(ws, provider=P, op=OP, key=KEY, url=URL, request_params={})
    assert snap["body_kind"] == "absent"

    replay_sess = fake_session(status=200, body="<feed/>")
    rep = make_gateway(replay_sess, network="replay")
    assert rep.get_text(P, OP, URL, count=lambda _b: 0, absent=(404,)) is None
    assert replay_sess.calls == []


def test_a_recorded_absent_answer_replays_as_none_with_no_transport(project, make_gateway,
                                                                    fake_session):
    root, ws, home = project
    rec = make_gateway(fake_session(status=404), network="record")
    assert rec.get_json(P, OP, URL, absent=(404,)) is None

    snap = snapshots.load(ws, provider=P, op=OP, key=KEY, url=URL, request_params={})
    assert snap["body_kind"] == "absent"
    assert snap["response_body"] is None
    assert snap["results"] == 0

    replay_sess = fake_session(status=200, body={"unexpected": True})
    rep = make_gateway(replay_sess, network="replay")
    assert rep.get_json(P, OP, URL, absent=(404,)) is None
    assert replay_sess.calls == []


def _write_raw(ws, body_kind, body):
    rec = {"schema_version": 1, "source": P, "endpoint": OP, "url": URL, "request_params": {},
           "request_key": KEY, "body_kind": body_kind, "response_body": body,
           "response_hash": sha256_hex(canonical_json(body)), "results": 0,
           "retrieved_at": "2026-10-30T10:00:00Z"}
    target = snapshots.path(ws, P, KEY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rec), encoding="utf-8")


def test_a_valid_absent_snapshot_loads(project):
    _root, ws, _home = project
    _write_raw(ws, "absent", None)
    assert snapshots.load(ws, provider=P, op=OP, key=KEY, url=URL,
                          request_params={})["body_kind"] == "absent"


@pytest.mark.parametrize("body_kind,body", [
    ("absent", {"x": 1}),      # an absent answer carries no body
    ("json", None),            # a stored json answer is never null
    ("text", None),            # a text answer is a string
])
def test_a_snapshot_whose_body_does_not_match_its_kind_is_corrupt(project, body_kind, body):
    _root, ws, _home = project
    _write_raw(ws, body_kind, body)
    with pytest.raises(snapshots.SnapshotCorrupt):
        snapshots.load(ws, provider=P, op=OP, key=KEY, url=URL, request_params={})


def test_the_writer_refuses_an_absent_kind_with_a_body(project):
    _root, ws, _home = project
    with pytest.raises(ValueError):
        snapshots.write(ws, provider=P, op=OP, key=KEY, url=URL, request_params={},
                        body_kind="absent", response_body={"x": 1}, results=0)
    assert not snapshots.path(ws, P, KEY).exists()
