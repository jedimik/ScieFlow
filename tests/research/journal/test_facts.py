"""M-F t3 (plan 5.3): deterministic journal facts from OpenAlex and Crossref.

Every HTTP call is mocked (`responses`); the journals, works and publishers are synthetic.
"""

import re
from datetime import date, timedelta

import pytest
import responses

from scieflow.core.review.claims import LeakError
from scieflow.research.journal import facts
from scieflow.research.lib import providers
from scieflow.research.lib.providers import ProviderRefused

SOURCES_URL = "https://api.openalex.org/sources"
WORKS_URL = "https://api.openalex.org/works"
CROSSREF_URL = "https://api.crossref.org/journals/1234-5679"
ISSN = "1234-5679"
AS_OF = date(2026, 10, 1)
WINDOW_START = AS_OF - timedelta(days=730)      # M-F 5.3: the two-year works window


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    run = root / "workspace" / "demo"
    run.mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.chdir(root)
    return run


def gateway(run):
    return providers.Gateway(run, providers.load(run.parents[1]), sleep=lambda _s: None)


def source(**over):
    row = {
        "id": "https://openalex.org/S123456", "display_name": "Journal of Synthetic Neurology",
        "issn_l": ISSN, "issn": [ISSN], "host_organization_name": "Synthetic Press",
        "homepage_url": "https://journal.example/", "is_oa": False, "is_in_doaj": False,
        "apc_usd": None, "works_count": 42, "type": "journal",
    }
    row.update(over)
    return row


def work(refs, abstract_words=None, title="A synthetic study of tremor"):
    index = None
    if abstract_words is not None:
        index = {"w": list(range(abstract_words))}
    return {"id": "https://openalex.org/W1", "publication_year": 2025,
            "referenced_works_count": refs, "abstract_inverted_index": index,
            "display_name": title}


def mock_sources(rows, status=200):
    responses.add(responses.GET, re.compile(re.escape(SOURCES_URL) + r".*"),
                  json={"results": rows} if status == 200 else {}, status=status)


def mock_works(rows, status=200):
    responses.add(responses.GET, re.compile(re.escape(WORKS_URL) + r".*"),
                  json={"results": rows} if status == 200 else {}, status=status)


def mock_crossref(message=None, status=200):
    body = {"message": message} if message is not None else {}
    responses.add(responses.GET, CROSSREF_URL, json=body, status=status)


# --- ISSN, names, selection ------------------------------------------------------------------


def test_the_issn_check_digit_is_verified():
    assert facts.issn_ok("1234-5679")
    assert not facts.issn_ok("1234-5678")
    assert not facts.issn_ok("12345679")


@responses.activate
def test_an_issn_selects_the_one_source_that_lists_it(ws):
    mock_sources([source()])
    found = facts.select_source(gateway(ws), name="Journal of Synthetic Neurology", issn=ISSN)
    assert found["id"] == "S123456" and found["issn_l"] == ISSN


@responses.activate
def test_a_name_with_two_exact_matches_is_ambiguous_and_lists_both(ws):
    mock_sources([source(), source(id="https://openalex.org/S999", issn_l="9876-5432",
                                   issn=["9876-5432"])])
    with pytest.raises(facts.FactsError) as exc:
        facts.select_source(gateway(ws), name="Journal of Synthetic Neurology", issn=None)
    assert exc.value.code == 1
    assert "1234-5679" in str(exc.value) and "9876-5432" in str(exc.value)
    assert "--issn" in str(exc.value)


@responses.activate
def test_an_invalid_issn_is_refused_with_no_call(ws):
    with pytest.raises(facts.FactsError) as exc:
        facts.collect(gateway(ws), name="Journal of Synthetic Neurology", issn="1234-5678",
                      as_of=AS_OF)
    assert exc.value.code == 1
    assert len(responses.calls) == 0


@responses.activate
def test_a_data_shaped_name_is_refused_with_no_call(ws):
    with pytest.raises(LeakError):
        facts.collect(gateway(ws), name="patient 4711 age 63", issn=None, as_of=AS_OF)
    assert len(responses.calls) == 0


@responses.activate
def test_a_works_filter_with_an_unreturned_source_id_is_refused_before_the_request(ws):
    with pytest.raises(ProviderRefused):
        facts.works_sample(gateway(ws), "S424242", AS_OF)
    assert len(responses.calls) == 0


# --- norms -----------------------------------------------------------------------------------


