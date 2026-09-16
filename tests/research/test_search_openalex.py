import json
import re

import pytest
import requests
import responses

from scieflow.research.search import openalex as search_openalex
from scieflow.research.lib import http, papers

OPENALEX_WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.5555/demo",
    "display_name": "Demo Paper",
    "publication_year": 2023,
    "cited_by_count": 17,
    "primary_location": {"source": {"display_name": "Demo Journal"}},
    "open_access": {"oa_url": "https://example.org/demo.pdf"},
    "abstract_inverted_index": {"Hello": [0], "world": [1]},
}


def test_record_shape():
    r = papers.record(title="t", year=2020)
    assert set(r) == {"doi", "title", "year", "venue", "cited_by", "abstract", "url", "source"}
    assert r["doi"] is None


def test_norm_reconstructs_abstract_and_strips_doi_prefix():
    r = search_openalex.norm(OPENALEX_WORK)
    assert r["doi"] == "10.5555/demo"
    assert r["abstract"] == "Hello world"
    assert r["venue"] == "Demo Journal"
    assert r["source"] == "openalex"


def test_norm_tolerates_missing_fields():
    r = search_openalex.norm({"display_name": "Bare", "publication_year": 2020})
    assert r["title"] == "Bare"
    assert r["venue"] is None and r["abstract"] is None


@responses.activate
def test_search_hits_api_and_emits_json(capsys):
    responses.get(
        re.compile(r"https://api\.openalex\.org/works.*"),
        json={"results": [OPENALEX_WORK]},
    )
    search_openalex.main(["demo query", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["title"] == "Demo Paper"


@responses.activate
def test_http_get_404_fails_fast_no_retry():
    """Non-429 4xx errors should raise immediately without retry."""
    responses.add(
        responses.GET,
        "https://api.example.com/test",
        status=404,
    )
    with pytest.raises(requests.HTTPError):
        http.get("https://api.example.com/test", retries=3)
    # Should have made exactly 1 request, not retried
    assert len(responses.calls) == 1


@responses.activate
def test_http_get_500_retries_on_success():
    """5xx errors should be retried until success."""
    responses.add(
        responses.GET,
        "https://api.example.com/test",
        status=500,
    )
    responses.add(
        responses.GET,
        "https://api.example.com/test",
        status=200,
        json={"data": "success"},
    )
    resp = http.get("https://api.example.com/test", retries=3, backoff=0.01)
    assert resp.status_code == 200
    assert resp.json() == {"data": "success"}
    # Should have made 2 requests: first 500, second 200
    assert len(responses.calls) == 2


@responses.activate
def test_http_get_429_retries_on_success():
    """429 (rate limit) errors should be retried until success."""
    responses.add(
        responses.GET,
        "https://api.example.com/test",
        status=429,
    )
    responses.add(
        responses.GET,
        "https://api.example.com/test",
        status=200,
        json={"data": "success"},
    )
    resp = http.get("https://api.example.com/test", retries=3, backoff=0.01)
    assert resp.status_code == 200
    assert resp.json() == {"data": "success"}
    # Should have made 2 requests: first 429, second 200
    assert len(responses.calls) == 2
