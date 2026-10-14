"""M3 free discovery providers (T3.1): the deny-by-default provider gateway
(config/integrations.yml), PubTator3 A->B->C open links, ClinicalTrials.gov
completed-but-unpublished trials, OpenAlex trend breaks and co-citation
bridges. Recorded fixtures (tests/fixtures/discovery/), no network."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
import responses
import yaml

from scieflow.core import events
from scieflow.core.review.claims import LeakError
from scieflow.integrations.scite.cache import Cache
from scieflow.integrations.scite.policy import PolicyError, exit_code
from scieflow.research.lib import ctgov, openalex_trends, providers, pubtator
from scieflow.research.lib.http import HTTPFailure

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "discovery"
PT = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
CT = "https://clinicaltrials.gov/api/v2/studies"
OA = "https://api.openalex.org/works"

RBD = "@DISEASE_REM_Sleep_Behavior_Disorder"


def fx(rel):
    return json.loads((FIX / rel).read_text())


def nosleep(_s):
    pass


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    return root, ws, tmp_path / "cachehome"


def enable(root, cfg=None):
    if cfg is None:
        shutil.copy(REPO / "config" / "integrations.example.yml",
                    root / "config" / "integrations.yml")
    else:
        (root / "config" / "integrations.yml").write_text(yaml.safe_dump(cfg))
    return providers.load(root)


def gateway(proj, cfg=None, **kw):
    root, ws, home = proj
    pol = enable(root, cfg)
    kw.setdefault("sleep", nosleep)
    return providers.Gateway(ws, pol, cache=Cache(home / "cache", pol.cache_ttl_days),
                             ledger_dir=home / "day", **kw)


# --- policy: deny by default ------------------------------------------------------------


def test_absent_config_means_every_provider_off(proj):
    root, ws, home = proj
    pol = providers.load(root)
    assert pol.enabled_providers() == []
    for name in providers.CATALOGUE:
        assert not pol.allows(name)
        assert "config/integrations.yml" in pol.reason(name)


@responses.activate
def test_absent_config_refuses_before_any_request(proj):
    root, ws, home = proj
    gw = providers.Gateway(ws, providers.load(root), cache=Cache(home / "c", {"metadata": 1,
                           "search": 1}), ledger_dir=home / "day", sleep=nosleep)
    with pytest.raises(providers.ProviderRefused) as exc:
        pubtator.relations(gw, RBD)
    assert isinstance(exc.value, PolicyError) and exit_code(exc.value) == 3
    assert len(responses.calls) == 0
    assert not (ws / "scite" / "ledger.jsonl").exists()


def test_shipped_example_enables_the_three_catalogue_providers(proj):
    root, _, _ = proj
    pol = enable(root)
    assert pol.enabled_providers() == ["ctgov", "openalex", "pubtator"]
    for name, spec in providers.CATALOGUE.items():
        assert set(pol.hosts(name)) == set(spec["hosts"])


def test_example_is_gitignored_copy_target():
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert "config/integrations.yml" in ignored


@pytest.mark.parametrize("cfg,match", [
    ({"providers": {"pubmed": {"enabled": True}}}, "unknown provider"),
    ({"providers": {}, "extra": 1}, "unknown key"),
    ({"providers": {"openalex": {"enabled": True, "hosts": ["evil.example.org"]}}},
     "not in the openalex catalogue"),
    ({"providers": {"openalex": {"enabled": "yes"}}}, "enabled must be true or false"),
    ({"providers": {"openalex": {"enabled": True, "rate_per_s": 0}}}, "rate_per_s"),
    ({"providers": {"openalex": {"enabled": True, "colour": "red"}}}, "unknown key"),
    ({"providers": {"openalex": {"enabled": True}}, "cache_ttl_days": {"forever": 1}},
     "cache_ttl_days"),
    ("just a string", "mapping"),
])
def test_bad_config_refused(proj, cfg, match):
    root, _, _ = proj
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(PolicyError, match=match):
        providers.load(root)


def test_disabled_or_unlisted_provider_is_off(proj):
    root, _, _ = proj
    pol = enable(root, {"providers": {"openalex": {"enabled": True},
                                      "ctgov": {"enabled": False}}})
    assert pol.enabled_providers() == ["openalex"]
    assert "disabled" in pol.reason("ctgov")
    assert "not listed" in pol.reason("pubtator")


@responses.activate
def test_host_outside_the_provider_list_is_refused(proj):
    gw = gateway(proj, {"providers": {"openalex": {"enabled": True, "hosts": []},
                                      "pubtator": {"enabled": True}}})
    with pytest.raises(providers.ProviderRefused, match="host"):
        openalex_trends.counts_by_year(gw, "gut microbiome")
    # a provider may only reach its own hosts, whatever URL a caller builds
    with pytest.raises(providers.ProviderRefused, match="host"):
        gw.get_json("pubtator", "relations", OA)
    with pytest.raises(providers.ProviderRefused, match="https"):
        gw.get_json("pubtator", "relations", PT.replace("https", "http") + "/relations")
    assert len(responses.calls) == 0


@responses.activate
@pytest.mark.parametrize("term", ["RBD cohort_a n = 43", "p<.001", "95% (CI 90.3–97.6)",
                                  "{{file:../../config/scite.yml}}", "data: visits.csv",
                                  "RBD043 melatonin"])
def test_free_text_that_looks_like_data_never_leaves(proj, term):
    gw = gateway(proj)
    with pytest.raises(LeakError):
        pubtator.autocomplete(gw, term)
    with pytest.raises(LeakError):
        ctgov.search_completed(gw, term)
    with pytest.raises(LeakError):
        openalex_trends.counts_by_year(gw, term)
    assert len(responses.calls) == 0


@responses.activate
def test_entity_ids_are_shape_checked(proj):
    gw = gateway(proj)
    for bad in ["melatonin", "@CHEMICAL_../../x", "@DISEASE_a b", "@GENE_" + "x" * 200,
                # data dressed up as an entity id: refused like the same free text
                "@DISEASE_cohort_a_n_43", "@CHEMICAL_visits.csv", "@GENE_RBD043_p_.001",
                "@DISEASE_95.3_CI_90.3-97.6", "@DISEASE_sub-01_Day-28"]:
        with pytest.raises(LeakError):
            pubtator.relations(gw, bad)
    assert len(responses.calls) == 0


@responses.activate
def test_entity_ids_the_provider_returned_may_be_sent_back(proj):
    odd = "@CHEMICAL_6_sulfatoxymelatonin"       # digit-led: refused as caller input
    gw = gateway(proj)
    with pytest.raises(LeakError):
        pubtator.relations(gw, odd)
    assert len(responses.calls) == 0
    responses.add(responses.GET, f"{PT}/entity/autocomplete/",
                  json=[{"_id": odd, "biotype": "chemical", "name": "6-sulfatoxymelatonin"}])
    responses.add(responses.GET, f"{PT}/relations", json=[])
    [hit] = pubtator.autocomplete(gw, "sulfatoxymelatonin")
    assert pubtator.relations(gw, hit["id"]) == []
    assert responses.calls[-1].request.params == {"e1": odd}


def test_every_parameter_is_classified(proj):
    gw = gateway(proj)
    with pytest.raises(TypeError):
        gw.get_json("openalex", "x", OA, {"search": "free text"})   # unclassified params
    with pytest.raises(ValueError, match="fixed"):
        gw.get_json("openalex", "x", OA, fixed={"q": ["not", "a", "scalar"]})


@responses.activate
def test_redirects_are_never_followed(proj):
    root, ws, home = proj
    responses.add(responses.GET, f"{PT}/relations", status=302,
                  headers={"Location": "https://evil.example.org/x"})
    responses.add(responses.GET, "https://evil.example.org/x", json=[])
    gw = gateway(proj)
    with pytest.raises(HTTPFailure) as exc:
        pubtator.relations(gw, RBD)
    assert exc.value.classification == "HTTP_302"
    assert [c.request.url.split("?")[0] for c in responses.calls] == [f"{PT}/relations"]
    rows = [json.loads(x) for x in (ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert [(r["status"], r["error"]) for r in rows] == [("error", "HTTP_302")]


def test_requests_belong_to_a_run(proj):
    root, _, home = proj
    pol = enable(root)
    with pytest.raises(PolicyError, match="run"):
        providers.Gateway(None, pol, cache=Cache(home / "c", pol.cache_ttl_days))


# --- gateway: cache, ledger, events, rate limit, failures ---------------------------------


@responses.activate
def test_cache_ledger_and_event(proj):
    root, ws, home = proj
    responses.add(responses.GET, f"{PT}/relations", json=fx("pubtator/relations_rbd.json"))
    gw = gateway(proj)
    first = pubtator.relations(gw, RBD)
    again = pubtator.relations(gw, RBD)
    assert first == again and len(responses.calls) == 1
    rows = [json.loads(x) for x in (ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert [(r["source"], r["op"], r["cached"], r["status"]) for r in rows] == [
        ("pubtator", "relations", False, "ok"), ("pubtator", "relations", True, "ok")]
    assert rows[0]["results"] == 20 and rows[0]["request"]["e1"] == RBD
    day = list((home / "day").glob("ledger-*.jsonl"))
    assert len(day) == 1 and len(day[0].read_text().splitlines()) == 2
    evs = events.read(ws, types=("integration.call",))
    assert len(evs) == 1
    assert evs[0]["data"]["integration"] == "pubtator" and evs[0]["data"]["results"] == 20


@responses.activate
def test_provider_rows_do_not_spend_the_scite_budget(proj):
    from scieflow.integrations.scite import ledger as scite_ledger
    from scieflow.integrations.scite import policy as scite_policy

    root, ws, home = proj
    responses.add(responses.GET, f"{PT}/relations", json=fx("pubtator/relations_rbd.json"))
    pubtator.relations(gateway(proj), RBD)
    (root / "config" / "scite.yml").write_text(yaml.safe_dump({"max_mcp_calls_per_run": 2}))
    led = scite_ledger.Ledger(ws, scite_policy.load(root, environ={}), day_dir=home / "day")
    assert led.remaining(1)["run"] == 2 and led.calls("run", source="pubtator") == 1


@responses.activate
def test_rate_limit_spaces_requests_per_provider(proj):
    sleeps = []
    t = [100.0]
    responses.add(responses.GET, f"{OA}", json=fx("openalex/years_rbd.json"))
    gw = gateway(proj, {"providers": {"openalex": {"enabled": True, "rate_per_s": 2}}},
                 sleep=sleeps.append, clock=lambda: t[0])
    openalex_trends.counts_by_year(gw, "REM sleep behavior disorder")
    openalex_trends.counts_by_year(gw, "gut microbiome")
    assert sleeps == [pytest.approx(0.5)]


@responses.activate
def test_failure_is_ledgered_classified_and_not_cached(proj):
    root, ws, home = proj
    responses.add(responses.GET, f"{CT}", status=503)
    gw = gateway(proj)
    with pytest.raises(HTTPFailure) as exc:
        ctgov.search_completed(gw, "REM sleep behavior disorder")
    assert exc.value.classification == "TRANSIENT"
    assert len(responses.calls) == 3          # bounded retries, then a boundary
    rows = [json.loads(x) for x in (ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert [(r["source"], r["status"], r["error"]) for r in rows] == [
        ("ctgov", "error", "TRANSIENT")]
    responses.replace(responses.GET, f"{CT}", json=fx("ctgov/studies_rbd_completed_p1.json"))
    ctgov.search_completed(gw, "REM sleep behavior disorder", max_pages=1)
    assert len(responses.calls) == 4          # the failure was not cached


@responses.activate
def test_malformed_body_is_a_classified_failure(proj):
    responses.add(responses.GET, f"{PT}/relations", body="<html>oops</html>")
    with pytest.raises(HTTPFailure) as exc:
        pubtator.relations(gateway(proj), RBD)
    assert exc.value.classification == "MALFORMED"


@responses.activate
@pytest.mark.parametrize("call,url,body", [
    (lambda gw: pubtator.relations(gw, RBD), f"{PT}/relations", {"detail": "unavailable"}),
    (lambda gw: pubtator.autocomplete(gw, "melatonin"), f"{PT}/entity/autocomplete/", {}),
    (lambda gw: ctgov.search_completed(gw, "insomnia"), CT, {"error": "busy"}),
    (lambda gw: openalex_trends.counts_by_year(gw, "insomnia"), OA, {"meta": {}}),
    (lambda gw: openalex_trends.references(gw, "insomnia"), OA, {"meta": {"count": 3}}),
])
def test_wrong_shape_is_ledgered_as_error_and_never_cached(proj, call, url, body):
    root, ws, home = proj
    responses.add(responses.GET, url, json=body)
    gw = gateway(proj)
    for _ in range(2):
        with pytest.raises(HTTPFailure) as exc:
            call(gw)
        assert exc.value.classification == "MALFORMED"
    assert len(responses.calls) == 2
    rows = [json.loads(x) for x in (ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert [(r["cached"], r["status"], r["error"]) for r in rows] == [
        (False, "error", "MALFORMED")] * 2


# --- PubTator3 ------------------------------------------------------------------------


@responses.activate
def test_autocomplete_recorded(proj):
    responses.add(responses.GET, f"{PT}/entity/autocomplete/", json=fx("pubtator/autocomplete_rbd.json"))
    hits = pubtator.autocomplete(gateway(proj), "REM sleep behavior", concept="disease", limit=3)
    assert hits == [{"id": RBD, "name": "REM Sleep Behavior Disorder", "biotype": "disease",
                     "db": "ncbi_mesh", "db_id": "D020187"}]
    q = responses.calls[0].request.params
    assert q == {"query": "REM sleep behavior", "concept": "disease", "limit": "3"}


@responses.activate
def test_relations_recorded_and_normalised(proj):
    responses.add(responses.GET, f"{PT}/relations", json=fx("pubtator/relations_rbd.json"))
    edges = pubtator.relations(gateway(proj), RBD, partner="GENE")
    assert responses.calls[0].request.params == {"e1": RBD, "e2": "GENE"}
    assert edges[0] == {"source": "@CHEMICAL_Clonazepam", "target": RBD, "type": "treat",
                        "publications": 108}
    assert all(set(e) == {"source", "target", "type", "publications"} for e in edges)


def _graph():
    """A fixture graph: A -> {B1, B2, B3}; B1 and B2 both reach C1 (open), B1 reaches C2
    (open), B3 reaches A's own neighbour B2 (known, not open) and A itself."""
    A, B1, B2, B3 = "@DISEASE_A", "@GENE_B1", "@CHEMICAL_B2", "@GENE_B3"
    C1, C2 = "@CHEMICAL_C1", "@DISEASE_C2"
    a_edges = [
        {"source": A, "target": B1, "type": "associate", "publications": 40},
        {"source": B2, "target": A, "type": "treat", "publications": 30},
        {"source": A, "target": B3, "type": "associate", "publications": 5},
    ]
    b_edges = {
        B1: [{"source": B1, "target": C1, "type": "associate", "publications": 12},
             {"source": B1, "target": C2, "type": "cause", "publications": 3},
             {"source": A, "target": B1, "type": "associate", "publications": 40}],
        B2: [{"source": C1, "target": B2, "type": "inhibit", "publications": 20}],
        B3: [{"source": B3, "target": B2, "type": "interact", "publications": 9}],
    }
    return A, a_edges, b_edges


