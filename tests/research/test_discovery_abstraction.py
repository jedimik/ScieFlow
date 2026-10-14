"""M3 hypothesis abstraction and novelty (T3.4): hypotheses are abstracted (no
numbers, cohort descriptors, effect sizes) and residual leaks are refused before
anything is sent; a free novelty pre-check (OpenAlex + Europe PMC) runs for
every abstracted hypothesis; the scite check covers the tournament's top-k only,
OR-batched, at most 2 MCP calls. Recorded fixtures, no network."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import responses

from scieflow.core.review.claims import LeakError
from scieflow.integrations.scite.cache import Cache
from scieflow.integrations.scite.ledger import BudgetError
from scieflow.integrations.scite.mcp import MCPFailure
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.discovery import abstraction as A
from scieflow.research.lib import providers

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures"
OA = "https://api.openalex.org/works"
EP = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fx(rel):
    return json.loads((FIX / rel).read_text())


# --- abstraction ------------------------------------------------------------------------------

LEAKY = [
    "In our cohort of 43 patients, melatonin reduced phenoconversion by 35% (HR 0.62, "
    "95% CI 0.41-0.93, p<.001).",
    "We hypothesise that in the Prague cohort, REM sleep without atonia predicts a two-fold "
    "higher conversion risk.",
    "Among our participants [data:cohort_a], olfactory loss doubles the risk with a large "
    "effect size (Cohen's d = 0.9).",
    "In cohort_a, n = 43, gut dysbiosis precedes motor symptoms by 5 years.",
    "Clonazepam use (OR 2.3) is associated with falls in this sample of older adults "
    "% source: results/table2.csv",
    "The effect in our data is threefold larger than reported \\input{../secret}.",
]


@pytest.mark.parametrize("text", LEAKY)
def test_abstraction_strips_numbers_cohorts_and_effect_sizes(text):
    out = A.abstract(text)
    low = out.lower()
    words = set(re.findall(r"[a-z_-]+", low))
    assert not any(ch.isdigit() for ch in out)
    for bad in ("cohort", "cohort_a", "our", "we", "prague", "patients", "participants",
                "sample", "two-fold", "threefold", "doubles", "cohen's", "cohen", "hr", "or",
                "ci", "data", "source", "secret", "input", "table"):
        assert bad not in words, (bad, out)
    for bad in ("effect size", "<", "%", "{", "/"):
        assert bad not in low, (bad, out)
    assert A.residual_leaks(out) == []


def test_abstraction_keeps_the_scientific_content():
    out = A.abstract("In our cohort of 43 patients, melatonin slows phenoconversion of "
                     "isolated REM sleep behaviour disorder to synucleinopathy (HR 0.62).")
    for word in ("melatonin", "phenoconversion", "REM", "sleep", "synucleinopathy"):
        assert word.lower() in out.lower()


def test_study_design_words_are_not_cohort_names():
    out = A.abstract("Prospective cohort studies show that melatonin slows phenoconversion.")
    assert out.startswith("Prospective cohort studies")
    assert "Prague" not in A.abstract("In the Prague cohort, melatonin slows phenoconversion.")


def test_residual_leaks_are_refused_not_sent():
    for residual in ("melatonin in our cohort slows conversion",
                     "melatonin slows conversion by two-fold",
                     "melatonin slows conversion in cohort B",
                     "melatonin with a large effect size",
                     "melatonin 43"):
        assert A.residual_leaks(residual), residual
    with pytest.raises(LeakError):
        A.check(" 43 % ")
    with pytest.raises(LeakError):
        A.abstract("12 34 56")          # nothing left


def test_abstract_all_reports_refusals_per_hypothesis():
    out = A.abstract_all([{"id": "H1", "statement": LEAKY[0]},
                          {"id": "H2", "statement": "12 34"},
                          {"id": "H3", "statement": ""}])
    assert out[0]["status"] == "ok" and out[0]["id"] == "H1"
    assert out[1] == {"id": "H2", "status": "refused", "reason": out[1]["reason"]}
    assert out[2]["status"] == "refused"
    assert "abstract" not in out[1]


# --- free novelty pre-check ---------------------------------------------------------------------


@pytest.fixture
def gw(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    shutil.copy(REPO / "config" / "integrations.example.yml",
                root / "config" / "integrations.yml")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    pol = providers.load(root)
    return providers.Gateway(ws, pol, cache=Cache(tmp_path / "cache", pol.cache_ttl_days),
                             ledger_dir=tmp_path / "day", sleep=lambda _s: None)


ITEM = {"id": "H1", "status": "ok",
        "abstract": "melatonin slows synuclein progression in rem sleep behavior disorder"}


@responses.activate
def test_free_precheck_queries_openalex_and_europepmc_with_key_terms(gw):
    responses.get(OA, json=fx("discovery/novelty/openalex_rbd_melatonin.json"))
    responses.get(EP, json=fx("discovery/novelty/europepmc_rbd_melatonin.json"))
    out = A.free_precheck(gw, [ITEM, {"id": "H2", "status": "refused", "reason": "x"}])
    assert [r["id"] for r in out] == ["H1"]          # refused ones send nothing
    r = out[0]
    assert r["openalex"]["status"] == "ok" and r["openalex"]["count"] == 880
    assert r["europepmc"]["status"] == "ok" and r["europepmc"]["count"] == 406
    assert len(r["openalex"]["hits"]) == 5 and r["openalex"]["hits"][0]["title"]
    assert r["europepmc"]["hits"][0]["doi"] == "10.1002/cns.70991"
    assert len(responses.calls) == 2
    sent = [c.request.url for c in responses.calls]
    assert all("melatonin" in u for u in sent)
    rows = [json.loads(x) for x in (gw.ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert {x["source"] for x in rows} == {"openalex", "europepmc"}


@responses.activate
def test_free_precheck_survives_a_provider_that_is_off_or_failing(gw, tmp_path):
    responses.get(OA, status=500)
    responses.get(EP, json=fx("discovery/novelty/europepmc_rbd_melatonin.json"))
    out = A.free_precheck(gw, [ITEM])
    assert out[0]["openalex"]["status"] == "unavailable"
    assert out[0]["europepmc"]["status"] == "ok"
    off = providers.Gateway(gw.ws, providers.ProviderPolicy(off={"openalex": "off",
                                                                  "europepmc": "off"}),
                            cache=gw.cache, ledger_dir=tmp_path / "day2")
    n = len(responses.calls)
    out = A.free_precheck(off, [ITEM])
    assert out[0]["openalex"]["status"] == "off" and out[0]["europepmc"]["status"] == "off"
    assert len(responses.calls) == n


def test_free_precheck_refuses_a_leaky_abstract_before_sending(gw):
    with responses.RequestsMock() as rsps:
        out = A.free_precheck(gw, [{"id": "H9", "status": "ok",
                                    "abstract": "melatonin in cohort_a 43"}])
        assert out[0]["status"] == "refused"
        assert len(rsps.calls) == 0


# --- scite check: top-k only, <= 2 MCP calls ---------------------------------------------------


def items(n):
    words = ["melatonin", "clonazepam", "olfaction", "dysbiosis", "atonia", "dopamine",
             "constipation", "cardiac", "orthostatic", "cognition"]
    return [{"id": f"H{i + 1}", "status": "ok",
             "abstract": f"{words[i]} predicts phenoconversion of rem sleep behavior disorder"}
            for i in range(n)]


SEARCH = {"hits": [
    {"doi": "10.1000/mel.1", "title": "Melatonin and phenoconversion in RBD",
     "abstract": "Melatonin did not change phenoconversion of rem sleep behavior disorder.",
     "year": 2022, "tally": {"supporting": 2, "contrasting": 4, "mentioning": 10},
     "citations": [{"snippet": "Melatonin showed no protective effect in our series.",
                    "type": "contrasting", "sourceDoi": "10.1000/c.1",
                    "targetDoi": "10.1000/mel.1"}]},
    {"doi": "10.1000/olf.2", "title": "Olfaction predicts phenoconversion",
     "abstract": "Olfaction loss predicts phenoconversion in rem sleep behavior disorder.",
     "year": 2020, "tally": {"supporting": 9, "contrasting": 1, "mentioning": 3}},
    {"doi": "10.1000/unrelated", "title": "Something else",
     "abstract": "Kidney function in athletes.", "year": 2019},
]}
GRAPH = {"edges": [
    {"s": "10.1000/c.2", "t": "10.1000/mel.1", "type": "contrasting",
     "snippets": [{"text": "We could not replicate the melatonin finding."}]},
    {"s": "10.1000/c.3", "t": "10.1000/olf.2", "type": "supporting",
     "snippets": [{"text": "Confirmed."}]},
]}


class FakeMCP:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def __call__(self, tool, args, round_):
        self.calls.append((tool, json.loads(json.dumps(args)), round_))
        err = self.fail.get(len(self.calls))
        if err is not None:
            raise err
        return SEARCH if tool == "search_literature" else GRAPH


def test_scite_plan_is_or_batched_top_k_and_at_most_two_calls():
    its = items(10)
    plan = A.scite_plan(its, top=["H1", "H3", "H5"])
    assert plan["mcp_calls"] <= A.MAX_SCITE_CALLS == 2
    req = plan["requests"]
    assert [r["tool"] for r in req] == ["search_literature", "citation_graph"]
    term = req[0]["args"]["term"]
    assert " OR " in term
    assert "melatonin" in term and "atonia" in term and "olfaction" in term
    assert "clonazepam" not in term                  # H2 is not in the top-k
    assert req[1]["conditional"]
    assert A.scite_plan(its, top=[])["requests"] == []


def test_scite_check_spends_at_most_two_calls_and_matches_back():
    mcp = FakeMCP()
    out = A.scite_check(items(10), top=["H1", "H3", "H5"], mcp=mcp, round_=1)
    assert len(mcp.calls) == 2 <= A.MAX_SCITE_CALLS
    assert [c[0] for c in mcp.calls] == ["search_literature", "citation_graph"]
    assert all(c[2] == 1 for c in mcp.calls)
    seeds = mcp.calls[1][1]["seeds"]
    assert seeds[0] == "10.1000/mel.1" and "10.1000/unrelated" not in seeds
    assert set(out["results"]) == {"H1", "H3", "H5"}
    h1 = out["results"]["H1"]
    assert [h["doi"] for h in h1["novelty_hits"]] == ["10.1000/mel.1"]
    quotes = [c["quote"] for c in h1["counter_evidence"]]
    assert "Melatonin showed no protective effect in our series." in quotes
    assert "We could not replicate the melatonin finding." in quotes
    assert [h["doi"] for h in out["results"]["H3"]["novelty_hits"]] == ["10.1000/olf.2"]
    assert out["results"]["H3"]["counter_evidence"] == []       # supporting edges only
    assert out["results"]["H5"]["novelty_hits"] == []
    assert out["status"] == "ok" and out["calls"] == 2


def test_scite_check_never_exceeds_two_calls_even_with_many_hypotheses():
    mcp = FakeMCP()
    A.scite_check(items(10), top=[f"H{i}" for i in range(1, 11)], mcp=mcp, round_=1)
    assert len(mcp.calls) <= 2


def test_scite_check_skips_graph_when_nothing_matched():
    mcp = FakeMCP()
    its = [{"id": "H1", "status": "ok", "abstract": "zebrafish cilia regenerate quickly"}]
    out = A.scite_check(its, top=["H1"], mcp=mcp, round_=1)
    assert [c[0] for c in mcp.calls] == ["search_literature"]
    assert out["results"]["H1"]["novelty_hits"] == []


def test_scite_check_quota_and_budget_stop_and_mark_rows():
    mcp = FakeMCP(fail={1: MCPFailure("quota", "QUOTA")})
    out = A.scite_check(items(3), top=["H1", "H2"], mcp=mcp, round_=1)
    assert len(mcp.calls) == 1
    assert out["status"] == "quota"
    assert all(r["scite_status"] == "quota" for r in out["results"].values())
    mcp = FakeMCP(fail={2: BudgetError("budget")})
    out = A.scite_check(items(3), top=["H1"], mcp=mcp, round_=1)
    assert len(mcp.calls) == 2 and out["status"] == "quota"
    assert out["skipped"][0]["reason"] == "budget"
    mcp = FakeMCP(fail={1: PolicyError("scite is off")})
    out = A.scite_check(items(3), top=["H1"], mcp=mcp, round_=1)
    assert out["status"] == "policy" and len(mcp.calls) == 1


def test_scite_check_sends_only_abstracted_ok_items():
    mcp = FakeMCP()
    its = items(2) + [{"id": "H3", "status": "refused", "reason": "leak"}]
    out = A.scite_check(its, top=["H3"], mcp=mcp, round_=1)
    assert mcp.calls == []
    assert out["results"]["H3"]["scite_status"] == "refused"
    mcp = FakeMCP()
    out = A.scite_check([{"id": "H1", "status": "ok", "abstract": "cohort_a 43 melatonin"}],
                        top=["H1"], mcp=mcp, round_=1)
    assert mcp.calls == [] and out["results"]["H1"]["scite_status"] == "refused"


# --- review findings (ticket 15) --------------------------------------------------------------

STILL_LEAKY = [
    "Melatonin slows conversion in one hundred and twelve patients.",
    "Melatonin slows conversion by one third.",
    "Melatonin slows conversion by half.",
    "Melatonin predicts conversion with an AUC of point nine.",
    "Melatonin halves conversion with HR around zero point five.",
    "Melatonin has an effect d of eight tenths on conversion.",
    "Melatonin slows conversion in \u216b patients.",
    "Melatonin slows conversion in XLIII patients.",
    "Melatonin slows conversion in f\u0456fty patients.",
    "Melatonin slows conversion, Cohen d large.",
    "Melatonin slows conversion, Cohen\u2019s d large.",
    "Melatonin slows conversion with SMD large.",
    "Melatonin slows conversion with a large standardized mean difference.",
    "Melatonin slows conversion with an odds-ratio above unity.",
    "Melatonin slows conversion with \u03b7\u00b2 large.",
    "Melatonin slows conversion by a factor of three.",
    "Melatonin slows conversion in the Mayo Clinic series.",
    "Melatonin slows conversion in subjects of the Brno sleep clinic.",
    "Melatonin slows conversion in the ICEBERG study.",
    "Melatonin slows conversion at the VA Palo Alto site.",
    "Melatonin slows conversion in Prague.",
    "Melatonin slows conversion among older Czech men.",
    "Melatonin slows conversion in Hispanic women living in Texas.",
    "Melatonin slows conversion in my patients.",
    "Melatonin slows conversion in PD12 participants.",
]
SECRETS = ["hundred", "twelve", "third", "half", "nine", "zero", "five", "eight", "tenths",
           "xii", "xliii", "f\u0456fty", "cohen", "smd", "standardized", "odds", "\u03b7",
           "factor", "mayo", "brno", "iceberg", "palo", "va", "prague", "czech", "hispanic",
           "texas", "my", "pd12", "point"]


@pytest.mark.parametrize("text", STILL_LEAKY)
def test_review_leaks_are_stripped_or_refused(text):
    try:
        out = A.abstract(text)
    except LeakError:
        return                                   # refused: nothing is sent
    words = set(re.findall(r"[^\W_]+", out.lower()))
    assert not words & set(SECRETS), (text, out)
    assert "melatonin" in words


def test_review_scientific_terms_survive():
    for text, keep in [
        ("Melatonin slows conversion in Parkinson disease.", "parkinson"),
        ("Type I interferon signalling precedes Lewy body pathology.", "lewy"),
        ("Stage III sleep loss precedes conversion.", "stage"),
        ("Mild cognitive impairment (MCI) precedes conversion.", "mci"),
        ("\u03b1-synuclein seeding in skin predicts conversion.", "synuclein"),
    ]:
        assert keep in A.abstract(text).lower(), text


def test_scite_check_caps_the_top_k():
    mcp = FakeMCP()
    out = A.scite_check(items(10), top=[f"H{i}" for i in range(1, 11)], mcp=mcp, round_=1,
                        top_k=8)
    assert set(out["results"]) == {f"H{i}" for i in range(1, 9)}
    term = mcp.calls[0][1]["term"]
    assert "orthostatic" not in term and "cognition" not in term     # H9, H10
    plan = A.scite_plan(items(10), top=[f"H{i}" for i in range(1, 11)], top_k=8)
    assert "cognition" not in plan["requests"][0]["args"]["term"]
    assert A.DEFAULT_TOP_K == 8


def test_one_leaky_top_item_is_refused_and_the_rest_go_ahead():
    its = items(2) + [{"id": "H3", "status": "ok", "abstract": "melatonin in cohort A"},
                      {"id": "H4", "status": "ok", "abstract": "the and of with"}]
    mcp = FakeMCP()
    out = A.scite_check(its, top=["H1", "H3", "H4", "H2"], mcp=mcp, round_=1)
    assert out["results"]["H3"]["scite_status"] == "refused"
    assert out["results"]["H4"]["scite_status"] == "refused"
    assert out["results"]["H1"]["scite_status"] == "ok"
    assert "cohort" not in mcp.calls[0][1]["term"]
    plan = A.scite_plan(its, top=["H1", "H3", "H4"])
    assert plan["refused"] == ["H3", "H4"]


def test_policy_refusal_is_a_boundary_not_an_outage():
    mcp = FakeMCP(fail={1: PolicyError("scite is off")})
    out = A.scite_check(items(2), top=["H1"], mcp=mcp, round_=1)
    assert out["status"] == "policy"
    assert out["results"]["H1"]["scite_status"] == "policy"
    assert len(mcp.calls) == 1
