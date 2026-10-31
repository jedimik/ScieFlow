"""M-C1 t4 (plan 5.2, Part B t4 items 1-5, 7): `scieflow research search all`.

Every transport is faked by the `responses` library (synthetic answers, no real service). The
fixture forces one attempt per request and no rate-limit waits, so failures are immediate.
Synthetic bibliographic records only; no patient data.
"""

from __future__ import annotations

import json
import re

import pytest
import responses
import yaml

from scieflow.research.lib import providers
from scieflow.research.search import crossref, fanout

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF_URL = "https://api.crossref.org/works"
URLS = {"openalex": OPENALEX_URL, "arxiv": ARXIV_URL, "europepmc": EPMC_URL,
        "crossref": CROSSREF_URL}
QUERY = "type 2 diabetes"

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Arxiv Demo Paper</title>
    <summary>An abstract.</summary>
    <published>2024-01-02T00:00:00Z</published>
  </entry>
</feed>"""


def oa(title, doi=None, year=2023):
    return {"display_name": title, "publication_year": year,
            "doi": f"https://doi.org/{doi}" if doi else None}


def cr(title, doi=None, year=2023):
    return {"DOI": doi, "title": [title], "published": {"date-parts": [[year]]}}


def epmc(title, doi=None):
    return {"title": title, "pubYear": "2023", "doi": doi}


DEFAULTS = {
    "openalex": {"json": {"results": [oa("Alpha synuclein in Parkinson disease",
                                         "10.5555/alpha")]}},
    "crossref": {"json": {"message": {"items": [cr("Beta gut microbiota study",
                                                   "10.5555/beta")]}}},
    "europepmc": {"json": {"resultList": {"result": [epmc("Gamma trial", "10.5555/gamma")]}}},
    "arxiv": {"body": ATOM},
}


def serve(**over):
    """Register one answer per source. `over[name]` replaces that source's answer."""
    for name, url in URLS.items():
        spec = over.get(name, DEFAULTS[name])
        responses.add(responses.GET, re.compile(re.escape(url) + r".*"), **spec)


def requested(name):
    return [c for c in responses.calls if c.request.url.startswith(URLS[name])]


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
def fast(monkeypatch):
    """One attempt per request, no sleeps (the real retry and limiter logic still runs)."""
    real = providers.http.send

    def send(*args, **kwargs):
        kwargs["retries"] = 1
        kwargs["sleep"] = lambda seconds: None
        return real(*args, **kwargs)

    monkeypatch.setattr(providers.http, "send", send)
    monkeypatch.setattr(providers.http.RateLimiter, "wait", lambda self: None)


def write_integrations(root, providers_cfg):
    (root / "config" / "integrations.yml").write_text(
        yaml.safe_dump({"providers": providers_cfg}), encoding="utf-8")


def declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")


def write_l2_policy(root, providers_list):
    (root / "config" / "data-policy.yml").write_text(yaml.safe_dump({
        "version": 1, "l2_providers": [{"name": n, "reason": "test: reviewed"}
                                       for n in providers_list]}), encoding="utf-8")


def run(argv, capsys):
    """(exit code, parsed JSON or None, stdout, stderr) of one `search all` command."""
    try:
        fanout.main(argv)
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    payload = None
    if captured.out.strip().startswith("{"):
        payload = json.loads(captured.out)
    return code, payload, captured.out, captured.err


def status_of(payload, name):
    return next(s for s in payload["sources"] if s["source"] == name)


# --- 1. one source failing never sinks the others (GAP acceptance) -----------------------


@pytest.mark.parametrize("failure, expected_error", [
    ({"status": 429, "headers": {"Retry-After": "0"}}, "RATE"),
    ({"status": 500}, None),
    ({"json": {"message": {"items": "not a list"}}}, "MALFORMED"),
])
@responses.activate
def test_one_failing_source_leaves_the_others_answering(project, fast, capsys, failure,
                                                        expected_error):
    serve(crossref=failure)
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 0
    crossref_status = status_of(payload, "crossref")
    assert crossref_status["status"] == "failed"
    if expected_error:
        assert crossref_status["error"] == expected_error
    else:
        assert crossref_status["error"]
    for name in ("openalex", "europepmc", "arxiv"):
        assert status_of(payload, name)["status"] == "ok"
    titles = {r["title"] for r in payload["records"]}
    assert {"Alpha synuclein in Parkinson disease", "Gamma trial", "Arxiv Demo Paper"} <= titles
    assert "Beta gut microbiota study" not in titles