def test_abc_open_links_on_a_fixture_graph():
    A, a_edges, b_edges = _graph()
    links = pubtator.abc_open_links(A, a_edges, b_edges)
    assert [lk["c"] for lk in links] == ["@CHEMICAL_C1", "@DISEASE_C2"]
    c1 = links[0]
    assert c1["a"] == A and c1["ac_publications"] == 0
    assert [(v["b"], v["ab_publications"], v["bc_publications"]) for v in c1["via"]] == [
        ("@CHEMICAL_B2", 30, 20), ("@GENE_B1", 40, 12)]
    assert c1["score"] == 20 + 12            # sum over B of min(AB, BC)
    # deterministic whatever the input order
    rev = {b: list(reversed(es)) for b, es in reversed(list(b_edges.items()))}
    assert pubtator.abc_open_links(A, list(reversed(a_edges)), rev) == links


def test_abc_thresholds_and_type_filter():
    A, a_edges, b_edges = _graph()
    assert [lk["c"] for lk in pubtator.abc_open_links(A, a_edges, b_edges, min_bc=10)] == [
        "@CHEMICAL_C1"]
    assert [lk["c"] for lk in pubtator.abc_open_links(A, a_edges, b_edges,
                                                       c_types=("DISEASE",))] == ["@DISEASE_C2"]
    assert pubtator.abc_open_links(A, a_edges, b_edges, min_ab=50) == []


