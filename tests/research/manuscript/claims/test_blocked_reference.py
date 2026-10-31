"""M-G t3: C005 blocked references (M-G 5.6): only from review_service.blockers(), evidence and identity entries,
matched by DOI or by the cite key or its safe token. Synthetic only."""

from __future__ import annotations

import hashlib

import pytest

from scieflow.core.review import identity_gates
from scieflow.core.review import service as review_service
from scieflow.research.manuscript.claims import compile as compile_mod
from tests.research.manuscript.claims.t2_support import BIB_DOI
from tests.research.manuscript.claims.t3_support import body, built_check, of_code, of_rule, results_run, rules
from tests.research.manuscript.t8_support import add_review

NOVAK = BIB_DOI + "@article{novak2020,\n  title = {Synthetic retracted study},\n  doi = {10.5555/rbd.2020.014}\n}\n"


def blocker(reason: str, references: list, cluster=None) -> dict:
    return {"round": 1, "cluster": cluster, "reason": reason, "title": None, "flags": [],
            "references": references, "decision": None}


def stub_blockers(monkeypatch, entries) -> None:
    monkeypatch.setattr(review_service, "blockers", lambda project, slug: list(entries))


def test_a_retracted_reference_in_the_shipped_review_fixture_blocks_the_cited_claim(synth):
    add_review(synth)
    results_run(synth, body("The retracted study reported findings \\cite{novak2020}."), bib=NOVAK)
    result = built_check(synth)
    found = of_code(result, "C005")
    assert found and found[0]["rule"] == "blocked-reference" and found[0]["severity"] == "error", rules(result)
    assert "retracted" in found[0]["message"]
    assert result.errors, "a blocked reference is an error"


def test_an_evidence_blocker_matches_by_doi_and_names_the_reason_only(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    stub_blockers(monkeypatch, [blocker("retracted", [{"doi": "https://doi.org/10.5555/SYN.2010.1",
                                                       "detail": "a synthetic detail that must not print"}])])
    result = built_check(synth)
    found = of_code(result, "C005")
    assert len(found) == 1, rules(result)
    assert "retracted" in found[0]["message"]
    assert "synthetic detail" not in str(result.document())


def test_an_identity_mismatch_on_a_safe_key_blocks_the_citing_claim(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    stub_blockers(monkeypatch, [blocker("identity-mismatch", [{"key": "lee2010", "detail": "x"}])])
    result = built_check(synth)
    assert of_code(result, "C005") and "identity-mismatch" in of_code(result, "C005")[0]["message"]


def test_an_identity_unresolvable_with_only_a_doi_blocks_the_claim_by_doi(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    stub_blockers(monkeypatch, [blocker("identity-unresolvable", [{"doi": "10.5555/SYN.2010.1"}])])
    result = built_check(synth)
    assert of_code(result, "C005"), rules(result)


def test_a_blocker_for_another_reference_does_not_block_the_claim(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    stub_blockers(monkeypatch, [blocker("retracted", [{"key": "smith2001"}])])
    result = built_check(synth)
    assert of_code(result, "C005") == [], rules(result)


def test_an_overrule_that_clears_the_blocker_clears_the_claim(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))
    stub_blockers(monkeypatch, [])
    result = built_check(synth)
    assert of_code(result, "C005") == [] and result.errors == []


def test_an_unreadable_status_is_a_warning_in_check_and_marks_the_result(synth, monkeypatch):
    results_run(synth, body("The timing study \\cite{lee2010} was reported in the Results."))

    def broken(project, slug):
        raise ValueError("bad review file")

    monkeypatch.setattr(review_service, "blockers", broken)
    result = built_check(synth)
    found = of_rule(result, "status-unreadable")
    assert found and found[0]["severity"] == "warning" and found[0]["code"] is None, rules(result)
    assert of_code(result, "C005") == []
    assert result.status_unreadable is True


@pytest.mark.parametrize("key", ["smith2019", "smith 2019", "Smith&Jones:2019", "x" * 250])
def test_the_local_token_rule_equals_identity_gates_token(key):
    assert compile_mod.key_token(key) == identity_gates._token(key)


def test_an_unsafe_key_token_is_the_hash_form():
    token = compile_mod.key_token("smith 2019")
    assert token == "k-" + hashlib.sha256("smith 2019".encode("utf-8")).hexdigest()[:16]
    assert compile_mod.key_token("smith2019") == "smith2019"
