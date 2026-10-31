"""M-C2 t3 (plan 5.3 fan-out, M-C2-D7; Part B t3 item 5): `scieflow research search all` with the new
sources. Transports are faked by `responses`; the answers are converted researcher recordings
(PubMed, Semantic Scholar, DataCite) and small synthetic M-C1 answers. No real service is reached.
"""

from __future__ import annotations

import json
import re

import pytest
import responses
import yaml

from scieflow.research.lib import providers
from scieflow.research.search import fanout

M1_URLS = {
    "openalex": "https://api.openalex.org/works",
    "crossref": "https://api.crossref.org/works",
    "europepmc": "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
    "arxiv": "https://export.arxiv.org/api/query",
}
PUBMED_ESEARCH = r"https://eutils\.ncbi\.nlm\.nih\.gov/entrez/eutils/esearch\.fcgi.*"
PUBMED_EFETCH = r"https://eutils\.ncbi\.nlm\.nih\.gov/entrez/eutils/efetch\.fcgi.*"
S2_URL = r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"
DC_URL = r"https://api\.datacite\.org/dois.*"
QUERY = "self-supervised ECG"
ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2401.00001v2</id><title>Arxiv Demo Paper</title>
  <summary>An abstract.</summary><published>2024-01-02T00:00:00Z</published></entry>