@responses.activate
def test_open_links_end_to_end_on_recorded_relations(proj):
    files = {RBD: "relations_rbd.json", "@CHEMICAL_Clonazepam": "relations_clonazepam.json",
             "@GENE_SNCA": "relations_snca.json", "@CHEMICAL_Melatonin": "relations_melatonin.json"}

    def cb(request):
        return 200, {}, json.dumps(fx("pubtator/" + files[request.params["e1"]]))
    responses.add_callback(responses.GET, f"{PT}/relations", callback=cb)
    out = pubtator.open_links(gateway(proj), RBD, max_b=3)
    # B = the three best-published neighbours of RBD, in a fixed order
    assert out["b"] == ["@CHEMICAL_Clonazepam", "@GENE_SNCA", "@CHEMICAL_Melatonin"]
    assert len(responses.calls) == 4
    cs = [lk["c"] for lk in out["links"]]
    # bottleneck score min(AB, BC): the clonazepam neighbours (AB 108) lead, ties by id
    assert cs[:3] == ["@DISEASE_Epilepsy", "@DISEASE_Myoclonus", "@DISEASE_Seizures"]
    # melatonin neighbours RBD already has a (weak) relation with are known, not open
    for known in ("@CHEMICAL_Lipids", "@CHEMICAL_Reactive_Oxygen_Species",
                  "@CHEMICAL_Malondialdehyde"):
        assert known not in cs
    # relation types merge: A-B uses the best-published edge (clonazepam: treat, 108)
    sz = next(lk for lk in out["links"] if lk["c"] == "@DISEASE_Seizures")
    assert (sz["via"][0]["ab_type"], sz["via"][0]["ab_publications"]) == ("treat", 108)
    # PubTator3 extracts no disease-disease relations, so RBD -> Parkinson disease is
    # "open" by construction (documented limitation)
    pd = next(lk for lk in out["links"] if lk["c"] == "@DISEASE_Parkinson_Disease")
    assert pd["score"] == 92 and [v["b"] for v in pd["via"]] == ["@GENE_SNCA"]
    assert pd["via"][0]["bc_publications"] == 9947
    assert RBD not in cs and "@CHEMICAL_Clonazepam" not in cs and "@GENE_SNCA" not in cs
    assert "@DISEASE_Seizures" in cs and "@DISEASE_Inflammation" in cs
    assert all(lk["ac_publications"] == 0 for lk in out["links"])
    assert out == pubtator.open_links(gateway(proj), RBD, max_b=3)   # cached, deterministic


