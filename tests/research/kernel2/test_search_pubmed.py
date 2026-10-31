"""M-C2 t3 (plan 5.3, Part B t3 items 1-2, 4, 6): `scieflow research search pubmed`.

Every transport is faked by the `responses` library: converted researcher answers (synthetic
or published bibliographic metadata, no patient data), no real service is reached.
"""

from __future__ import annotations

import json
import re

import pytest
import responses

from scieflow.core.review.claims import LeakError
from scieflow.research.lib import http, providers
from scieflow.research.search import pubmed, registry

ESEARCH = r"https://eutils\.ncbi\.nlm\.nih\.gov/entrez/eutils/esearch\.fcgi.*"
EFETCH = r"https://eutils\.ncbi\.nlm\.nih\.gov/entrez/eutils/efetch\.fcgi.*"


def serve(recorded_esearch: dict, xml: str | None = None) -> None:
    responses.add(responses.GET, re.compile(ESEARCH),
                  json=recorded_esearch["response_body"])
    if xml is not None:
        responses.add(responses.GET, re.compile(EFETCH), body=xml,
                      content_type="text/xml")


def calls_to(pattern: str) -> list:
    return [c for c in responses.calls if re.match(pattern, c.request.url)]


# --- 1. the query check (M-C2-D6, plan 5.3) -------------------------------------------------


@pytest.mark.parametrize("query", [
    '"REM sleep behavior disorder"[tiab] AND (alpha-synuclein[mh] OR SNCA[tiab])',
    "parkinson*[tiab]",
    "RBD[TIAB] NOT review[pt]",
])
def test_a_tagged_query_is_sent_byte_for_byte(query):
    checked = registry.check_pubmed_query(query)
    assert isinstance(checked, providers.Checked)
    assert str(checked) == query


@pytest.mark.parametrize("query, repaired", [
    ('"REM sleep behavior disorder"[tiab] AND (alpha-synuclein[tiab]',
     '"REM sleep behavior disorder"[tiab] AND alpha-synuclein[tiab]'),
    ('REM sleep behavior disorder"[tiab]', "REM sleep behavior disorder[tiab]"),
    ("RBD[tiab]\\", "RBD[tiab]"),
])
def test_syntax_damage_is_repaired_before_the_check(query, repaired):
    assert str(registry.check_pubmed_query(query)) == repaired


@pytest.mark.parametrize("query", [
    "RBD[xx]",                       # a PubMed field tag that is not on the closed list
    "RBD[dp] AND 2020",              # a date tag written in the query; dates are composed in code
    "patient 0471[tiab]",            # a data-shaped number, even with a closed tag
    "RBD patient records C:/data/rbd.csv",  # a path
])
def test_refused_queries_raise_before_anything_is_built(query):
    with pytest.raises(LeakError):
        registry.check_pubmed_query(query)


def test_the_term_composes_the_year_in_code_only():
    checked = registry.check_pubmed_query("RBD[TIAB] NOT review[pt]")
    plain = registry.pubmed_term(checked)
    dated = registry.pubmed_term(checked, 2020)
    assert isinstance(plain, providers.Composed)
    assert str(plain) == "RBD[TIAB] NOT review[pt]"
    assert str(dated) == "(RBD[TIAB] NOT review[pt]) AND 2020:3000[dp]"


def test_the_term_needs_a_checked_query():
    with pytest.raises(TypeError):
        registry.pubmed_term("RBD[TIAB]")


# --- 2. esearch then efetch (plan 5.3 item 2) ---------------------------------------------


@pytest.fixture
def cli(cli_project, fast_send, cli_run):
    """The PubMed command with the source switched on in config/integrations.yml (D16: off by
    default, so every transport test opts in explicitly)."""
    root, _ws = cli_project
    (root / "config" / "integrations.yml").write_text(
        "providers:\n  search-pubmed:\n    enabled: true\n", encoding="utf-8")

    def run(argv):
        return cli_run(pubmed.main, argv)

    return run


