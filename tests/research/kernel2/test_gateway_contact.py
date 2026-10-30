"""M-C2 t2 (plan 5.2, items 3-4): the Gateway's `contact` parameter (Unpaywall's e-mail).

The address is built at run time under example.org. It must never reach a file under the run or
the cache home, a ledger row, an event or a message. Every transport is a fake session.
"""

from __future__ import annotations

import uuid

import pytest

from scieflow.integrations.scite.cache import normalise_params, request_key
from scieflow.research.lib import providers

P = "unpaywall"
OP = "lookup"
UNPAY = "https://api.unpaywall.org/v2/10.5555/contact.1"
BODY = {"doi": "10.5555/contact.1", "is_oa": False}


def _address():
    return f"contact-{uuid.uuid4().hex[:10]}@example.org"


def _cfg(address):
    return {"providers": {"unpaywall": {"enabled": True, "contact_email": address}}}


def test_the_wire_carries_the_address_and_the_key_does_not(make_gateway, fake_session):
    address = _address()
    sess = fake_session(status=200, body=BODY)
    gw = make_gateway(sess, cfg=_cfg(address), network="live")
    assert gw.get_json(P, OP, UNPAY, contact={"email": address}) == BODY
    assert sess.calls[0]["params"] == {"email": address}
    rows = [r for r in gw.ledger.rows("run") if r["source"] == P]
    assert rows[0]["key"] == request_key(P, OP, {"url": UNPAY, **normalise_params({})})
    assert rows[0]["request"] == {}


def test_no_file_under_the_run_or_the_home_holds_the_address(project, make_gateway,
                                                             fake_session):
    root, ws, home = project
    address = _address()
    sess = fake_session(status=200, body=BODY)
    rec = make_gateway(sess, cfg=_cfg(address), network="record")
    assert rec.get_json(P, OP, UNPAY, contact={"email": address}) == BODY
    assert list((ws / "snapshots" / "literature" / P).glob("*.json")), "a snapshot was recorded"
    scanned = [f for area in (ws, home) for f in area.rglob("*") if f.is_file()]
    assert scanned, "the scan must cover the run, the ledger and the cache"
    for item in scanned:
        assert address.encode() not in item.read_bytes(), item.name


def test_replay_needs_no_address_on_the_wire(make_gateway, fake_session):
    address = _address()
    rec = make_gateway(fake_session(status=200, body=BODY), cfg=_cfg(address), network="record")
    rec.get_json(P, OP, UNPAY, contact={"email": address})
    replay_sess = fake_session(status=200, body={"unexpected": True})
    rep = make_gateway(replay_sess, cfg=_cfg(address), network="replay")
    assert rep.get_json(P, OP, UNPAY) == BODY
    assert replay_sess.calls == []


def test_contact_for_a_provider_without_a_contact_is_refused(make_gateway, fake_session):
    sess = fake_session(status=200, body=BODY)
    gw = make_gateway(sess)
    with pytest.raises(ValueError):
        gw.get_json("search-crossref", OP, "https://api.crossref.org/works/10.5555/c",
                    contact={"email": _address()})
    assert sess.calls == []


def test_contact_must_equal_the_configured_address(make_gateway, fake_session):
    configured, other = _address(), _address()
    sess = fake_session(status=200, body=BODY)
    gw = make_gateway(sess, cfg=_cfg(configured), network="live")
    with pytest.raises(ValueError):
        gw.get_json(P, OP, UNPAY, contact={"email": other})
    assert sess.calls == []


def test_a_live_contact_provider_needs_its_address_before_any_request(make_gateway, fake_session):
    sess = fake_session(status=200, body=BODY)
    gw = make_gateway(sess, cfg=_cfg(_address()), network="live")
    with pytest.raises(ValueError):
        gw.get_json(P, OP, UNPAY)
    assert sess.calls == []


def test_an_off_contact_provider_is_refused_before_any_request(make_gateway, fake_session):
    sess = fake_session(status=200, body=BODY)
    gw = make_gateway(sess)   # no config: unpaywall is off
    with pytest.raises(providers.ProviderRefused):
        gw.get_json(P, OP, UNPAY, contact={"email": _address()})
    assert sess.calls == []
