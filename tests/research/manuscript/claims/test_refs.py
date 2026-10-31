"""Claim references (M-G 5.4): comment and inline markers, cite keys and their DOIs. Synthetic only."""

from scieflow.research.manuscript.claims import refs

BIB = {"smith2019": "10.5555/SYN.2019.1"}


def test_comment_marker_is_a_data_ref():
    found, problems = refs.parse_refs("% source: [data:tbl-metrics]", "", BIB)
    assert found == [{"type": "data", "id": "tbl-metrics", "via": "comment"}]
    assert problems == []


def test_inline_run_marker_is_a_run_ref():
    found, problems = refs.parse_refs("", "Sigma was 1.5 in [run:denoise/sigma-1.5].", BIB)
    assert found == [{"type": "run", "id": "denoise/sigma-1.5", "via": "inline"}]
    assert problems == []


def test_cite_with_doi_becomes_a_doi_ref_and_a_keyless_cite_does_not():
    text = r"A finding \cite{smith2019} and another \cite{nodoi}."
    found, _ = refs.parse_refs("", text, BIB)
    assert found == [{"type": "doi", "id": "10.5555/syn.2019.1", "key": "smith2019", "via": "cite"}]
    assert refs.cite_keys(text) == ["smith2019", "nodoi"]


def test_malformed_markers_are_ignored():
    found, problems = refs.parse_refs("", "[data:] [dat:x] [data:a b] [run:]", {})
    assert found == []
    assert problems == []


def test_a_201_character_id_is_refused():
    found, problems = refs.parse_refs("", "[data:" + "a" * 201 + "]", {})
    assert found == []
    assert [p["rule"] for p in problems] == ["ref-too-long"]


def test_a_200_character_id_is_accepted():
    found, problems = refs.parse_refs("", "[data:" + "a" * 200 + "]", {})
    assert found == [{"type": "data", "id": "a" * 200, "via": "inline"}]
    assert problems == []
