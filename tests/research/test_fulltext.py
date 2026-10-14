"""research/lib/fulltext.py: Europe PMC DOI -> PMCID, fullTextXML parse,
deterministic passage windows around key terms, abstract-only fallback.
Recorded fixtures, no network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import responses

from scieflow.research.lib import fulltext
from scieflow.research.lib.http import HTTPFailure

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "europepmc"
SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
XML_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4204854/fullTextXML"
OA_DOI = "10.1371/journal.pone.0110232"
CLOSED_DOI = "10.1016/j.biopsych.2005.08.012"
TERMS = ["GPS-SNO", "accuracy", "specificity"]


def fx(name):
    return json.loads((FIX / name).read_text())


def xml():
    return (FIX / "PMC4204854.xml").read_text()


def nosleep(_s):
    pass


def search_cb(request):
    q = request.params.get("query", "")
    if OA_DOI in q:
        return 200, {}, json.dumps(fx("search_oa.json"))
    if CLOSED_DOI in q:
        return 200, {}, json.dumps(fx("search_closed.json"))
    return 200, {}, json.dumps(fx("search_empty.json"))


# --- DOI -> PMCID -------------------------------------------------------------------


@responses.activate
def test_resolve_open_access():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    r = fulltext.resolve("https://doi.org/10.1371/JOURNAL.PONE.0110232", sleep=nosleep)
    assert r["pmcid"] == "PMC4204854" and r["pmid"] == "25333472"
    assert r["open_access"] is True
    assert r["doi"] == OA_DOI
    assert r["abstract"].startswith("Nitric oxide (NO) is an important signaling molecule")
    params = responses.calls[0].request.params
    assert params["query"] == f'DOI:"{OA_DOI}"'
    assert params["format"] == "json" and params["resultType"] == "core"


@responses.activate
def test_resolve_closed_strips_markup_from_abstract():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    r = fulltext.resolve(CLOSED_DOI, sleep=nosleep)
    assert r["pmcid"] is None and r["open_access"] is False
    assert "<" not in r["abstract"] and r["abstract"].startswith("Background Previous")


@responses.activate
def test_resolve_not_found():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    assert fulltext.resolve("10.9999/nothing-here", sleep=nosleep) is None


@responses.activate
def test_resolve_ignores_a_hit_for_another_doi():
    body = fx("search_oa.json")
    body["resultList"]["result"][0]["doi"] = "10.1371/journal.pone.9999999"
    responses.get(SEARCH, json=body)
    assert fulltext.resolve(OA_DOI, sleep=nosleep) is None


@responses.activate
def test_resolve_ignores_malformed_pmcid():
    body = fx("search_oa.json")
    body["resultList"]["result"][0]["pmcid"] = "PMC1/../../x"
    responses.get(SEARCH, json=body)
    r = fulltext.resolve(OA_DOI, sleep=nosleep)
    assert r["pmcid"] is None


@pytest.mark.parametrize("bad", ["not-a-doi", '10.1234/a"OR"b', "10.1234/../x", None])
@responses.activate
def test_resolve_invalid_doi_sends_nothing(bad):
    assert fulltext.resolve(bad, sleep=nosleep) is None
    assert len(responses.calls) == 0


# --- fullTextXML ----------------------------------------------------------------------


@responses.activate
def test_fetch_xml_and_404():
    responses.get(XML_URL, body=xml())
    assert fulltext.fetch_xml("PMC4204854", sleep=nosleep).lstrip().startswith("<!DOCTYPE")
    responses.get("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML",
                  status=404)
    assert fulltext.fetch_xml("PMC1", sleep=nosleep) is None


@pytest.mark.parametrize("bad", ["4204854", "PMC12/../x", "PMC", "pmc123 ", None])
@responses.activate
def test_fetch_xml_validates_pmcid(bad):
    with pytest.raises(ValueError):
        fulltext.fetch_xml(bad, sleep=nosleep)
    assert len(responses.calls) == 0


def test_parse_sections_paragraphs_and_abstract():
    doc = fulltext.parse(xml())
    assert [p["section"] for p in doc["abstract"]] == ["Abstract"]
    assert doc["abstract"][0]["text"].startswith("Nitric oxide (NO)")
    sections = list(dict.fromkeys(p["section"] for p in doc["paragraphs"]))
    assert sections == ["Introduction", "Results and Discussion", "Conclusions"]
    assert len(doc["paragraphs"]) == 6
    body = " ".join(p["text"] for p in doc["paragraphs"])
    assert "[1]–[9]" in body                         # inline xrefs kept as text
    assert "C in bold" not in body                   # tables / figures skipped
    assert "Table 1Computational" not in body
    assert all("  " not in p["text"] and "\n" not in p["text"] for p in doc["paragraphs"])


def test_parse_refuses_entity_declarations():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;">]><article><body><p>&lol2;</p></body></article>')
    with pytest.raises(ValueError, match="entity"):
        fulltext.parse(bomb)


def test_parse_malformed_xml():
    with pytest.raises(ValueError):
        fulltext.parse("<article><body><p>unclosed</body>")


def test_parse_without_sections():
    doc = fulltext.parse("<article><body><p>Loose  paragraph.</p></body></article>")
    assert doc["paragraphs"] == [{"section": "Body", "text": "Loose paragraph."}]
    assert doc["abstract"] == []


# --- passages -----------------------------------------------------------------------


def test_passages_deterministic_bounded_and_ranked():
    paras = fulltext.parse(xml())["paragraphs"]
    a = fulltext.passages(paras, TERMS, max_windows=3)
    b = fulltext.passages(list(paras), list(reversed(TERMS)), max_windows=3)
    assert a == b                                   # term order does not matter
    assert 1 <= len(a) <= 3
    for p in a:
        assert any(t.lower() in p["quote"].lower() for t in TERMS)
        assert set(p) == {"section", "quote", "terms", "score", "position"}
    assert [p["score"] for p in a] == sorted((p["score"] for p in a), reverse=True)
    top = a[0]
    assert top["section"] == "Results and Discussion"
    assert "GPS-SNO performed best" in top["quote"]
    assert top["terms"] == sorted(t.lower() for t in TERMS)
    assert top["score"] == 3


def test_passages_ties_break_by_document_order():
    paras = [{"section": "S", "text": "Alpha beta here. Filler one. Filler two. Filler three. "
                                      "Filler four. Alpha beta again."}]
    out = fulltext.passages(paras, ["alpha", "beta"], max_windows=2, context=1)
    assert [p["position"] for p in out] == [[0, 0], [0, 5]]


def test_passages_windows_do_not_overlap():
    paras = fulltext.parse(xml())["paragraphs"]
    out = fulltext.passages(paras, TERMS, max_windows=10, context=1)
    spans = {}
    for p in out:
        para, sent = p["position"]
        for other in spans.get(para, []):
            assert abs(other - sent) > 2
        spans.setdefault(para, []).append(sent)


def test_passages_window_has_context_sentences():
    paras = [{"section": "S", "text": "One intro. Two has needle here. Three after. Four far."}]
    out = fulltext.passages(paras, ["needle"], max_windows=1, context=1)
    assert out[0]["quote"] == "One intro. Two has needle here. Three after."
    out0 = fulltext.passages(paras, ["needle"], max_windows=1, context=0)
    assert out0[0]["quote"] == "Two has needle here."


def test_passages_max_chars():
    paras = fulltext.parse(xml())["paragraphs"]
    for p in fulltext.passages(paras, TERMS, max_windows=5, max_chars=120):
        assert len(p["quote"]) <= 120


def test_passages_keep_the_hit_sentence_when_context_is_too_long():
    long = "Background " + "words " * 200 + "end."
    paras = [{"section": "S", "text": long + " The drug reduced mortality. " + long}]
    out = fulltext.passages(paras, ["mortality"], max_windows=1, context=1, max_chars=200)
    assert "mortality" in out[0]["quote"]
    assert len(out[0]["quote"]) <= 200
    assert out[0]["quote"] == "The drug reduced mortality."


def test_passages_long_hit_sentence_trimmed_around_the_term():
    text = "Lead " + "alpha " * 150 + "the needle sits here " + "omega " * 150 + "end."
    out = fulltext.passages([{"section": "S", "text": text}], ["needle"], context=0,
                            max_chars=120)
    assert "needle" in out[0]["quote"] and len(out[0]["quote"]) <= 120


def test_passages_match_whole_words_with_simple_inflections():
    paras = [{"section": "S", "text": "Catalysis rose. A cat sat. Two cats ran. Rates fell. "
                                      "Spindles were counted. Proteins were S-nitrosylated."}]
    quotes = [p["quote"] for p in fulltext.passages(paras, ["cat"], context=0, max_windows=9)]
    assert quotes == ["A cat sat.", "Two cats ran."]
    assert fulltext.passages(paras, ["rat"], context=0) == []
    assert fulltext.passages(paras, ["spindle"], context=0)[0]["quote"] == \
        "Spindles were counted."
    assert fulltext.passages(paras, ["nitrosylate"], context=0)[0]["quote"] == \
        "Proteins were S-nitrosylated."


def test_passages_term_regex_safety():
    paras = [{"section": "S", "text": "Proteins were S-nitrosylated. The (BST) method [x] works."}]
    assert fulltext.passages(paras, ["(BST)"], context=0)[0]["quote"] == \
        "The (BST) method [x] works."
    assert fulltext.passages(paras, ["[x]"], context=0)  # literal, not a char class
    assert fulltext.passages(paras, ["ein"], context=0) == []   # no mid-word match


def test_passages_no_terms_or_no_hits():
    paras = fulltext.parse(xml())["paragraphs"]
    assert fulltext.passages(paras, []) == []
    assert fulltext.passages(paras, ["  ", ""]) == []
    assert fulltext.passages(paras, ["zebrafish"]) == []


def test_sentence_split_keeps_abbreviations_together():
    paras = [{"section": "S", "text": "Tissues, e.g., meristems are hard (Table S1). Next one."}]
    out = fulltext.passages(paras, ["meristems"], context=0)
    assert out[0]["quote"] == "Tissues, e.g., meristems are hard (Table S1)."


# --- evidence (end to end, recorded) ------------------------------------------------


@responses.activate
def test_evidence_full_text():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    responses.get(XML_URL, body=xml())
    ev = fulltext.evidence(OA_DOI, TERMS, max_windows=2, sleep=nosleep)
    assert ev["status"] == "full_text" and ev["abstract_only"] is False
    assert ev["pmcid"] == "PMC4204854" and ev["doi"] == OA_DOI
    assert len(ev["passages"]) == 2
    assert ev["passages"][0]["section"] == "Results and Discussion"


@responses.activate
def test_evidence_non_oa_is_abstract_only():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    ev = fulltext.evidence(CLOSED_DOI, ["amygdala", "social anxiety"], sleep=nosleep)
    assert ev["status"] == "abstract_only" and ev["abstract_only"] is True
    assert ev["passages"] and all(p["section"] == "Abstract" for p in ev["passages"])
    assert "amygdala" in ev["passages"][0]["quote"].lower()
    assert not any("fullTextXML" in c.request.url for c in responses.calls)


@responses.activate
def test_evidence_oa_but_fulltext_missing_falls_back_to_abstract():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    responses.get(XML_URL, status=404)
    ev = fulltext.evidence(OA_DOI, ["biotin switch"], sleep=nosleep)
    assert ev["status"] == "abstract_only" and ev["abstract_only"] is True
    assert ev["reason"] == "full text not available"
    assert ev["passages"] and ev["passages"][0]["section"] == "Abstract"


@responses.activate
def test_evidence_fulltext_transient_falls_back_to_abstract():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    for _ in range(3):
        responses.get(XML_URL, status=500)
    ev = fulltext.evidence(OA_DOI, ["biotin switch"], sleep=nosleep)
    assert ev["status"] == "abstract_only"
    assert ev["reason"] == "full text unavailable (TRANSIENT)"


@responses.activate
def test_evidence_unparseable_fulltext_falls_back_to_abstract():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    responses.get(XML_URL, body="<article><body>")
    ev = fulltext.evidence(OA_DOI, ["biotin switch"], sleep=nosleep)
    assert ev["status"] == "abstract_only"
    assert ev["reason"] == "full text not parseable"


@responses.activate
def test_evidence_not_in_europepmc():
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    ev = fulltext.evidence("10.9999/nothing-here", TERMS, sleep=nosleep)
    assert ev == {"doi": "10.9999/nothing-here", "status": "not_found", "pmcid": None,
                  "abstract_only": False, "passages": []}


@responses.activate
def test_evidence_search_failure_raises():
    for _ in range(3):
        responses.get(SEARCH, status=503)
    with pytest.raises(HTTPFailure) as exc:
        fulltext.evidence(OA_DOI, TERMS, sleep=nosleep)
    assert exc.value.classification == "TRANSIENT"


@responses.activate
def test_evidence_cache_reuses_search_and_xml():
    class DictCache:
        def __init__(self):
            self.d = {}

        def get(self, key, ttl_class):
            return self.d.get((key, ttl_class))

        def put(self, key, ttl_class, value):
            self.d[(key, ttl_class)] = value

    cache = DictCache()
    responses.add_callback(responses.GET, SEARCH, callback=search_cb)
    responses.get(XML_URL, body=xml())
    a = fulltext.evidence(OA_DOI, TERMS, cache=cache, sleep=nosleep)
    n = len(responses.calls)
    b = fulltext.evidence(OA_DOI, ["biotin switch"], cache=cache, sleep=nosleep)
    assert len(responses.calls) == n == 2
    assert a["status"] == b["status"] == "full_text"
    assert re.search("biotin switch", b["passages"][0]["quote"], re.I)