def test_norms_for_nine_works_are_exact():
    works = [work(refs, abstract_words=words, title=title) for refs, words, title in [
        (1, 100, "one two three"), (2, 110, "one two"), (3, 120, "one"),
        (4, 130, "one two three four"), (5, 140, "one two"), (6, 150, "one"),
        (7, 160, "one two three"), (8, 170, "one two"), (9, None, "one two three four five")]]
    norms = facts.norms(works, WINDOW_START, AS_OF)
    assert norms["sample"] == {"n": 9, "from_date": "2024-10-01", "to_date": "2026-10-01"}
    assert norms["references"] == {"median": 5, "p25": 3, "p75": 7}
    assert norms["abstract_words"]["median"] == 135
    assert norms["abstract_words"]["p25"] == 117.5
    assert norms["abstract_words"]["p75"] == 152.5
    assert norms["abstract_words"]["share_with_abstract"] == 0.8889
    assert norms["title_words"]["median"] == 2


def test_norms_for_zero_works_are_null():
    norms = facts.norms([], WINDOW_START, AS_OF)
    assert norms["sample"]["n"] == 0
    assert norms["references"] == {"median": None, "p25": None, "p75": None}
    assert norms["title_words"]["median"] is None


def test_norms_for_one_work_use_its_own_value_for_every_quartile():
    norms = facts.norms([work(12, abstract_words=200, title="a b")], WINDOW_START, AS_OF)
    assert norms["references"] == {"median": 12, "p25": 12, "p75": 12}
    assert norms["abstract_words"]["median"] == 200


@responses.activate
def test_the_norms_sample_records_the_window_it_was_drawn_from(ws):
    mock_sources([source()])
    mock_works([work(r, abstract_words=50) for r in range(1, 4)])
    mock_crossref({"title": "Journal of Synthetic Neurology", "publisher": "Synthetic Press",
                   "ISSN": [ISSN]})
    data = facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert data["norms"]["sample"] == {"n": 3, "from_date": "2024-10-01", "to_date": "2026-10-01"}


@responses.activate
def test_a_source_row_without_a_string_id_is_malformed_and_exits_cleanly(ws):
    row = source()
    del row["id"]
    mock_sources([row])
    with pytest.raises(facts.FactsError, match="MALFORMED"):
        facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)


@responses.activate
def test_a_source_row_whose_issn_is_not_a_list_is_malformed(ws):
    mock_sources([source(issn=7)])
    with pytest.raises(facts.FactsError, match="MALFORMED"):
        facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)


# --- collect: the whole facts block ----------------------------------------------------------


@responses.activate
def test_facts_by_issn_keep_the_fields_norms_and_provider_status(ws):
    mock_sources([source()])
    mock_works([work(r, abstract_words=50) for r in range(1, 10)])
    mock_crossref({"title": "Journal of Synthetic Neurology", "publisher": "Synthetic Press",
                   "ISSN": [ISSN]})
    data = facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert data["version"] == 1 and data["as_of"] == "2026-10-01"
    assert data["openalex"]["id"] == "S123456"
    assert data["openalex"]["host_organization_name"] == "Synthetic Press"
    assert data["crossref"]["publisher"] == "Synthetic Press"
    assert data["conflicts"] == []
    assert data["norms"]["sample"]["n"] == 9
    assert data["provider_status"]["crossref"] == "ok"


@responses.activate
def test_a_crossref_404_is_recorded_and_is_not_fatal(ws):
    mock_sources([source()])
    mock_works([work(3, abstract_words=20)])
    mock_crossref(status=404)
    data = facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert data["crossref"] is None
    assert data["provider_status"]["crossref"] == "HTTP_404"
    assert data["norms"]["sample"]["n"] == 1


@responses.activate
def test_a_publisher_that_differs_between_sources_is_a_conflict_not_a_choice(ws):
    mock_sources([source()])
    mock_works([])
    mock_crossref({"title": "Journal of Synthetic Neurology", "publisher": "Other Press",
                   "ISSN": [ISSN]})
    data = facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert data["conflicts"] == [{"field": "publisher", "openalex": "Synthetic Press",
                                  "crossref": "Other Press"}]


@responses.activate
def test_a_works_failure_leaves_the_norms_null_with_its_status(ws):
    mock_sources([source()])
    mock_works([], status=404)
    mock_crossref({"title": "Journal of Synthetic Neurology", "publisher": "Synthetic Press",
                   "ISSN": [ISSN]})
    data = facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert data["norms"] is None
    assert data["provider_status"]["works"] == "HTTP_404"


@responses.activate
def test_a_failed_source_lookup_is_fatal(ws):
    mock_sources([], status=404)
    with pytest.raises(facts.FactsError) as exc:
        facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF)
    assert exc.value.code == 1


@responses.activate
def test_the_same_recorded_answers_give_byte_identical_facts(ws):
    mock_sources([source()])
    mock_works([work(r, abstract_words=40) for r in range(1, 10)])
    mock_crossref({"title": "Journal of Synthetic Neurology", "publisher": "Synthetic Press",
                   "ISSN": [ISSN]})
    first = facts.dumps(facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF))
    second = facts.dumps(facts.collect(gateway(ws), name=None, issn=ISSN, as_of=AS_OF))
    assert first == second
    assert first.endswith("\n") and '"version": 1' in first
