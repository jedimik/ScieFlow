"""M-F t3 (plan 5.7): the `journal-guidelines` Gateway provider whose hosts come from the run.

Mocked HTTP only (`responses`); no network. Synthetic hosts and pages.
"""

import pytest
import responses
import yaml

from scieflow.core.data import policy as data_policy
from scieflow.core.data.policy import KNOWN_PROVIDERS
from scieflow.integrations.scite import ledger
from scieflow.research.lib import http, providers
from scieflow.research.lib.providers import ProviderRefused

GUIDE_HOST = "guides.journal.example"
GUIDE_URL = f"https://{GUIDE_HOST}/authors"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return root, ws


def enable_guidelines(root, rate=1000):
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump({
        "providers": {"journal-guidelines": {"enabled": True, "rate_per_s": rate}}}))


def declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))


def gateway(root, ws, run_hosts=None):
    return providers.Gateway(ws, providers.load(root), sleep=lambda _s: None,
                             run_hosts=run_hosts)


# --- the catalogue entry and its policy ----------------------------------------------------


def test_journal_guidelines_is_off_with_no_config(project):
    root, _ = project
    policy = providers.load(root)
    assert not policy.allows("journal-guidelines")
    assert "off" in (policy.reason("journal-guidelines") or "")


def test_journal_guidelines_is_off_when_unlisted_and_the_search_sources_stay_on(project):
    root, _ = project
    (root / "config" / "integrations.yml").write_text("providers:\n  pubtator: {enabled: true}\n")
    policy = providers.load(root)
    assert not policy.allows("journal-guidelines")
    assert policy.allows("search-openalex") and policy.allows("search-crossref")


def test_enabled_true_switches_it_on_with_no_hosts(project):
    root, _ = project
    enable_guidelines(root)
    policy = providers.load(root)
    assert policy.allows("journal-guidelines")
    assert policy.hosts("journal-guidelines") == ()


def test_a_hosts_key_is_a_load_error(project):
    root, _ = project
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump({"providers": {
        "journal-guidelines": {"enabled": True, "hosts": [GUIDE_HOST]}}}))
    with pytest.raises(providers.PolicyError, match="takes no hosts"):
        providers.load(root)


def test_rate_per_s_must_be_a_positive_number(project):
    root, _ = project
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump({"providers": {
        "journal-guidelines": {"enabled": True, "rate_per_s": 0}}}))
    with pytest.raises(providers.PolicyError, match="rate_per_s"):
        providers.load(root)


def test_the_mirrors_gain_the_provider_name():
    assert "journal-guidelines" in ledger.PROVIDER_SOURCES
    assert "journal-guidelines" in KNOWN_PROVIDERS
    assert "journal-guidelines" in data_policy.KNOWN_PROVIDERS


def test_run_hosts_for_another_provider_is_a_value_error(project):
    root, ws = project
    with pytest.raises(ValueError, match="run_hosts"):
        gateway(root, ws, run_hosts={"search-openalex": (GUIDE_HOST,)})


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.0.0.5", "localhost", "x.localhost", "x.local", "x.internal",
    "guides.lan", "pages.home.arpa", "nodot", "Guides.Example.org",
    "127.1", "0x7f.0.0.1", "0177.0.0.1", "10.1", "1.2.3",
])
def test_unsafe_run_hosts_are_refused(project, host):
    root, ws = project
    enable_guidelines(root)
    with pytest.raises(ProviderRefused):
        gateway(root, ws, run_hosts={"journal-guidelines": (host,)})


# --- every request is checked before it is sent --------------------------------------------


@responses.activate
def test_an_approved_host_passes_and_any_other_host_is_refused_with_no_call(project):
    root, ws = project
    enable_guidelines(root)
    responses.add(responses.GET, GUIDE_URL, body="<html><title>x</title></html>", status=200)
    gw = gateway(root, ws, run_hosts={"journal-guidelines": (GUIDE_HOST,)})
    gw.get_text("journal-guidelines", "page", GUIDE_URL, count=lambda _: 1)
    assert len(responses.calls) == 1
    with pytest.raises(ProviderRefused, match="allowed hosts"):
        gw.get_text("journal-guidelines", "page", "https://other.journal.example/x",
                    count=lambda _: 1)
    assert len(responses.calls) == 1


@responses.activate
def test_a_provider_with_no_run_hosts_refuses_every_request(project):
    root, ws = project
    enable_guidelines(root)
    gw = gateway(root, ws)
    with pytest.raises(ProviderRefused):
        gw.get_text("journal-guidelines", "page", GUIDE_URL, count=lambda _: 1)
    assert len(responses.calls) == 0


@responses.activate
def test_run_hosts_do_not_widen_any_other_provider(project):
    root, ws = project
    enable_guidelines(root)
    gw = gateway(root, ws, run_hosts={"journal-guidelines": (GUIDE_HOST,)})
    with pytest.raises(ProviderRefused):
        gw.get_text("journal-guidelines", "page", "https://api.openalex.org/works",
                    count=lambda _: 1)
    with pytest.raises(ProviderRefused):
        gw.get_json("search-openalex", "search", "https://" + GUIDE_HOST + "/x",
                    count=lambda _: 1)
    assert len(responses.calls) == 0


@pytest.mark.parametrize("url", [
    f"http://{GUIDE_HOST}/authors",
    f"https://{GUIDE_HOST}:8443/authors",
    f"https://user:pw@{GUIDE_HOST}/authors",
])
@responses.activate
def test_http_port_and_userinfo_are_refused(project, url):
    root, ws = project
    enable_guidelines(root)
    gw = gateway(root, ws, run_hosts={"journal-guidelines": (GUIDE_HOST,)})
    with pytest.raises(ProviderRefused):
        gw.get_text("journal-guidelines", "page", url, count=lambda _: 1)
    assert len(responses.calls) == 0


@responses.activate
def test_a_redirect_is_a_failure_and_its_location_is_never_requested(project):
    root, ws = project
    enable_guidelines(root)
    responses.add(responses.GET, GUIDE_URL, status=301,
                  headers={"Location": "https://elsewhere.journal.example/authors"})
    gw = gateway(root, ws, run_hosts={"journal-guidelines": (GUIDE_HOST,)})
    with pytest.raises(http.HTTPFailure) as exc:
        gw.get_text("journal-guidelines", "page", GUIDE_URL, count=lambda _: 1)
    assert exc.value.classification == "HTTP_301"
    assert [c.request.url.split("/")[2] for c in responses.calls] == [GUIDE_HOST]


@responses.activate
def test_an_l2_run_refuses_guidelines_even_for_a_listed_host(project):
    root, ws = project
    enable_guidelines(root)
    declare_l2(ws)
    gw = gateway(root, ws, run_hosts={"journal-guidelines": (GUIDE_HOST,)})
    with pytest.raises(ProviderRefused, match="data protection"):
        gw.get_text("journal-guidelines", "page", GUIDE_URL, count=lambda _: 1)
    assert len(responses.calls) == 0
