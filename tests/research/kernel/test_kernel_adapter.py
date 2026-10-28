"""M-C1 t4 (plan 5.2): the record adapter between search records and the vendored CSL model.

Synthetic bibliographic records only; offline. The dedup gold set (210 labelled pairs) is the
same byte copy as tests/vendor; the adapter must reach the same decisions (plan t1 item 5).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scieflow._vendor.researcher_core.dedupe import dedupe
from scieflow._vendor.researcher_core.model import CSLRecord
from scieflow._vendor.researcher_core.rank import rank_records
from scieflow.research.lib import kernel, papers

GOLD = Path(__file__).resolve().parents[2] / "fixtures" / "kernel" / "dedup.yaml"


def test_to_csl_keeps_every_identifier_and_the_source_rank():
    raw = papers.record(doi="10.5555/ABC.1", title="Demo", year=2023, venue="Journal",
                        cited_by=7, abstract="An abstract", url="https://example.org/x",
                        source="openalex")
    csl = kernel.to_csl(raw, "openalex", 3)
    assert isinstance(csl, CSLRecord)
    assert csl.id == "openalex:3"
    assert csl.DOI.lower() == "10.5555/abc.1"
    assert csl.URL == "https://example.org/x"
    assert csl.source == "openalex"
    assert csl.source_id == "https://example.org/x"
    assert csl.year == 2023
    assert csl.citation_count == 7
    assert csl.container_title == "Journal"
    assert csl.extra["source_ranks"] == {"openalex": 3}


def test_to_csl_tolerates_missing_fields():
    csl = kernel.to_csl(papers.record(title="Bare title", source="crossref"), "crossref", 0)
    assert csl.DOI == "" and csl.year is None and csl.citation_count is None
    assert csl.title == "Bare title"


def test_from_csl_unions_namespaced_signals_and_names_the_sources():
    a = papers.record(doi="10.5555/x", title="Same title", year=2023, source="openalex",
                      signals={"openalex": {"is_oa": False, "authors": ["A. Author"]}})
    b = papers.record(doi="10.5555/x", title="Same title", year=2023, source="crossref",
                      signals={"crossref": {"subject": "neurology"}})
    merged = dedupe([kernel.to_csl(a, "openalex", 0), kernel.to_csl(b, "crossref", 0)]).records[0]
    ranked = rank_records([merged])[0]
    out = kernel.from_csl(ranked, [a, b], rank=0)
    assert out["doi"] == "10.5555/x" and out["title"] == "Same title" and out["year"] == 2023
    assert out["signals"]["openalex"] == {"is_oa": False, "authors": ["A. Author"]}  # False kept
    assert out["signals"]["crossref"] == {"subject": "neurology"}
    kern = out["signals"]["kernel"]
    assert kern["sources"] == ["openalex", "crossref"]
    assert kern["rank"] == 0
    assert isinstance(kern["score"], float) and kern["score"] > 0


def test_from_csl_of_a_single_source_record_keeps_the_old_shape_plus_kernel_block():
    raw = papers.record(doi="10.5555/solo", title="Solo", year=2020, source="arxiv")
    ranked = rank_records([kernel.to_csl(raw, "arxiv", 0)])[0]
    out = kernel.from_csl(ranked, [raw], rank=0)
    assert set(out) == {"doi", "title", "year", "venue", "cited_by", "abstract", "url",
                        "source", "signals"}
    assert out["signals"]["kernel"]["sources"] == ["arxiv"]


def test_rank_ties_break_on_id_not_on_input_order():
    one = kernel.to_csl(papers.record(title="Twin title one", source="openalex"), "openalex", 0)
    two = kernel.to_csl(papers.record(title="Twin title two", source="crossref"), "crossref", 0)
    forward = [r.id for r in rank_records([one, two])]
    backward = [r.id for r in rank_records([two, one])]
    assert forward == backward == sorted(forward)


def test_dedup_gold_set_through_the_adapter_gives_the_gold_decisions():
    items = yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]
    counts = {"same_merged": 0, "same_kept": 0, "different_merged": 0, "different_kept": 0}

    def adapt(raw: dict, source: str, rank: int) -> CSLRecord:
        rec = papers.record(doi=raw.get("doi"), title=raw.get("title"), source=raw.get("source"))
        return kernel.to_csl(rec, source or "gold", rank)

    for index, item in enumerate(items):
        pair = [adapt(item["a"], item["a"].get("source"), 2 * index),
                adapt(item["b"], item["b"].get("source"), 2 * index + 1)]
        merged = len(dedupe(pair).records) == 1
        kind = "same" if item["label"] == "same" else "different"
        counts[f"{kind}_{'merged' if merged else 'kept'}"] += 1
    assert counts == {"same_merged": 110, "same_kept": 0, "different_merged": 0,
                      "different_kept": 100}, counts
