"""M-C2 t2 (plan 5.2, items 5-7): the five new catalogue entries, their policy and Gateway.usable.

Synthetic configuration only. Every transport is a fake session; nothing is sent.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
import yaml

from scieflow.core.data import policy as data_policy
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.lib import providers

REPO = Path(__file__).resolve().parents[3]

# name -> (catalogue host, rate per second)
NEW = {
    "search-pubmed": ("eutils.ncbi.nlm.nih.gov", 2),
    "search-semantic-scholar": ("api.semanticscholar.org", 0.3),
    "search-datacite": ("api.datacite.org", 5),
    "opencitations": ("api.opencitations.net", 2),
    "unpaywall": ("api.unpaywall.org", 5),
}


def _address():
    return f"contact-{uuid.uuid4().hex[:10]}@example.org"


def _load_cfg(root, cfg):
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return providers.load(root)


def test_the_five_entries_have_their_hosts_and_rates():
    for name, (host, rate) in NEW.items():
        assert providers.CATALOGUE[name]["hosts"] == (host,), name
        assert providers.CATALOGUE[name]["rate_per_s"] == rate, name


def test_with_no_config_only_datacite_and_opencitations_are_on(project):
    root, _ws, _home = project
    pol = providers.load(root)
    assert pol.allows("search-datacite") and pol.allows("opencitations")
    for name in ("search-pubmed", "search-semantic-scholar", "unpaywall"):
        assert not pol.allows(name)
        assert "config/integrations.yml" in pol.reason(name)


def test_a_config_that_omits_them_gives_the_same_answer(project):
    root, _ws, _home = project
    pol = _load_cfg(root, {"providers": {"openalex": {"enabled": True}}})
    assert pol.allows("search-datacite") and pol.allows("opencitations")
    for name in ("search-pubmed", "search-semantic-scholar", "unpaywall"):
        assert not pol.allows(name)
        assert "not listed" in pol.reason(name)


def test_enabling_pubmed_and_semantic_scholar_turns_them_on(project):
    root, _ws, _home = project
    pol = _load_cfg(root, {"providers": {"search-pubmed": {"enabled": True},
                                         "search-semantic-scholar": {"enabled": True}}})
    assert pol.allows("search-pubmed") and pol.allows("search-semantic-scholar")
    assert pol.hosts("search-pubmed") == ("eutils.ncbi.nlm.nih.gov",)


def test_unpaywall_enabled_without_a_contact_is_off_with_the_stated_reason(project):
    root, _ws, _home = project
    pol = _load_cfg(root, {"providers": {"unpaywall": {"enabled": True}}})
    assert not pol.allows("unpaywall")
    assert pol.reason("unpaywall") == "Unpaywall needs contact_email in config/integrations.yml"


def test_the_contact_is_kept_out_of_the_describe_output(project):
    root, _ws, _home = project
    address = _address()
    pol = _load_cfg(root, {"providers": {"unpaywall": {"enabled": True,
                                                       "contact_email": address}}})
    assert pol.allows("unpaywall")
    assert pol.contact("unpaywall") == address
    described = pol.describe()
    assert described["unpaywall"]["contact"] == "set"
    assert address not in json.dumps(described)


def test_an_unset_contact_is_reported_as_unset(project):
    root, _ws, _home = project
    pol = providers.load(root)
    assert pol.contact("unpaywall") is None
    assert pol.describe()["unpaywall"]["contact"] == "unset"


def test_the_contact_is_kept_out_of_the_policy_repr(project):
    # a traceback or a debug print of the policy must not show the address
    root, _ws, _home = project
    address = _address()
    pol = _load_cfg(root, {"providers": {"unpaywall": {"enabled": True,
                                                       "contact_email": address}}})
    assert pol.contact("unpaywall") == address
    assert address not in repr(pol)


@pytest.mark.parametrize("address", ["no-at-sign.example.org", "a@b", "a b@example.org"])
def test_a_malformed_contact_address_is_refused_without_echoing_it(project, address):
    root, _ws, _home = project
    with pytest.raises(PolicyError) as exc:
        _load_cfg(root, {"providers": {"unpaywall": {"enabled": True,
                                                     "contact_email": address}}})
    assert address not in str(exc.value)


def test_contact_email_on_another_provider_is_an_unknown_key(project):
    root, _ws, _home = project
    with pytest.raises(PolicyError, match="unknown key"):
        _load_cfg(root, {"providers": {"openalex": {"enabled": True,
                                                    "contact_email": _address()}}})


def test_the_shipped_example_lists_ten_on_and_the_three_opt_ins_off(project):
    root, _ws, _home = project
    shutil.copy(REPO / "config" / "integrations.example.yml", root / "config" / "integrations.yml")
    pol = providers.load(root)
    assert len(pol.enabled_providers()) == 10
    for name in ("search-datacite", "opencitations"):
        assert pol.allows(name)
    for name in ("search-pubmed", "search-semantic-scholar", "unpaywall"):
        assert not pol.allows(name)
        assert "disabled" in pol.reason(name)


def test_usable_names_why_a_provider_is_off_and_sends_nothing(make_gateway, fake_session):
    sess = fake_session()
    gw = make_gateway(sess)
    reason = gw.usable("search-pubmed")
    assert isinstance(reason, str) and "config/integrations.yml" in reason
    assert gw.usable("search-crossref") is None
    assert sess.calls == []


def test_usable_refuses_a_provider_with_no_allowed_host(make_gateway, fake_session):
    gw = make_gateway(fake_session(), cfg={"providers": {"search-crossref": {"enabled": True,
                                                                             "hosts": []}}})
    reason = gw.usable("search-crossref")
    assert reason and "allowed hosts" in reason


def test_usable_names_the_m_b_refusal_for_an_l2_run_in_an_egress_mode(project, make_gateway,
                                                                       fake_session, helpers):
    _root, _ws, _home = project
    helpers.declare_l2()
    helpers.write_network("live")
    sess = fake_session()
    gw = make_gateway(sess)
    reason = gw.usable("search-datacite")
    assert reason and "data protection" in reason
    assert sess.calls == []


def test_usable_is_none_for_a_provider_the_l2_policy_lists(project, make_gateway, fake_session,
                                                           helpers):
    root, _ws, _home = project
    helpers.declare_l2()
    helpers.write_network("live")
    helpers.write_policy("version: 1\nl2_providers:\n  - name: search-datacite\n"
                         "    reason: metadata only\n")
    assert make_gateway(fake_session()).usable("search-datacite") is None


def test_the_l2_policy_accepts_each_new_name(project):
    root, _ws, _home = project
    text = "version: 1\nl2_providers:\n" + "".join(
        f"  - name: {name}\n    reason: metadata only\n" for name in NEW)
    (root / "config" / "data-policy.yml").write_text(text, encoding="utf-8")
    assert set(data_policy.load(root).l2_providers) == set(NEW)
