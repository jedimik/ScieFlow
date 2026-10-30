"""M-C2 t4 (plan 5.4, Part B t4 items 1, 2, 6): the identity connectors and the record parsers.

Every request goes through the Gateway to a faked transport (responses): the answers are the
converted researcher recordings in tests/fixtures/kernel2/identity, and a request that is not
recorded fails the test. No network, no e-mail address, no patient data.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
import responses

from tests.research.kernel2.identity_support import (
    BASELINE_FILE,
    IDENTITY_URL,
    GoldTransport,
    NoTransport,
    canon,
    load_gold_answers,
    split_request,
)
from scieflow._vendor.researcher_core import verify
from scieflow._vendor.researcher_core.model import CSLRecord
from scieflow.core import network
from scieflow.core.review.claims import LeakError
from scieflow.research.lib import records
from scieflow.research.lib.identity import CrossrefIdentity, DataCiteIdentity, OpenAlexIdentity
from scieflow.research.search import registry

BASELINE = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
ANSWERS = load_gold_answers()


def baseline_item(expected: str) -> dict:
    return next(i for i in BASELINE["items"] if i["expected"] == expected)


def recorded_openalex_select(key_prefix: str) -> str:
    """The `select` the researcher recorded for an OpenAlex request with this endpoint prefix."""
    key = next(k for k in ANSWERS if k[0] == "openalex" and k[1].startswith(key_prefix))
    return json.loads(key[2])["select"]


@pytest.fixture
def gold(make_gateway, fast_send):
    """(gateway, transport): every identity request is answered from the gold fixture."""
    transport = GoldTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=transport)
        yield make_gateway(None), transport


# --- connectors: request shapes (items 1, 6) --------------------------------------------------


def test_openalex_doi_lookup_sends_the_recorded_request(gold):
    gw, transport = gold
    doi = baseline_item("verified")["doi"]
    record = asyncio.run(OpenAlexIdentity(gw).resolve_doi(doi))
    assert record is not None and record.DOI == doi
    assert transport.unrecorded == []
    select = recorded_openalex_select("works/doi:")
    assert transport.served[-1] == ("openalex", f"works/doi:{doi}", canon({"select": select}))


def test_crossref_and_datacite_lookups_use_the_recorded_endpoints(gold):
    gw, transport = gold
    doi = baseline_item("verified")["doi"]
    cr = asyncio.run(CrossrefIdentity(gw).resolve_doi(doi))
    assert cr is not None and cr.DOI == doi
    assert transport.served[-1] == ("crossref", f"works/{doi}", canon({}))
    assert asyncio.run(DataCiteIdentity(gw).resolve_doi(doi)) is None   # a Crossref DOI: 404
    assert transport.served[-1] == ("datacite", f"dois/{doi}", canon({}))
    assert transport.unrecorded == []


def test_a_missing_doi_is_a_clean_negative(gold):
    gw, transport = gold
    doi = baseline_item("unresolvable")["doi"]
    for conn in (OpenAlexIdentity(gw), CrossrefIdentity(gw), DataCiteIdentity(gw)):
        assert asyncio.run(conn.resolve_doi(doi)) is None
    assert transport.unrecorded == []


def test_title_search_sends_the_recorded_sanitised_title(gold):
    gw, transport = gold
    key = next(k for k in ANSWERS if k[0] == "openalex" and k[1] == "works" and '"search"' in k[2])
    title = json.loads(key[2])["search"]
    hits = asyncio.run(OpenAlexIdentity(gw).search(title, limit=5))
    assert isinstance(hits, list)
    assert transport.served[-1] == key
    assert transport.unrecorded == []


def test_a_refused_title_is_not_sent_and_names_no_title(make_gateway, fast_send):
    title = ("Interpretable deep learning for automatic diagnosis of 12-lead "
             "electrocardiogram [Anonymized Data]")
    with pytest.raises(LeakError):
        registry.check_query(title)
    gw = make_gateway(None)
    sink = NoTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=sink)
        with pytest.raises(verify.SourceError) as exc:
            asyncio.run(OpenAlexIdentity(gw).search(title))
    assert sink.calls == []
    assert exc.value.kind.value == "config"
    assert "12-lead" not in str(exc.value) and "Anonymized" not in str(exc.value)


@pytest.mark.parametrize(("status", "body", "kind"), [
    (500, "", "server_error"),
    (429, "", "rate_limit"),
    (200, "<html>not json</html>", "bad_response"),
])
def test_http_failures_map_to_source_error_kinds(make_gateway, fast_send, status, body, kind):
    gw = make_gateway(None)
    doi = baseline_item("verified")["doi"]
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, re.compile(r"https://api\.crossref\.org/.*"),
                 status=status, body=body)
        with pytest.raises(verify.SourceError) as exc:
            asyncio.run(CrossrefIdentity(gw).resolve_doi(doi))
    assert exc.value.kind.value == kind


def test_a_network_miss_is_not_a_source_error(make_gateway):
    gw = make_gateway(None, network="replay")
    with pytest.raises(network.NetworkMiss):
        asyncio.run(OpenAlexIdentity(gw).resolve_doi(baseline_item("verified")["doi"]))


def test_a_source_error_at_one_source_never_yields_unresolvable(make_gateway, fast_send):
    """Two sources answer 'no such DOI' and the third fails: never a refusal (vendored rule)."""
    transport = GoldTransport()
    doi = baseline_item("unresolvable")["doi"]

    def route(request):
        source, _, _ = split_request(request.url)
        if source == "datacite":
            return 500, {}, ""
        return transport(request)

    gw = make_gateway(None)
    claim = verify.ReferenceClaim(key=doi, title="", doi=doi)   # no title: no search is sent
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=route)
        conns = [OpenAlexIdentity(gw), CrossrefIdentity(gw), DataCiteIdentity(gw)]
        entry = asyncio.run(verify.verify_claim_async(
            claim, conns, check_status=False, check_accessibility=False))
    assert entry.verdict not in ("unresolvable", "mismatch")
    assert entry.outcomes[2].outcome == "source_error"


# --- parsers (item 2) -------------------------------------------------------------------------


def test_openalex_record_reads_the_recorded_work():
    doi = baseline_item("verified")["doi"]
    key = ("openalex", f"works/doi:{doi}", canon({"select": recorded_openalex_select("works/doi:")}))
    rec = records.openalex_record(ANSWERS[key][1])
    assert isinstance(rec, CSLRecord)
    assert rec.DOI == doi and rec.title
    assert rec.year is not None and rec.author and rec.author[0].surname
    assert rec.arxiv_id == ""


def test_openalex_arxiv_doi_gives_the_arxiv_id_and_a_preprint():
    body = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.48550/arXiv.2101.00001",
            "title": "A preprint", "publication_year": 2021, "type": "preprint",
            "authorships": [{"author": {"display_name": "Ann Smith"}}]}
    rec = records.openalex_record(body)
    assert rec.arxiv_id == "2101.00001"
    assert verify._is_preprint(rec)


def test_crossref_posted_content_is_a_preprint():
    rec = records.crossref_record({
        "DOI": "10.1101/2020.01.01.000000", "type": "posted-content", "title": ["A title"],
        "author": [{"given": "Ann", "family": "Smith"}], "issued": {"date-parts": [[2020]]}})
    assert rec.extra.get("crossref_type") == "posted-content"
    assert rec.year == 2020
    assert [a.surname.casefold() for a in rec.author] == ["smith"]
    assert verify._is_preprint(rec)


def test_datacite_record_reads_the_recorded_item():
    doi = baseline_item("inconclusive")["doi"]
    body = ANSWERS[("datacite", f"dois/{doi}", canon({}))][1]
    rec = records.datacite_record(body["data"])
    assert rec.DOI == doi and rec.title and rec.year is not None


def test_datacite_full_name_in_family_is_split():
    rec = records.datacite_record({"id": "10.5281/zenodo.1", "attributes": {
        "doi": "10.5281/zenodo.1", "titles": [{"title": "Software"}], "publicationYear": 2020,
        "creators": [{"name": "Ines Montani", "familyName": "Ines Montani",
                      "nameType": "Personal"}]}})
    assert rec.author[0].surname.casefold() == "montani"


# --- identifiers that are not plain DOIs are never sent (M-C2 t4 review r1, findings 1 and 4) --

#: DOI-looking text from a bibliography that would put data into a URL path, a query or a fragment,
#: or that is free text. Each must send nothing and resolve to None.
HOSTILE_DOIS = [
    "10.1234/home/jsmith/patients_2024.csv",   # path-shaped: a file name
    "10.1234/John Smith born 1960-01-01",     # spaced free text
    "10.1234/x?email=a@b.c",                  # a query string
    "10.1234/x#fragment",                     # a fragment
    "10.1234/x/../y",                         # a traversal segment
]


@pytest.mark.parametrize("conn_cls", [OpenAlexIdentity, CrossrefIdentity, DataCiteIdentity])
@pytest.mark.parametrize("doi", HOSTILE_DOIS)
def test_a_doi_that_is_not_a_plain_doi_is_never_sent(make_gateway, fast_send, conn_cls, doi):
    """Not sent, and not a clean negative either: a source that was not asked has not answered."""
    gw = make_gateway(None)
    sink = NoTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=sink)
        with pytest.raises(verify.SourceError) as exc:
            asyncio.run(conn_cls(gw).resolve_doi(doi))
        assert exc.value.kind.value == "config"
        with pytest.raises(verify.SourceError):
            asyncio.run(conn_cls(gw).get_by_id(doi))
    assert sink.calls == []
    assert "patients" not in str(exc.value) and "John" not in str(exc.value)


@pytest.mark.parametrize("conn_cls", [OpenAlexIdentity, CrossrefIdentity, DataCiteIdentity])
@pytest.mark.parametrize("text", ["../../etc/passwd", "2101.00001 extra", "hep-th/9901001?x=1",
                                  "{injected}", "2101.000"])
def test_a_non_arxiv_identifier_is_not_sent(make_gateway, fast_send, conn_cls, text):
    gw = make_gateway(None)
    sink = NoTransport()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, IDENTITY_URL, callback=sink)
        with pytest.raises(verify.SourceError) as exc:
            asyncio.run(conn_cls(gw).get_by_id(text))
    assert exc.value.kind.value == "config"
    assert sink.calls == []


def test_an_arxiv_id_goes_to_crossref_as_its_plain_path_and_a_miss_is_none(make_gateway, fast_send):
    """Researcher parity (M-C2 5.4): Crossref treats an arXiv id as a DOI path; a 404 is no record."""
    gw = make_gateway(None)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, re.compile(r"https://api\.crossref\.org/works/.*"), status=404)
        assert asyncio.run(CrossrefIdentity(gw).get_by_id("arXiv:2101.00001")) is None
        sent = [c.request.url.split("?")[0] for c in rsps.calls]
    assert sent == ["https://api.crossref.org/works/2101.00001"]


def test_a_search_404_is_a_source_error_not_a_clean_negative(make_gateway, fast_send):
    gw = make_gateway(None)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, re.compile(r"https://api\.openalex\.org/works.*"), status=404)
        with pytest.raises(verify.SourceError) as exc:
            asyncio.run(OpenAlexIdentity(gw).search("Deep learning for diagnosis"))
    assert exc.value.kind.value == "bad_response"
