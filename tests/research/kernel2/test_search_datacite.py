"""M-C2 t3 (plan 5.3, Part B t3 items 3-4, 6): `scieflow research search datacite`.

Moved here from test_search_semantic_scholar.py (review r1 F4). Transports are faked by `responses`;
the answers are converted researcher recordings. No API key is read or sent and no e-mail address is
in any fixture.
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

DC_URL = r"https://api\.datacite\.org/dois.*"


def calls_to(pattern: str) -> list:
    return [c for c in responses.calls if re.match(pattern, c.request.url)]


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
    query = parse_qs(urlparse(url).query)["query"][0]
    assert query == "(self\\-supervised ECG) AND publicationYear:[2023 TO *]"


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


# --- record, replay and miss (plan 5.3 item 6, review r1 F2) ------------------------------------


@responses.activate
def test_a_recorded_datacite_search_replays_byte_identical_with_zero_transport_calls(
        dc_cli, answer):
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "82f62bd4")["response_body"])
    code, recorded_out, err = dc_cli(["self-supervised ECG", "--workspace", "demo",
                                      "--network", "record"])
    assert code == 0, err
    responses.calls.reset()
    for mode in ("replay", "offline"):
        code, out, err = dc_cli(["self-supervised ECG", "--workspace", "demo", "--network", mode])
        assert code == 0, err
        assert out == recorded_out
        assert len(responses.calls) == 0


@responses.activate
def test_a_datacite_replay_miss_exits_six_and_sends_nothing(dc_cli):
    code, _out, err = dc_cli(["self-supervised ECG", "--workspace", "demo", "--network", "replay"])
    assert code == 6
    assert len(responses.calls) == 0