@responses.activate
def test_an_unexpected_exception_in_one_source_is_failed_and_never_echoes_the_query(
        project, fast, capsys, monkeypatch):
    serve()

    def broken(item):
        raise RuntimeError(f"boom while reading {QUERY}")

    monkeypatch.setattr(crossref, "norm", broken)
    code, payload, out, err = run([QUERY], capsys)
    assert code == 0
    crossref_status = status_of(payload, "crossref")
    assert crossref_status == {"source": "crossref", "status": "failed", "count": 0,
                               "error": "ERROR"}
    assert QUERY not in out and QUERY not in err


# --- 2. off sources are not asked; a default-off source stays off until listed (D16) ----


@responses.activate
def test_a_source_disabled_in_config_is_off_and_never_asked(project, fast, capsys):
    root, _ = project
    write_integrations(root, {"search-crossref": {"enabled": False}})
    serve()
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 0
    assert status_of(payload, "crossref")["status"] == "off"
    assert requested("crossref") == []


@responses.activate
def test_a_default_off_catalogue_source_stays_off_until_listed_enabled(project, fast, capsys,
                                                                       monkeypatch):
    root, _ = project
    monkeypatch.setitem(providers.CATALOGUE["search-crossref"], "default_enabled", False)
    serve()
    code, payload, _, _ = run([QUERY], capsys)
    assert status_of(payload, "crossref")["status"] == "off"
    assert requested("crossref") == []

    write_integrations(root, {"search-crossref": {"enabled": True}})
    responses.calls.reset()
    code, payload, _, _ = run([QUERY], capsys)
    assert status_of(payload, "crossref")["status"] == "ok"
    assert requested("crossref")


# --- 3. data protection: an L2 run refuses every unlisted source before any request ------


@responses.activate
def test_an_l2_run_refuses_every_source_before_any_request(project, fast, capsys):
    root, ws = project
    declare_l2(ws)
    serve()
    code, payload, _, _ = run([QUERY, "--workspace", "demo"], capsys)
    assert code == 3
    assert [s["status"] for s in payload["sources"]] == ["refused"] * 4
    assert responses.calls.__len__() == 0


@responses.activate
def test_an_l2_run_still_asks_a_listed_source(project, fast, capsys):
    root, ws = project
    declare_l2(ws)
    write_l2_policy(root, ["search-openalex"])
    serve()
    code, payload, _, _ = run([QUERY, "--workspace", "demo"], capsys)
    assert code == 0
    assert status_of(payload, "openalex")["status"] == "ok"
    for name in ("crossref", "europepmc", "arxiv"):
        assert status_of(payload, name)["status"] == "refused"
    assert requested("crossref") == []


# --- 4. exit rules of 5.2 -------------------------------------------------------------


@responses.activate
def test_every_source_a_snapshot_miss_exits_six_and_sends_nothing(project, fast, capsys):
    _, ws = project
    serve()
    code, payload, _, _ = run([QUERY, "--workspace", "demo", "--network", "replay"], capsys)
    assert code == 6
    assert [s["status"] for s in payload["sources"]] == ["offline-miss"] * 4
    assert responses.calls.__len__() == 0


@responses.activate
def test_a_switched_off_source_with_the_rest_failing_exits_three(project, fast, capsys):
    root, _ = project
    write_integrations(root, {"search-openalex": {"enabled": False}})
    serve(crossref={"status": 500}, europepmc={"status": 500}, arxiv={"status": 500})
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 3
    assert status_of(payload, "openalex")["status"] == "off"


@responses.activate
def test_every_source_failing_exits_one(project, fast, capsys):
    serve(**{name: {"status": 500} for name in URLS})
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 1
    assert all(s["status"] == "failed" for s in payload["sources"])


@responses.activate
def test_a_query_refused_by_the_leak_check_exits_three_before_any_source(project, fast, capsys):
    serve()
    code, payload, out, err = run(["patient 4711 age 63"], capsys)
    assert code == 3 and payload is None
    assert responses.calls.__len__() == 0
    assert "4711" not in err


def test_an_unknown_source_name_is_a_usage_error(project, capsys):
    with pytest.raises(SystemExit) as exc:
        fanout.main([QUERY, "--sources", "openalex,pubmd"])
    assert exc.value.code == 2


# --- 5. dedupe and rank (GAP acceptance: the gold cases through the fan-out) -----------


@responses.activate
def test_the_same_doi_from_two_sources_is_one_record_naming_both(project, fast, capsys):
    serve(openalex={"json": {"results": [oa("Alpha synuclein study", "10.5555/same")]}},
          crossref={"json": {"message": {"items": [cr("Alpha synuclein study",
                                                       "10.5555/same")]}}})
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 0
    merged = [r for r in payload["records"] if r["title"] == "Alpha synuclein study"]
    assert len(merged) == 1
    kern = merged[0]["signals"]["kernel"]
    assert kern["sources"] == ["openalex", "crossref"]
    assert kern["score"] > 0
    assert [d["reason"] for d in payload["dedup"]] == ["doi_exact"]


