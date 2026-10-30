"""M-C2 t3 (plan 5.3, Part B t3 items 3-4, 6): `scieflow research search semantic-scholar` and `datacite`.

Transports are faked by `responses`; the answers are converted researcher recordings. No API key is
read or sent (Semantic Scholar is keyless here), and no e-mail address is in any fixture.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from scieflow._vendor.researcher_core.query import escape_lucene
from scieflow.core.review.claims import LeakError
from scieflow.research.lib import providers
from scieflow.research.search import datacite, registry
from scieflow.research.search import semantic_scholar as s2

S2_URL = r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"
DC_URL = r"https://api\.datacite\.org/dois.*"


def calls_to(pattern: str) -> list:
    return [c for c in responses.calls if re.match(pattern, c.request.url)]


# --- Semantic Scholar ----------------------------------------------------------------------


@pytest.fixture
def s2_cli(cli_project, fast_send, cli_run):
    """The Semantic Scholar command, switched on in config/integrations.yml (off by default, D16)."""
    root, _ws = cli_project
    (root / "config" / "integrations.yml").write_text(
        "providers:\n  search-semantic-scholar:\n    enabled: true\n", encoding="utf-8")

    def run(argv):
        return cli_run(s2.main, argv)

    return run


@responses.activate
def test_semantic_scholar_parses_the_recorded_search(s2_cli, answer, monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "sentinel-key-123")
    body = answer("semantic_scholar", "a6742947")["response_body"]
    responses.add(responses.GET, re.compile(S2_URL), json=body)
    code, out, err = s2_cli(["self-supervised ECG", "--limit", "5"])
    assert code == 0, err
    records = json.loads(out)
    items = body["data"]
    assert len(records) == len(items)
    first = items[0]
    assert records[0]["title"] == first["title"]
    assert records[0]["year"] == first["year"]
    assert records[0]["cited_by"] == first["citationCount"]
    assert records[0]["doi"] == (first.get("externalIds") or {}).get("DOI")
    assert records[0]["source"] == "semantic-scholar"
    assert records[0]["signals"]["semantic_scholar"] == first["paperId"]
    url = calls_to(S2_URL)[0].request.url
    assert "sentinel-key-123" not in url
    assert "api_key" not in url and "email" not in url
    fields = parse_qs(urlparse(url).query)["fields"][0].split(",")
    assert "abstract" not in fields and "title" in fields


@responses.activate
def test_semantic_scholar_year_is_the_open_ended_range_in_the_request(s2_cli, answer):
    responses.add(responses.GET, re.compile(S2_URL),
                  json=answer("semantic_scholar", "a6742947")["response_body"])
    code, _out, err = s2_cli(["self-supervised ECG", "--from-year", "2020"])
    assert code == 0, err
    assert "year=2020-" in calls_to(S2_URL)[0].request.url


@responses.activate
def test_semantic_scholar_without_data_is_a_clean_negative(s2_cli, answer):
    responses.add(responses.GET, re.compile(S2_URL),
                  json=answer("semantic_scholar", "a97002ca")["response_body"])
    code, out, err = s2_cli(["zzzqqxxvv nonexistent phrase"])
    assert code == 0, err
    assert json.loads(out) == []


@responses.activate
def test_semantic_scholar_is_off_by_default_and_sends_nothing(cli_project, fast_send, cli_run):
    code, _out, err = cli_run(s2.main, ["self-supervised ECG"])
    assert code == 3
    assert "off" in err
    assert len(responses.calls) == 0


@responses.activate
def test_semantic_scholar_refuses_a_path_before_any_request(s2_cli):
    code, _out, err = s2_cli(["RBD patient records C:/data/rbd.csv"])
    assert code == 3
    assert len(responses.calls) == 0


# --- DataCite ------------------------------------------------------------------------------


@pytest.fixture
def dc_cli(cli_project, fast_send, cli_run):
    def run(argv):
        return cli_run(datacite.main, argv)

    return run


def test_the_datacite_query_escapes_then_wraps_in_code():
    checked = registry.check_query("self-supervised ECG")
    plain = registry.datacite_query(checked)
    dated = registry.datacite_query(checked, 2023)
    assert str(plain) == escape_lucene("self-supervised ECG")
    assert str(plain) == "self\\-supervised ECG"
    assert str(dated) == "(self\\-supervised ECG) AND publicationYear:[2023 TO *]"


def test_datacite_reads_slashes_as_spaces_before_escaping():
    # The leak check refuses "/" today (only letters, digits, spaces and listed punctuation pass),
    # so the composition is defence in depth: a checked text that carries a slash still escapes as
    # the vendored composition does.
    checked = providers._mint(providers.Checked, "CRISPR genome/editing: review")
    assert str(registry.datacite_query(checked)) == escape_lucene("CRISPR genome editing: review")


def test_the_check_refuses_a_slash_before_datacite_sees_it():
    with pytest.raises(LeakError):
        registry.check_query("CRISPR genome/editing")


@responses.activate
def test_datacite_parses_the_recorded_search(dc_cli, answer):
    body = answer("datacite", "82f62bd4")["response_body"]
    responses.add(responses.GET, re.compile(DC_URL), json=body)
    code, out, err = dc_cli(["self-supervised ECG"])
    assert code == 0, err
    records = json.loads(out)
    assert len(records) == len(body["data"])
    for record, item in zip(records, body["data"]):
        attrs = item.get("attributes") or {}
        assert record["doi"].lower() == (attrs.get("doi") or item["id"]).lower()
        assert record["year"] == attrs.get("publicationYear")
        assert record["source"] == "datacite"


@responses.activate
def test_datacite_from_year_is_composed_in_the_request(dc_cli, answer):
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "0528fda1")["response_body"])
    code, _out, err = dc_cli(["self-supervised ECG", "--from-year", "2023"])
    assert code == 0, err
    url = calls_to(DC_URL)[0].request.url
    assert "publicationYear" in url


@responses.activate
def test_datacite_failure_is_exit_one(dc_cli):
    responses.add(responses.GET, re.compile(DC_URL), status=500)
    code, _out, err = dc_cli(["self-supervised ECG"])
    assert code == 1


@responses.activate
def test_datacite_refuses_a_patient_number_before_any_request(dc_cli):
    code, _out, err = dc_cli(["patient 0471 records"])
    assert code == 3
    assert len(responses.calls) == 0


def test_datacite_refusal_is_a_leak_error_in_the_query_check():
    with pytest.raises(LeakError):
        registry.check_query("patient 0471 records")
