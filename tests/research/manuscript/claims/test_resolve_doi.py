"""M-G t2: DOI references and cite keys (M-G 5.4): bibliography, selected DOIs, unsafe markers.

Synthetic only: invented authors, titles and DOIs.
"""

from __future__ import annotations

import os

import pytest

from scieflow.research.manuscript.claims import ledger
from tests.research.manuscript.claims.t2_support import manuscript, write

LEE_DOI = "10.5555/syn.2010.1"  # lower case: the normalised form selected_dois.txt is compared in


def _rules(result) -> set:
    return {p["rule"] for p in result.problems}


def _severity(result, rule) -> set:
    return {p["severity"] for p in result.problems if p["rule"] == rule}


def test_cite_key_missing_from_the_bibliography_is_unresolved(synth):
    manuscript(synth, "Walkers were measured \\cite{nokey}.")
    result = ledger.build(synth.run_dir)
    assert "ref-unresolved" in _rules(result)
    assert _severity(result, "ref-unresolved") == {"error"}


def test_cited_entry_without_a_doi_is_a_doi_missing_warning(synth):
    manuscript(synth, "Walkers were measured \\cite{smith2001}.")
    result = ledger.build(synth.run_dir)
    assert "doi-missing" in _rules(result)
    assert _severity(result, "doi-missing") == {"warning"}
    assert "ref-unresolved" not in _rules(result)


def test_cited_doi_is_a_doi_reference_with_no_pin(synth):
    manuscript(synth, "Walkers were measured \\cite{lee2010}.")
    result = ledger.build(synth.run_dir)
    assert result.problems == []
    [rec] = ledger.read(synth.run_dir)
    assert {"type": "doi", "id": LEE_DOI, "key": "lee2010", "via": "cite"} in rec["refs"]
    assert rec["pins"] == {}


def test_doi_not_in_selected_dois_is_unselected_warning(synth):
    manuscript(synth, "Walkers were measured \\cite{lee2010}.")
    write(synth.run_dir / "report" / "selected_dois.txt", "# searched\n10.1234/other\n")
    result = ledger.build(synth.run_dir)
    assert "doi-unselected" in _rules(result)
    assert _severity(result, "doi-unselected") == {"warning"}


def test_doi_listed_in_selected_dois_is_accepted(synth):
    manuscript(synth, "Walkers were measured \\cite{lee2010}.")
    write(synth.run_dir / "report" / "selected_dois.txt", f"{LEE_DOI}\n")
    result = ledger.build(synth.run_dir)
    assert result.problems == []


def test_no_selected_dois_file_means_no_unselected_warning(synth):
    manuscript(synth, "Walkers were measured \\cite{lee2010}.")
    result = ledger.build(synth.run_dir)
    assert "doi-unselected" not in _rules(result)


def test_symlinked_selected_dois_file_refuses_the_build(synth, tmp_path):
    manuscript(synth, "Walkers were measured \\cite{lee2010}.")
    outside = tmp_path / "selected.txt"
    outside.write_text(f"{LEE_DOI}\n", encoding="utf-8")
    (synth.run_dir / "report").mkdir()
    os.symlink(outside, synth.run_dir / "report" / "selected_dois.txt")
    with pytest.raises(ledger.ClaimsError) as err:
        ledger.build(synth.run_dir)
    assert err.value.rule == "ref-unsafe"


def test_doi_marker_not_declared_in_the_bibliography_is_unresolved(synth):
    manuscript(synth, "Walkers were measured in 2019. % source: [doi:10.9999/undeclared.1]")
    result = ledger.build(synth.run_dir)
    assert "ref-unresolved" in _rules(result)


def test_doi_marker_declared_in_the_bibliography_resolves(synth):
    manuscript(synth, "Walkers were measured in 2010. % source: [doi:10.5555/SYN.2010.1]")
    result = ledger.build(synth.run_dir)
    assert result.problems == []


@pytest.mark.parametrize("marker", ["[doi:10.5555/%2F..%2Fsecret]", "[doi:10.5555/a/../b]"])
def test_unsafe_doi_marker_is_reported_not_recorded(synth, marker):
    manuscript(synth, f"Walkers were measured in 2010. % source: {marker}")
    result = ledger.build(synth.run_dir)
    assert "ref-unsafe-doi" in _rules(result)
    assert _severity(result, "ref-unsafe-doi") == {"error"}
    for rec in ledger.read(synth.run_dir):
        assert all(ref["type"] != "doi" for ref in rec["refs"])


def test_over_long_reference_id_is_reported(synth):
    manuscript(synth, "Walkers were measured in 2019. % source: [data:" + "a" * 201 + "]")
    result = ledger.build(synth.run_dir)
    assert "ref-too-long" in _rules(result)
    assert _severity(result, "ref-too-long") == {"error"}