@responses.activate
def test_esearch_then_efetch_gives_one_record_per_article(cli, answer, xml_of):
    esearch = answer("pubmed", "22b8c990")
    efetch = answer("pubmed", "73b72d5e")
    serve(esearch, xml_of(efetch["response_body"]["articles"]))
    code, out, err = cli(["self-supervised ECG", "--limit", "10"])
    assert code == 0, err
    records = json.loads(out)
    articles = efetch["response_body"]["articles"]
    ids = esearch["response_body"]["esearchresult"]["idlist"]
    assert len(records) == len(articles)
    pmids = [r["signals"]["pubmed"] for r in records]
    assert pmids == sorted(pmids, key=ids.index), "records follow the esearch order"
    by_pmid = {a["pmid"]: a for a in articles}
    for record in records:
        article = by_pmid[record["signals"]["pubmed"]]
        assert record["source"] == "pubmed"
        assert record["title"] == article["title"]
        assert record["doi"] == article["doi"]
        assert record["year"] == article["year"]
        assert record["venue"] == article["journal"]
        assert record["abstract"] == article["abstract"]
        assert record["url"] == f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/"


@responses.activate
def test_efetch_is_asked_only_for_the_ids_esearch_returned(cli, answer, xml_of):
    esearch = answer("pubmed", "3f2134ab")
    serve(esearch, xml_of(answer("pubmed", "81f4acba")["response_body"]["articles"]))
    code, _out, err = cli(["protein structure prediction"])
    assert code == 0, err
    efetch_calls = calls_to(EFETCH)
    assert len(efetch_calls) == 1
    asked = efetch_calls[0].request.url.split("id=")[1].split("&")[0]
    assert asked == "34265844"


@responses.activate
def test_zero_hits_is_a_clean_negative_with_no_efetch(cli, answer):
    serve(answer("pubmed", "12945908"))
    code, out, err = cli(["zzzqqxxvv nonexistent phrase"])
    assert code == 0, err
    assert json.loads(out) == []
    assert calls_to(EFETCH) == []


@responses.activate
def test_an_esearch_error_is_malformed_and_exits_one(cli):
    responses.add(responses.GET, re.compile(ESEARCH),
                  json={"esearchresult": {"ERROR": "Invalid term"}})
    code, out, err = cli(["self-supervised ECG"])
    assert code == 1
    assert "MALFORMED" in err


@responses.activate
def test_a_non_list_idlist_is_malformed_and_exits_one(cli):
    responses.add(responses.GET, re.compile(ESEARCH),
                  json={"esearchresult": {"idlist": "34265844", "count": "1"}})
    code, _out, err = cli(["self-supervised ECG"])
    assert code == 1
    assert "MALFORMED" in err


@responses.activate
def test_the_from_year_clause_is_composed_in_the_request(cli, answer, xml_of):
    serve(answer("pubmed", "22b8c990"),
          xml_of(answer("pubmed", "73b72d5e")["response_body"]["articles"]))
    code, _out, err = cli(["self-supervised ECG", "--from-year", "2020"])
    assert code == 0, err
    term = calls_to(ESEARCH)[0].request.url
    assert "2020%3A3000%5Bdp%5D" in term or "2020:3000[dp]" in term


# --- 3. policy and recording (D16, plan 5.3 item 4 and item 6) -----------------------------


@responses.activate
def test_pubmed_is_off_by_default_and_sends_nothing(cli_project, fast_send, cli_run):
    code, _out, err = cli_run(pubmed.main, ["self-supervised ECG"])
    assert code == 3
    assert "off" in err
    assert len(responses.calls) == 0


@responses.activate
def test_a_recorded_search_replays_byte_identical_with_zero_transport_calls(
        cli, answer, xml_of):
    serve(answer("pubmed", "22b8c990"),
          xml_of(answer("pubmed", "73b72d5e")["response_body"]["articles"]))
    code, recorded_out, err = cli(["self-supervised ECG", "--workspace", "demo",
                                   "--network", "record"])
    assert code == 0, err
    responses.calls.reset()
    for mode in ("replay", "offline"):
        code, out, err = cli(["self-supervised ECG", "--workspace", "demo", "--network", mode])
        assert code == 0, err
        assert out == recorded_out
        assert len(responses.calls) == 0


@responses.activate
def test_a_replay_miss_exits_six_and_sends_nothing(cli):
    code, _out, err = cli(["self-supervised ECG", "--workspace", "demo", "--network", "replay"])
    assert code == 6
    assert len(responses.calls) == 0