# --- ClinicalTrials.gov ------------------------------------------------------------------


def _ct_callback(request):
    page = 2 if request.params.get("pageToken") else 1
    return 200, {}, json.dumps(fx(f"ctgov/studies_rbd_completed_p{page}.json"))


@responses.activate
def test_ctgov_search_pages_and_normalises(proj):
    responses.add_callback(responses.GET, CT, callback=_ct_callback)
    res = ctgov.search_completed(gateway(proj), "REM sleep behavior disorder")
    studies = res["studies"]
    assert len(studies) == 24 and len(responses.calls) == 2   # stops at totalCount
    assert res["total"] == 24 and res["complete"] is True


    q = responses.calls[0].request.params
    assert q["query.cond"] == "REM sleep behavior disorder"
    assert q["filter.overallStatus"] == "COMPLETED"
    s = next(x for x in studies if x["nct_id"] == "NCT03072940")
    assert s["completion_date"] == "2015-12" and s["has_results"] is False
    assert s["publications"] == [{"type": "RESULT", "pmid": s["publications"][0]["pmid"]}]


@responses.activate
def test_ctgov_truncation_is_reported(proj):
    responses.add_callback(responses.GET, CT, callback=_ct_callback)
    res = ctgov.search_completed(gateway(proj), "REM sleep behavior disorder", max_pages=1)
    assert len(res["studies"]) == 12 and res["total"] == 24 and res["complete"] is False