</feed>"""


def urls_called(prefix: str) -> list:
    return [c for c in responses.calls if re.match(prefix, c.request.url)]


def serve_all(cli_answer, *, fail: dict | None = None) -> None:
    """Register the M-C1 four answers (`fail` maps a source to the HTTP status it answers with)."""
    fail = fail or {}
    if "openalex" in fail:
        responses.add(responses.GET, re.compile(re.escape(M1_URLS["openalex"]) + ".*"),
                      status=fail["openalex"])
    else:
        responses.add(responses.GET, re.compile(re.escape(M1_URLS["openalex"]) + ".*"),
                      json={"results": [{"display_name": "Alpha study", "publication_year": 2023,
                                         "doi": "https://doi.org/10.5555/alpha"}]})
    responses.add(responses.GET, re.compile(re.escape(M1_URLS["crossref"]) + ".*"),
                  json={"message": {"items": [{"DOI": "10.5555/beta", "title": ["Beta study"],
                                               "published": {"date-parts": [[2022]]}}]}})
    responses.add(responses.GET, re.compile(re.escape(M1_URLS["europepmc"]) + ".*"),
                  json={"resultList": {"result": [{"title": "Gamma study", "pubYear": "2021",
                                                   "doi": "10.5555/gamma"}]}})
    responses.add(responses.GET, re.compile(re.escape(M1_URLS["arxiv"]) + ".*"),
                  body=ATOM, content_type="application/atom+xml")


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


@pytest.fixture
def run(capsys):
    def go(argv):
        try:
            fanout.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out) if captured.out.strip().startswith("{") else None
        return code, payload, captured.out, captured.err

    return go


def write_integrations(root, providers_cfg):
    (root / "config" / "integrations.yml").write_text(
        yaml.safe_dump({"providers": providers_cfg}), encoding="utf-8")


def statuses(payload) -> list:
    return [(s["source"], s["status"]) for s in payload["sources"]]


# --- 1. the catalogue of sources (D7) ------------------------------------------------------


def test_the_canonical_order_has_seven_sources():
    assert fanout.ORDER == ("openalex", "crossref", "europepmc", "arxiv",
                            "pubmed", "semantic-scholar", "datacite")


def test_by_default_only_the_four_m_c1_sources_are_asked(project):
    root, _ws = project
    policy = providers.load(root)
    assert fanout.default_sources(policy) == ("openalex", "crossref", "europepmc", "arxiv")


def test_switching_pubmed_on_adds_it_to_the_default_set(project):
    root, _ws = project
    write_integrations(root, {"search-pubmed": {"enabled": True}})
    policy = providers.load(root)
    assert fanout.default_sources(policy) == ("openalex", "crossref", "europepmc", "arxiv",
                                              "pubmed")


def test_datacite_is_never_in_the_default_set_even_when_on(project):
    root, _ws = project
    policy = providers.load(root)
    assert "datacite" not in fanout.default_sources(policy)


# --- 2. the default run is M-C1's run (plan 5.3 item 5) ------------------------------------


@responses.activate
def test_a_default_search_all_prints_exactly_the_four_m_c1_statuses(project, run, fast_send):
    serve_all(None)
    code, payload, _out, err = run([QUERY, "--workspace", "demo"])
    assert code == 0, err
    assert [s for s, _ in statuses(payload)] == ["openalex", "crossref", "europepmc", "arxiv"]
    assert urls_called(PUBMED_ESEARCH) == [] and urls_called(S2_URL) == []
    assert urls_called(DC_URL) == []


@responses.activate
def test_pubmed_and_semantic_scholar_join_when_switched_on(project, run, fast_send, answer,
                                                           xml_of):
    root, _ws = project
    write_integrations(root, {"search-pubmed": {"enabled": True},
                              "search-semantic-scholar": {"enabled": True}})
    serve_all(None)
    responses.add(responses.GET, re.compile(PUBMED_ESEARCH),
                  json=answer("pubmed", "22b8c990")["response_body"])
    responses.add(responses.GET, re.compile(PUBMED_EFETCH),
                  body=xml_of(answer("pubmed", "73b72d5e")["response_body"]["articles"]),
                  content_type="text/xml")
    responses.add(responses.GET, re.compile(S2_URL),
                  json=answer("semantic_scholar", "a6742947")["response_body"])
    code, payload, _out, err = run([QUERY, "--workspace", "demo"])
    assert code == 0, err
    assert statuses(payload) == [("openalex", "ok"), ("crossref", "ok"), ("europepmc", "ok"),
                                 ("arxiv", "ok"), ("pubmed", "ok"), ("semantic-scholar", "ok")]


@responses.activate
def test_a_named_switched_off_source_shows_off_and_exits_three(project, run, fast_send):
    serve_all(None)
    code, payload, _out, _err = run([QUERY, "--workspace", "demo", "--sources", "pubmed,openalex"])
    assert code == 0
    assert statuses(payload) == [("openalex", "ok"), ("pubmed", "off")]
    assert urls_called(PUBMED_ESEARCH) == []


@responses.activate
def test_a_named_switched_off_source_alone_exits_three(project, run, fast_send):
    code, payload, _out, _err = run([QUERY, "--workspace", "demo", "--sources", "pubmed"])
    assert code == 3
    assert statuses(payload) == [("pubmed", "off")]


# --- 3. datacite only when named; order and determinism (plan 5.3 item 5) -------------------


@responses.activate
def test_datacite_is_asked_only_when_named(project, run, fast_send, answer):
    serve_all(None)
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "82f62bd4")["response_body"])
    code, payload, _out, err = run([QUERY, "--workspace", "demo", "--sources", "datacite,openalex"])
    assert code == 0, err
    assert statuses(payload) == [("openalex", "ok"), ("datacite", "ok")]


@responses.activate
def test_output_follows_the_canonical_order_whatever_the_request_order(project, run, fast_send,
                                                                       answer):
    serve_all(None)
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "82f62bd4")["response_body"])
    code, payload, _out, _err = run([QUERY, "--workspace", "demo",
                                     "--sources", "datacite,arxiv,openalex"])
    assert code == 0
    assert [s for s, _ in statuses(payload)] == ["openalex", "arxiv", "datacite"]


@responses.activate
def test_the_same_snapshots_give_byte_identical_output_in_any_source_order(project, run,
                                                                           fast_send, answer):
    serve_all(None)
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "82f62bd4")["response_body"])
    _c1, _p1, first, _e1 = run([QUERY, "--workspace", "demo", "--sources", "datacite,openalex"])
    _c2, _p2, second, _e2 = run([QUERY, "--workspace", "demo", "--sources", "openalex,datacite"])
    assert first == second


@responses.activate
def test_one_source_raising_leaves_the_others_ok(project, run, fast_send, answer):
    serve_all(None, fail={"openalex": 500})
    responses.add(responses.GET, re.compile(DC_URL),
                  json=answer("datacite", "82f62bd4")["response_body"])
    code, payload, _out, _err = run([QUERY, "--workspace", "demo", "--sources", "openalex,datacite"])
    assert code == 0
    assert statuses(payload) == [("openalex", "failed"), ("datacite", "ok")]


def test_an_unknown_source_name_is_still_a_usage_error(project, run):
    code, _payload, _out, err = run([QUERY, "--workspace", "demo", "--sources", "pubmd"])
    assert code == 2
    assert "unknown source" in err


# --- 4. an L2 run refuses listed sources that are not in l2_providers (M-B data check) -------


@responses.activate
def test_an_l2_run_refuses_a_listed_pubmed_not_in_l2_providers(project, run, fast_send):
    root, ws = project
    write_integrations(root, {"search-pubmed": {"enabled": True}})
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    serve_all(None)
    code, payload, _out, _err = run([QUERY, "--workspace", "demo", "--sources", "pubmed,openalex"])
    assert code == 3
    assert statuses(payload) == [("openalex", "refused"), ("pubmed", "refused")]
    assert urls_called(PUBMED_ESEARCH) == []


@responses.activate
def test_an_l2_run_refuses_a_listed_semantic_scholar_not_in_l2_providers(project, run, fast_send):
    root, ws = project
    write_integrations(root, {"search-semantic-scholar": {"enabled": True}})
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    serve_all(None)
    code, payload, _out, _err = run([QUERY, "--workspace", "demo", "--sources",
                                     "semantic-scholar,openalex"])
    assert code == 3
    assert statuses(payload) == [("openalex", "refused"), ("semantic-scholar", "refused")]
    assert urls_called(S2_URL) == []
    assert len(responses.calls) == 0


@responses.activate
def test_an_l2_run_refuses_pubmed_and_semantic_scholar_through_the_default_set(project, run,
                                                                              fast_send):
    # No --sources: both switched-on sources join the default set and are refused by the M-B
    # data check before any request; the four M-C1 sources are refused the same way.
    root, ws = project
    write_integrations(root, {"search-pubmed": {"enabled": True},
                              "search-semantic-scholar": {"enabled": True}})
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    serve_all(None)
    code, payload, _out, _err = run([QUERY, "--workspace", "demo"])
    assert code == 3
    assert statuses(payload) == [("openalex", "refused"), ("crossref", "refused"),
                                 ("europepmc", "refused"), ("arxiv", "refused"),
                                 ("pubmed", "refused"), ("semantic-scholar", "refused")]
    assert urls_called(PUBMED_ESEARCH) == [] and urls_called(S2_URL) == []
    assert len(responses.calls) == 0
