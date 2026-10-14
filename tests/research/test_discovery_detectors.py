"""M3 typed gap detectors and the mechanical verification gate (T3.2).

Each detector turns one provider's fetched evidence into candidate gaps whose
evidence items carry verbatim quotes, plus the evidence corpus those quotes
come from. `verify` keeps a gap only if at least one evidence item's quote is
found verbatim in the corpus under that item's ref: unquoted or invented quotes
are dropped mechanically. Recorded fixtures only, no network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scieflow.research.discovery import detectors
from scieflow.research.lib import ctgov, openalex_trends, pubtator

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures"
GAPS_SCHEMA = json.loads((REPO / "src/scieflow/research/schemas/gaps.schema.json").read_text())
RBD = "@DISEASE_REM_Sleep_Behavior_Disorder"


def fx(rel):
    return json.loads((FIX / rel).read_text())


def mcp_value(name):
    body = fx(f"scite/mcp/{name}.json")["http"]["body"]
    return json.loads(body["result"]["content"][0]["text"])


def gap_item_validator():
    item = GAPS_SCHEMA["properties"]["gaps"]["items"]
    return Draft202012Validator(item)


def assert_valid_gaps(gaps):
    v = gap_item_validator()
    for g in gaps:
        errors = list(v.iter_errors(g))
        assert not errors, (g, [e.message for e in errors])


def quoted_in_corpus(det):
    """Every quote a detector produced is verbatim in its own corpus."""
    for g in det["gaps"]:
        for e in g["evidence"]:
            assert any(detectors.normalise_quote(e["quote"]) in detectors.normalise_quote(t)
                       for t in det["corpus"][e["ref"].casefold()])


# --- schema (additive) ------------------------------------------------------------------------


def test_gaps_schema_gains_detector_and_quote_additively():
    item = GAPS_SCHEMA["properties"]["gaps"]["items"]
    assert item["required"] == ["id", "statement", "evidence", "novelty_rationale"]
    assert set(item["properties"]["detector"]["enum"]) == set(detectors.DETECTORS)
    ev = item["properties"]["evidence"]["items"]
    assert ev["required"] == ["kind", "ref"]
    assert {"paper", "data", "article"} <= set(ev["properties"]["kind"]["enum"])
    assert ev["properties"]["quote"]["type"] == "string"
    # an old-style gap (no detector, no quote) is still valid
    old = {"id": "G1", "statement": "s", "evidence": [{"kind": "paper", "ref": "10.1/x"}],
           "novelty_rationale": "r"}
    assert not list(gap_item_validator().iter_errors(old))


# --- detectors on fixtures --------------------------------------------------------------------


def test_disputes_quotes_contrasting_citation_statements():
    graph = {"edges": [
        {"s": "10.1000/citing.2", "t": "10.1000/seed.1", "type": "contrasting",
         "snippets": [{"text": "In contrast, we did not observe this association in RBD."}]},
        {"s": "10.1000/citing.3", "t": "10.1000/seed.1", "type": "contrasting",
         "snippet": "These results conflict with the earlier report."},
        {"s": "10.1000/citing.1", "t": "10.1000/seed.1", "type": "supporting",
         "snippets": [{"text": "We confirmed this finding."}]},
        {"s": "10.1000/citing.4", "t": "10.1000/other", "type": "contrasting",
         "snippets": [{"text": "Not a seed, ignored here."}]},
    ], "papers": {"10.1000/seed.1": {"title": "Seed paper title", "year": 2019}}}
    det = detectors.disputes(graph, seeds=["10.1000/seed.1"])
    assert det["detector"] == "disputes"
    assert len(det["gaps"]) == 1
    g = det["gaps"][0]
    assert g["detector"] == "disputes"
    assert "Seed paper title" in g["statement"]
    assert [e["ref"] for e in g["evidence"]] == ["10.1000/citing.2", "10.1000/citing.3"]
    assert all(e["source"] == "scite_mcp" and e["kind"] == "paper" for e in g["evidence"])
    assert "We confirmed" not in json.dumps(det)
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)


def test_disputes_without_snippets_yield_no_gap():
    det = detectors.disputes(mcp_value("call_citation_graph"),
                             seeds=["10.1038/s41586-020-2012-7"])
    assert det["gaps"] == []


def test_open_questions_quote_the_stating_sentence():
    value = {"hits": [
        {"doi": "10.1000/a", "title": "Paper A",
         "abstract": "RBD precedes synucleinopathy. Whether melatonin slows phenoconversion "
                     "remains unclear. We studied sleep."},
        {"doi": "10.1000/b", "title": "Paper B", "abstract": "Nothing open here at all.",
         "fulltextExcerpts": ["Further studies are needed to establish the role of "
                              "gut microbiota in prodromal disease."]},
        {"doi": "not a doi", "abstract": "It is unknown whether this matters."},
    ]}
    det = detectors.open_questions(value)
    quotes = [e["quote"] for g in det["gaps"] for e in g["evidence"]]
    assert quotes == ["Whether melatonin slows phenoconversion remains unclear.",
                      "Further studies are needed to establish the role of gut microbiota "
                      "in prodromal disease."]
    assert [g["evidence"][0]["ref"] for g in det["gaps"]] == ["10.1000/a", "10.1000/b"]
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)


def test_abc_links_on_recorded_pubtator_relations():
    a_edges = [pubtator._edge(r) for r in fx("discovery/pubtator/relations_rbd.json")]
    b_edges = {}
    for name, ent in (("clonazepam", "@CHEMICAL_Clonazepam"), ("snca", "@GENE_SNCA"),
                      ("melatonin", "@CHEMICAL_Melatonin")):
        b_edges[ent] = [e for e in (pubtator._edge(r) for r in
                                    fx(f"discovery/pubtator/relations_{name}.json")) if e]
    a_edges = [e for e in a_edges if e]
    links = pubtator.abc_open_links(RBD, a_edges, b_edges)
    assert links, "fixture must contain open links"
    det = detectors.abc_links({"a": RBD, "b": sorted(b_edges), "links": links}, limit=3)
    assert len(det["gaps"]) == 3
    for g, link in zip(det["gaps"], links[:3]):
        assert link["c"] in g["statement"] and RBD in g["statement"]
        assert g["detector"] == "abc_links"
        assert all(e["source"] == "pubtator" for e in g["evidence"])
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)


def test_unpublished_trials_on_recorded_ctgov_pages():
    studies = [ctgov.normalise(s) for p in ("p1", "p2")
               for s in fx(f"discovery/ctgov/studies_rbd_completed_{p}.json")["studies"]]
    rows = ctgov.unpublished(studies, as_of=date(2026, 10, 14))
    assert rows
    det = detectors.unpublished_trials(rows)
    assert len(det["gaps"]) == len([r for r in rows if r["title"] and r["nct_id"]])
    g = det["gaps"][0]
    assert g["evidence"][0]["kind"] == "trial"
    assert g["evidence"][0]["ref"] == rows[0]["nct_id"]
    assert g["evidence"][0]["quote"] == rows[0]["title"]
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)


def test_bridges_on_recorded_openalex_references():
    refs_a = [{"id": openalex_trends._short(r["id"]),
               "referenced_works": [openalex_trends._short(x) for x in r["referenced_works"]]}
              for r in fx("discovery/openalex/refs_rbd.json")["results"]]
    refs_b = [{"id": openalex_trends._short(r["id"]),
               "referenced_works": [openalex_trends._short(x) for x in r["referenced_works"]]}
              for r in fx("discovery/openalex/refs_synucleinopathy.json")["results"]]
    found = openalex_trends.bridges(refs_a, refs_b, min_each=1)
    assert found
    result = {"a": "REM sleep behavior disorder", "b": "synucleinopathy", "n_a": 1000,
              "n_b": 5000, "n_ab": 40, "overlap": 0.04, "shared_top": [], "bridges": found}
    det = detectors.bridges(result, limit=4)
    assert len(det["gaps"]) == 1
    g = det["gaps"][0]
    assert len(g["evidence"]) == min(4, len(found))
    assert g["evidence"][0]["ref"] == f"openalex:{found[0]['work']}"
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)
    # well-connected literatures are not a gap
    assert detectors.bridges({**result, "overlap": 0.5})["gaps"] == []
    assert detectors.bridges({**result, "bridges": []})["gaps"] == []


def test_trend_breaks_quote_the_window():
    counts = {y: 10 for y in range(2000, 2010)} | {y: 60 for y in range(2010, 2016)}
    t = {"term": "REM sleep behavior disorder", "until_year": 2015, "normalised": False,
         "counts": counts,
         "breaks": openalex_trends.trend_breaks(counts, until_year=2015)}
    assert t["breaks"]
    det = detectors.trend_breaks(t)
    assert len(det["gaps"]) == len(t["breaks"])
    assert det["gaps"][0]["detector"] == "trend_breaks"
    assert_valid_gaps(det["gaps"])
    quoted_in_corpus(det)


def test_own_data_tags_agent_gaps_and_keeps_their_quotes():
    doc = {"agent": "claude-gaps", "topic": "t", "hypotheses": [], "gaps": [
        {"id": "G1", "statement": "Night-to-night variability is unexplored.",
         "evidence": [{"kind": "data", "ref": "psg_summary",
                       "quote": "night-to-night variability was not assessed"}],
         "novelty_rationale": "r"}]}
    det = detectors.own_data(doc)
    assert det["gaps"][0]["detector"] == "own_data"
    assert det["corpus"] == {}            # the caller supplies the local corpus


# --- merge and the verification gate ------------------------------------------------------------


def test_merge_renumbers_and_unions_corpora():
    d1 = detectors.unpublished_trials([{"nct_id": "NCT01", "title": "Trial one of melatonin",
                                        "completion_date": "2019-01",
                                        "months_since_completion": 90}])
    d2 = detectors.unpublished_trials([{"nct_id": "NCT02", "title": "Trial two of clonazepam",
                                        "completion_date": "2018-01",
                                        "months_since_completion": 100}])
    merged = detectors.merge([d1, d2])
    assert [g["id"] for g in merged["gaps"]] == ["G1", "G2"]
    assert set(merged["corpus"]) == {"nct01", "nct02"}


def test_verify_drops_unquoted_and_invented_quotes_mechanically():
    corpus = {"10.1000/a": ["Whether melatonin slows phenoconversion remains unclear."],
              "NCT01": ["Trial one of melatonin"]}
    gaps = [
        {"id": "G1", "detector": "open_questions", "statement": "kept",
         "evidence": [{"kind": "paper", "ref": "10.1000/A",
                       "quote": "whether  melatonin slows\nphenoconversion remains unclear"},
                      {"kind": "paper", "ref": "10.1000/a", "quote": "an invented sentence here"}],
         "novelty_rationale": "r"},
        {"id": "G2", "detector": "disputes", "statement": "no quote at all",
         "evidence": [{"kind": "paper", "ref": "10.1000/a"}], "novelty_rationale": "r"},
        {"id": "G3", "detector": "disputes", "statement": "quote from another ref",
         "evidence": [{"kind": "trial", "ref": "NCT02", "quote": "Trial one of melatonin"}],
         "novelty_rationale": "r"},
        {"id": "G4", "detector": "unpublished_trials", "statement": "too short",
         "evidence": [{"kind": "trial", "ref": "NCT01", "quote": "Trial"}],
         "novelty_rationale": "r"},
        {"id": "G5", "detector": "unpublished_trials", "statement": "blank",
         "evidence": [{"kind": "trial", "ref": "NCT01", "quote": "   "}],
         "novelty_rationale": "r"},
    ]
    out = detectors.verify(gaps, corpus)
    assert [g["id"] for g in out["kept"]] == ["G1"]
    assert [e["quote"] for e in out["kept"][0]["evidence"]] == [
        "whether  melatonin slows\nphenoconversion remains unclear"]
    dropped = {d["id"]: d["reason"] for d in out["dropped"]}
    assert set(dropped) == {"G2", "G3", "G4", "G5"}
    assert all(r == "no verified quote" for r in dropped.values())
    assert out["dropped_evidence"] == 1 + 1 + 1 + 1 + 1   # G1's invented item + one per dropped gap
    # input untouched
    assert len(gaps[0]["evidence"]) == 2


def test_verify_on_detector_output_keeps_everything_and_drops_tampering():
    rows = [{"nct_id": "NCT01", "title": "Trial one of melatonin", "completion_date": "2019-01",
             "months_since_completion": 90}]
    det = detectors.merge([detectors.unpublished_trials(rows)])
    assert [g["id"] for g in detectors.verify(det["gaps"], det["corpus"])["kept"]] == ["G1"]
    tampered = json.loads(json.dumps(det["gaps"]))
    tampered[0]["evidence"][0]["quote"] = "Trial one of melatonin shows benefit"
    assert detectors.verify(tampered, det["corpus"])["kept"] == []


def test_verify_refuses_a_non_mapping_corpus():
    with pytest.raises(TypeError):
        detectors.verify([], ["not", "a", "mapping"])


# --- review findings (ticket 15) --------------------------------------------------------------


def test_disputes_deduplicate_long_snippets_after_clipping():
    long = "In contrast, " + "we did not find it " * 60
    graph = {"edges": [{"s": "10.1000/c.1", "t": "10.1000/seed.1", "type": "contrasting",
                        "snippets": [{"text": long}, {"text": long + " "}, {"text": long}]}]}
    det = detectors.disputes(graph, seeds=["10.1000/seed.1"])
    assert len(det["gaps"][0]["evidence"]) == 1


def test_verify_merges_corpus_keys_that_differ_only_by_case():
    corpus = {"10.1/B": ["Alpha beta gamma delta epsilon."], "10.1/b": ["Zeta eta theta iota."]}
    gap = {"id": "G1", "statement": "s", "novelty_rationale": "r",
           "evidence": [{"kind": "paper", "ref": "10.1/b", "quote": "alpha beta gamma delta"}]}
    assert detectors.verify([gap], corpus)["kept"]