def test_completed_trials_without_publications():
    raw = (fx("ctgov/studies_rbd_completed_p1.json")["studies"]
           + fx("ctgov/studies_rbd_completed_p2.json")["studies"])
    studies = [ctgov.normalise(s) for s in raw]
    gaps = ctgov.unpublished(studies, as_of=date(2026, 10, 14), grace_months=24)
    ids = [g["nct_id"] for g in gaps]
    # linked (RESULT / DERIVED) trials are never gaps; BACKGROUND references do not count
    for linked in ("NCT03072940", "NCT02708186", "NCT04006925", "NCT03255642", "NCT03595475",
                   "NCT07190287"):
        assert linked not in ids
    assert "NCT02312908" in ids                       # 11 BACKGROUND references only
    # completed within the grace period: too early to call unpublished
    for recent in ("NCT06738862", "NCT06812702", "NCT05976971", "NCT07609537"):
        assert recent not in ids
    assert ids[:2] == ["NCT01401413", "NCT00695136"]  # oldest completion first
    posted = gaps[1]                                  # results posted on the registry, no paper
    assert posted["completion_date"] == "2011-01" and posted["has_results"] is True
    # whole months since the END of the completion month (Jan 2011 -> 14 Oct 2026)
    assert posted["months_since_completion"] == (2026 - 2011) * 12 + (10 - 1) - 1
    assert "NCT03675282" in ids                       # 2024-04: 30 months, past the grace period
    assert ctgov.unpublished(list(reversed(studies)), as_of=date(2026, 10, 14)) == gaps
    assert len(ids) == 14