# --- 4. the efetch parser and its malformed answers (review r1 F3) -------------------------------
# Small synthetic XML strings: each branch of pubmed.parse_articles has one test.


def _set(*articles: str) -> str:
    return '<?xml version="1.0" ?><PubmedArticleSet>' + "".join(articles) + "</PubmedArticleSet>"


def _article(pmid: str, body: str) -> str:
    return (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>{body}</Article>"
            "</MedlineCitation><PubmedData><ArticleIdList>"
            f'<ArticleId IdType="pubmed">{pmid}</ArticleId></ArticleIdList></PubmedData>'
            "</PubmedArticle>")


def test_a_medline_date_gives_the_leading_year():
    body = ("<Journal><Title>Sleep Med</Title><JournalIssue><PubDate>"
            "<MedlineDate>2019 Dec-2020 Jan</MedlineDate></PubDate></JournalIssue></Journal>"
            "<ArticleTitle>Synthetic title.</ArticleTitle>")
    [article] = pubmed.parse_articles(_set(_article("1001", body)))
    assert article["year"] == 2019


def test_the_electronic_article_date_is_the_year_when_there_is_no_issue_date():
    body = ("<Journal><Title>J</Title><JournalIssue/></Journal>"
            "<ArticleTitle>Synthetic title.</ArticleTitle>"
            "<ArticleDate><Year>2021</Year></ArticleDate>")
    [article] = pubmed.parse_articles(_set(_article("1002", body)))
    assert article["year"] == 2021


def test_a_labelled_abstract_keeps_its_labels_in_order():
    abstract = ('<Abstract><AbstractText Label="BACKGROUND">Synthetic background.</AbstractText>'
                '<AbstractText Label="METHODS">Synthetic methods.</AbstractText></Abstract>')
    body = ("<Journal><Title>J</Title></Journal>"
            "<ArticleTitle>Synthetic title.</ArticleTitle>" + abstract)
    [article] = pubmed.parse_articles(_set(_article("1003", body)))
    assert article["abstract"] == "BACKGROUND: Synthetic background. METHODS: Synthetic methods."


def test_a_doi_given_only_as_an_elocation_is_read():
    body = ("<Journal><Title>J</Title></Journal><ArticleTitle>Synthetic title.</ArticleTitle>"
            '<ELocationID EIdType="doi" ValidYN="Y">10.5555/synthetic.elocation</ELocationID>')
    [article] = pubmed.parse_articles(_set(_article("1004", body)))
    assert article["doi"] == "10.5555/synthetic.elocation"


def test_inline_markup_and_a_bracketed_translation_are_flattened():
    plain = "<Journal><Title>J</Title></Journal><ArticleTitle>Inline <i>markup</i> title.</ArticleTitle>"
    translated = ("<Journal><Title>J</Title></Journal>"
                  "<ArticleTitle>[A <i>translated</i> title].</ArticleTitle>")
    first, second = pubmed.parse_articles(_set(_article("1005", plain), _article("1006", translated)))
    assert first["title"] == "Inline markup title"
    assert second["title"] == "A translated title"


def test_an_empty_article_set_is_a_clean_negative():
    assert pubmed.parse_articles('<?xml version="1.0" ?><PubmedArticleSet/>') == []


@pytest.mark.parametrize("text", [
    "<PubmedArticleSet><ERROR>Synthetic error</ERROR></PubmedArticleSet>",
    "<html>not an efetch answer",
    "not xml at all",
])
def test_an_error_or_unparseable_efetch_answer_is_malformed(text):
    with pytest.raises(http.HTTPFailure) as exc:
        pubmed.parse_articles(text)
    assert exc.value.classification == "MALFORMED"


@responses.activate
def test_a_malformed_efetch_body_exits_one(cli, answer):
    serve(answer("pubmed", "22b8c990"))
    responses.add(responses.GET, re.compile(EFETCH),
                  body="<PubmedArticleSet><ERROR>Synthetic error", content_type="text/xml")
    code, _out, err = cli(["self-supervised ECG"])
    assert code == 1
    assert "MALFORMED" in err
