"""M-C2 t3 (plan 5.3, Part B t3 items 3-4, 6): `scieflow research search semantic-scholar`.

Transports are faked by `responses`; the answers are converted researcher recordings. No API key is
read or sent (Semantic Scholar is keyless here), and no e-mail address is in any fixture. The DataCite
tests live in test_search_datacite.py (review r1 F4).
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses
import yaml

from scieflow.research.search import semantic_scholar as s2

S2_URL = r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"


def calls_to(pattern: str) -> list:
    return [c for c in responses.calls if re.match(pattern, c.request.url)]


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


# --- record, replay and miss (plan 5.3 item 6, review r1 F2) ------------------------------------


@responses.activate
def test_a_recorded_semantic_scholar_search_replays_byte_identical_with_zero_transport_calls(
        s2_cli, answer):
    responses.add(responses.GET, re.compile(S2_URL),
                  json=answer("semantic_scholar", "a6742947")["response_body"])
    code, recorded_out, err = s2_cli(["self-supervised ECG", "--workspace", "demo",
                                      "--network", "record"])
    assert code == 0, err
    responses.calls.reset()
    for mode in ("replay", "offline"):
        code, out, err = s2_cli(["self-supervised ECG", "--workspace", "demo", "--network", mode])
        assert code == 0, err
        assert out == recorded_out
        assert len(responses.calls) == 0


@responses.activate
def test_a_semantic_scholar_replay_miss_exits_six_and_sends_nothing(s2_cli):
    code, _out, err = s2_cli(["self-supervised ECG", "--workspace", "demo", "--network", "replay"])
    assert code == 6
    assert len(responses.calls) == 0


# --- an L2 run refuses it before any request (plan 5.3 item 5, M-B data check) ------------------


@responses.activate
def test_semantic_scholar_is_refused_in_an_l2_run_and_sends_nothing(s2_cli, cli_project):
    _root, ws = cli_project
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    code, _out, err = s2_cli(["self-supervised ECG", "--workspace", "demo"])
    assert code == 3
    assert len(responses.calls) == 0