def test_grace_period_counts_whole_months():
    def s(d):
        return {"nct_id": "NCT1", "title": "t", "completion_date": d, "has_results": False,
                "publications": [], "status": "COMPLETED"}
    as_of = date(2026, 10, 1)
    assert ctgov.unpublished([s("2024-10-31")], as_of=as_of) == []    # 23 months and a day
    assert ctgov.unpublished([s("2024-10")], as_of=as_of) == []       # month end: not yet
    assert ctgov.unpublished([s("2024-09-30")], as_of=as_of)[0]["months_since_completion"] == 24
    assert ctgov.unpublished([s("2024-09-30")], as_of=date(2026, 9, 29)) == []
    assert ctgov.unpublished([s("2024")], as_of=date(2026, 12, 30)) == []   # year end
    assert ctgov.unpublished([s("2024")], as_of=date(2026, 12, 31)) != []
    assert ctgov.unpublished([s("2024-02-29")], as_of=date(2026, 2, 28)) == []


def test_unpublished_skips_unparseable_dates():
    s = {"nct_id": "NCT1", "title": "t", "completion_date": "sometime", "has_results": False,
         "publications": [], "status": "COMPLETED", "study_type": None, "phases": []}
    assert ctgov.unpublished([s], as_of=date(2026, 1, 1)) == []


# --- OpenAlex trends and bridges ----------------------------------------------------------


@responses.activate
def test_counts_by_year_recorded(proj):
    responses.add(responses.GET, OA, json=fx("openalex/years_rbd.json"))
    counts = openalex_trends.counts_by_year(gateway(proj), "REM sleep behavior disorder")
    assert counts[2025] == 7519 and counts[2023] == 6319
    assert all(isinstance(k, int) for k in counts)
    q = responses.calls[0].request.params
    assert q["search"] == "REM sleep behavior disorder" and q["group_by"] == "publication_year"


def test_trend_breaks_deterministic():
    counts = {y: 10 for y in range(2000, 2010)}
    counts.update({y: 40 for y in range(2010, 2016)})
    counts.update({y: 8 for y in range(2016, 2026)})
    counts[2026] = 1                                  # the incomplete current year
    breaks = openalex_trends.trend_breaks(counts, until_year=2025, window=3, ratio=2.0)
    assert [(b["year"], b["direction"]) for b in breaks] == [(2010, "surge"), (2016, "drop")]
    assert breaks[0]["before_mean"] == 10 and breaks[0]["after_mean"] == 40
    assert breaks[0]["ratio"] == 4.0
    shuffled = dict(sorted(counts.items(), reverse=True))
    assert openalex_trends.trend_breaks(shuffled, until_year=2025, window=3, ratio=2.0) == breaks