@responses.activate
def test_a_preprint_without_a_doi_merges_with_its_version_of_record(project, fast, capsys):
    serve(openalex={"json": {"results": [
        oa("Gene regulation in REM sleep behaviour disorder", None)]}},
          crossref={"json": {"message": {"items": [
              cr("Gene regulation in REM sleep behavior disorder", "10.5555/vor")]}}})
    code, payload, _, _ = run([QUERY], capsys)
    assert code == 0
    gene = [r for r in payload["records"] if r["title"].startswith("Gene regulation")]
    assert len(gene) == 1
    assert gene[0]["doi"] == "10.5555/vor"
    assert gene[0]["signals"]["kernel"]["sources"] == ["openalex", "crossref"]


@responses.activate
def test_two_different_dois_with_similar_titles_are_never_merged(project, fast, capsys):
    serve(openalex={"json": {"results": [oa("Alpha synuclein in Parkinson disease",
                                            "10.5555/one")]}},
          crossref={"json": {"message": {"items": [cr("Alpha synuclein in Parkinson disease",
                                                       "10.5555/two")]}}})
    code, payload, _, _ = run([QUERY], capsys)
    same_title = [r for r in payload["records"]
                  if r["title"] == "Alpha synuclein in Parkinson disease"]
    assert len(same_title) == 2
    assert payload["dedup"] == []


# --- 6. determinism, --top, and no query echo ------------------------------------------


@responses.activate
def test_the_same_snapshots_give_byte_identical_output_in_any_source_order(project, fast,
                                                                          capsys):
    serve()
    _, first, out_a, _ = run([QUERY], capsys)
    _, _, out_b, _ = run([QUERY], capsys)
    _, _, out_c, _ = run([QUERY, "--sources", "arxiv,crossref,europepmc,openalex"], capsys)
    assert out_a == out_b == out_c
    assert first["kernel"] == 1


@responses.activate
def test_top_limits_the_records_after_ranking(project, fast, capsys):
    serve()
    code, payload, _, _ = run([QUERY, "--top", "1"], capsys)
    assert code == 0 and len(payload["records"]) == 1


@responses.activate
def test_the_output_never_carries_the_query(project, fast, capsys):
    serve()
    _, payload, out, err = run([QUERY], capsys)
    assert QUERY not in out and QUERY not in err
    assert "query" not in payload


# --- 7. review fixes (M-C1 t4 review r1): rank uses both source ranks; replay of a recording ---


@responses.activate
def test_a_record_two_sources_confirm_outranks_the_same_record_from_one(project, fast, capsys,
                                                                       monkeypatch, tmp_path):
    # openalex ranks the shared DOI third (behind two others); the scores are min-max normalised
    # per result set, so the position is the observable effect of the second source's rank.
    shared = oa("Shared rank study", "10.5555/shared")
    openalex_answer = {"json": {"results": [oa("Other openalex A", "10.5555/a"),
                                            oa("Other openalex B", "10.5555/b"), shared]}}
    crossref_alone = {"json": {"message": {"items": []}}}
    crossref_confirms = {"json": {"message": {"items": [cr("Shared rank study",
                                                          "10.5555/shared")]}}}

    serve(openalex=openalex_answer, crossref=crossref_alone)
    _, one, _, _ = run([QUERY], capsys)
    # A fresh cache for the second run: the Gateway would otherwise replay run one's empty crossref.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-second-run"))
    responses.reset()
    serve(openalex=openalex_answer, crossref=crossref_confirms)
    _, two, _, _ = run([QUERY], capsys)

    def position(payload):
        return [r["title"] for r in payload["records"]].index("Shared rank study")

    merged = next(r for r in two["records"] if r["title"] == "Shared rank study")
    assert merged["signals"]["kernel"]["sources"] == ["openalex", "crossref"]
    assert position(two) == 0
    assert position(one) > 0


@responses.activate
def test_a_recorded_search_replays_byte_identical_with_zero_transport_calls(project, fast,
                                                                           capsys):
    serve()
    code, _, recorded, _ = run([QUERY, "--workspace", "demo", "--network", "record"], capsys)
    assert code == 0
    responses.reset()                 # no registrations left: any transport call is unmatched
    responses.calls.reset()
    for mode in ("replay", "offline"):
        code, _, out, _ = run([QUERY, "--workspace", "demo", "--network", mode], capsys)
        assert code == 0
        assert out == recorded
    assert len(responses.calls) == 0
