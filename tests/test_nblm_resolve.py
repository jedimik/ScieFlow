"""Resolver tests. Offline: every Europe PMC response is a recorded fixture."""
import io
import json

import pytest

from nblm import policy, resolve

PROFILE = policy.Profile(
    name="default",
    session_state="/nonexistent/state.json",
    notebook_prefix="scieflow-",
    allowed_ops=["resolve", "fetch"],
    source_hosts=["europepmc.org", "www.ebi.ac.uk", "arxiv.org"],
    limits={"max_sources_per_notebook": 50, "max_source_mb": 50,
            "max_questions_per_run": 40, "max_questions_per_day": 45,
            "max_claims_per_question": 5, "reask_threshold": "partial"},
)


def result(full_text_urls=None, title="A Paper"):
    body = {"resultList": {"result": [{"title": title, "doi": "10.1000/xyz"}]}}
    if full_text_urls is not None:
        body["resultList"]["result"][0]["fullTextUrlList"] = {
            "fullTextUrl": full_text_urls}
    return body


def opener_for(*payloads):
    seen = []
    queue = list(payloads)

    def opener(url):
        seen.append(url)
        body = queue.pop(0) if queue else {"resultList": {"result": []}}
        stream = io.BytesIO(json.dumps(body).encode())
        stream.__enter__ = lambda: stream
        stream.__exit__ = lambda *exc: False
        return stream

    return opener, seen


PMC_PDF = {"documentStyle": "pdf", "availability": "Open access",
           "url": "https://europepmc.org/articles/PMC1/?pdf=render"}
PUBLISHER_PDF = {"documentStyle": "pdf", "availability": "Subscription",
                 "url": "https://www.sciencedirect.com/science/article/pii/X.pdf"}
HTML_ONLY = {"documentStyle": "html", "availability": "Open access",
             "url": "https://europepmc.org/article/MED/1"}


def test_query_url_is_an_exact_doi_lookup_on_an_allowlisted_host():
    url = resolve.query_url("10.1000/xyz")
    assert "DOI%3A%2210.1000%2Fxyz%22" in url
    assert url.startswith("https://www.ebi.ac.uk/")
    policy.check_source_url(PROFILE, url)


def test_resolves_a_pmc_pdf():
    opener, seen = opener_for(result([PMC_PDF]))
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert row["url"] == PMC_PDF["url"]
    assert row["title"] == "A Paper" and row["source"] == "europepmc"
    assert len(seen) == 1


def test_prefers_an_allowlisted_host_over_the_last_entry():
    """The bug in search_europepmc.py: no break, so the last PDF wins."""
    opener, _ = opener_for(result([PMC_PDF, PUBLISHER_PDF]))
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert row["url"] == PMC_PDF["url"]


def test_refuses_when_the_only_pdf_is_off_allowlist():
    opener, _ = opener_for(result([PUBLISHER_PDF]))
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert "url" not in row
    assert "sciencedirect.com" in row["reason"]


def test_reports_html_only_and_no_full_text_distinctly():
    opener, _ = opener_for(result([HTML_ONLY]), result(None))
    assert "no PDF url" in resolve.resolve_one(PROFILE, "10.1/a", opener=opener)["reason"]
    assert "no open-access full text" in \
        resolve.resolve_one(PROFILE, "10.1/b", opener=opener)["reason"]


def test_reports_a_doi_absent_from_europepmc():
    opener, _ = opener_for({"resultList": {"result": []}})
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert row["reason"] == "not found in Europe PMC"


def test_upgrades_http_to_https_only_for_an_allowlisted_host():
    insecure = {**PMC_PDF, "url": "http://europepmc.org/articles/PMC1/?pdf=render"}
    opener, _ = opener_for(result([insecure]))
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert row["url"].startswith("https://europepmc.org/")
    assert row["scheme_upgraded"] is True
    policy.check_source_url(PROFILE, row["url"])      # now fetchable


def test_never_upgrades_a_foreign_host():
    foreign = {**PMC_PDF, "url": "http://evil.example/paper.pdf"}
    opener, _ = opener_for(result([foreign]))
    row = resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)
    assert "url" not in row and "evil.example" in row["reason"]


def test_network_failure_is_reported_not_guessed():
    def opener(url):
        raise OSError("connection reset")

    with pytest.raises(resolve.ResolveError, match="connection reset"):
        resolve.resolve_one(PROFILE, "10.1000/xyz", opener=opener)


def test_resolve_all_keeps_order_reasons_and_is_polite():
    opener, seen = opener_for(result([PMC_PDF]), result([PUBLISHER_PDF]))
    slept = []
    out = resolve.resolve_all(PROFILE, ["10.1000/aaa", "10.1000/bbb"],
                              opener=opener, sleep=slept.append)
    assert [r["doi"] for r in out["sources"]] == ["10.1000/aaa"]
    assert [r["doi"] for r in out["unresolved"]] == ["10.1000/bbb"]
    assert slept == [resolve.POLITE_DELAY]          # one delay between two calls
    assert len(seen) == 2


def test_resolve_all_rejects_a_malformed_doi_without_calling_out():
    opener, seen = opener_for()
    out = resolve.resolve_all(PROFILE, ["not-a-doi"], opener=opener,
                              sleep=lambda _s: None)
    assert out["sources"] == []
    assert "not a DOI" in out["unresolved"][0]["reason"]
    assert seen == []