def test_trend_breaks_normalised_by_baseline():
    # steady growth of the whole literature is not a break once normalised
    base = {y: 1000 * 2 ** (y - 2000) for y in range(2000, 2012)}
    counts = {y: base[y] // 100 for y in base}
    assert openalex_trends.trend_breaks(counts, until_year=2011) != []
    assert openalex_trends.trend_breaks(counts, until_year=2011, baseline=base) == []


def test_trend_breaks_ignore_tiny_counts_and_gaps_are_zero():
    counts = {2000: 1, 2001: 0, 2002: 1, 2003: 3, 2004: 4, 2005: 3}
    assert openalex_trends.trend_breaks(counts, until_year=2005, min_count=5) == []
    sparse = {2000: 20, 2001: 20, 2002: 20, 2006: 20}  # 2003-2005 missing = 0
    breaks = openalex_trends.trend_breaks(sparse, until_year=2006, window=3)
    assert [(b["year"], b["direction"]) for b in breaks] == [(2003, "drop")]


@responses.activate
def test_trend_fetches_baseline_once(proj):
    def cb(request):
        name = "years_rbd.json" if "search" in request.params else "years_all.json"
        return 200, {}, json.dumps(fx("openalex/" + name))
    responses.add_callback(responses.GET, OA, callback=cb)
    gw = gateway(proj)
    out = openalex_trends.trend(gw, "REM sleep behavior disorder", until_year=2025)
    again = openalex_trends.trend(gw, "REM sleep behavior disorder", until_year=2025)
    assert out == again and len(responses.calls) == 2
    assert out["term"] == "REM sleep behavior disorder" and out["normalised"] is True
    assert max(out["counts"]) <= 2025
    base = fx("openalex/years_all.json")
    want = openalex_trends.trend_breaks(
        {int(g["key"]): g["count"] for g in fx("openalex/years_rbd.json")["group_by"]
         if int(g["key"]) <= 2025},
        until_year=2025, baseline={int(g["key"]): g["count"] for g in base["group_by"]})
    assert out["breaks"] == want and want, "the recorded series has at least one break"
    assert all(b["normalised"] for b in want)


def _refs(ids_lists, side="a"):
    return [{"id": f"W{side}{i}", "referenced_works": refs} for i, refs in enumerate(ids_lists)]


def test_bridges_deterministic():
    a = _refs([["X1", "X2", "X3"], ["X1", "X2"], ["X1", "Y9"]])
    b = _refs([["X1", "X3", "Z1"], ["X1", "X2", "X3"], ["X2", "X3", "X3"]], "b")
    out = openalex_trends.bridges(a, b, min_each=2)
    assert [(x["work"], x["a_citing"], x["b_citing"]) for x in out] == [
        ("X1", 3, 2), ("X2", 2, 2)]
    assert out[0]["score"] == 2
    assert openalex_trends.bridges(list(reversed(a)), list(reversed(b)), min_each=2) == out
    assert openalex_trends.bridges(a, b, min_each=1, limit=1) == [
        {"work": "X1", "a_citing": 3, "b_citing": 2, "score": 2}]


def test_papers_in_both_top_sets_are_not_bridges():
    shared = {"id": "https://openalex.org/WS", "referenced_works": ["X1", "X2"]}
    a = _refs([["X1"], ["X2"]]) + [shared]
    b = _refs([["X1"], ["X3"]], "b") + [dict(shared)]
    # without the shared paper X1 is cited once per side: no bridge at min_each=2
    assert openalex_trends.bridges(a, b, min_each=2) == []
    assert [x["work"] for x in openalex_trends.bridges(a, b, min_each=1)] == ["X1"]


@responses.activate
def test_bridge_gap_recorded(proj):
    def cb(request):
        s = request.params.get("search", "")
        name = ("overlap_rbd_synucleinopathy.json" if " AND " in s else
                "refs_rbd.json" if s == "REM sleep behavior disorder"
                else "refs_synucleinopathy.json")
        return 200, {}, json.dumps(fx("openalex/" + name))
    responses.add_callback(responses.GET, OA, callback=cb)
    gw = gateway(proj)
    out = openalex_trends.bridge_gap(gw, "REM sleep behavior disorder", "synucleinopathy", top=8)
    assert (out["n_a"], out["n_b"], out["n_ab"]) == (57160, 28632, 4481)
    assert out["overlap"] == round(4481 / 28632, 6)
    # two papers are top-cited in both literatures: direct overlap, not a bridge; their
    # shared references would otherwise make three spurious bridges at min_each=2
    assert out["shared_top"] == ["W2623521763", "W4206481149"]
    assert out["bridges"] == []
    one = openalex_trends.bridge_gap(gw, "REM sleep behavior disorder", "synucleinopathy",
                                     top=8, min_each=1)
    assert [b["work"] for b in one["bridges"]] == ["W2014670147", "W2146729934"]
    sent = [c.request.params.get("search") for c in responses.calls]
    assert '"REM sleep behavior disorder" AND "synucleinopathy"' in sent
    assert all(re.fullmatch(r"[\w\s\"]+", s) for s in sent)
