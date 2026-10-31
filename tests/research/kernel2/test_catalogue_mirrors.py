"""M-C2 t2 (plan 5.2 and review F1): the static mirrors name exactly the catalogue.

- the scite ledger refuses a source it does not know, so every catalogue provider must be one;
- the data policy and the egress table name every catalogue provider;
- an L2 run refuses each new provider unless its policy lists it (M-B, item 9).
Synthetic data; every transport is a fake session.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from scieflow.integrations.scite import ledger
from scieflow.research.lib import providers

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "data-protection.md"

# name -> (catalogue host, rate per second); the same table as test_catalogue.py
NEW = {
    "search-pubmed": ("eutils.ncbi.nlm.nih.gov", 2),
    "search-semantic-scholar": ("api.semanticscholar.org", 0.3),
    "search-datacite": ("api.datacite.org", 5),
    "opencitations": ("api.opencitations.net", 2),
    "unpaywall": ("api.unpaywall.org", 5),
}


def _address():
    return f"contact-{uuid.uuid4().hex[:10]}@example.org"


def test_the_ledger_knows_exactly_the_catalogue():
    assert set(ledger.PROVIDER_SOURCES) == set(providers.CATALOGUE)


@pytest.mark.parametrize("name", list(NEW))
def test_a_gateway_call_writes_a_ledger_row_for_each_new_provider(project, make_gateway,
                                                                  fake_session, name):
    host, _rate = NEW[name]
    address = _address()
    cfg = {"providers": {"search-pubmed": {"enabled": True},
                         "search-semantic-scholar": {"enabled": True},
                         "unpaywall": {"enabled": True, "contact_email": address}}}
    sess = fake_session(status=200, body={"ok": 1})
    gw = make_gateway(sess, cfg=cfg, network="live")
    kwargs = {"contact": {"email": address}} if name == "unpaywall" else {}
    gw.get_json(name, "probe", f"https://{host}/v/probe", **kwargs)
    rows = gw.ledger.rows("run")
    assert [row["source"] for row in rows] == [name]


@pytest.mark.parametrize("name", list(NEW))
def test_an_l2_run_refuses_each_new_provider_unless_its_policy_lists_it(project, make_gateway,
                                                                        fake_session, helpers,
                                                                        name):
    host, _rate = NEW[name]
    helpers.declare_l2()
    gw = make_gateway(fake_session())
    with pytest.raises(providers.ProviderRefused, match="data protection"):
        gw.check(name, f"https://{host}/x")


def test_the_unpaywall_row_says_the_address_is_never_written():
    rows = [line for line in DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `unpaywall`")]
    assert len(rows) == 1
    assert "the address is never written to a file" in rows[0]
