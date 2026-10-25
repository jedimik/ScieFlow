"""M-A T1 (A-1/A-8): literature search goes through the provider Gateway.

Every `scieflow research search <source>` call is: policy (config/integrations.yml,
search sources on by default) -> leak check of the query -> cache -> rate limit ->
transport (no redirects) -> ledger. Mocked HTTP only; no network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import responses
import yaml

from scieflow.core.review.claims import LeakError
from scieflow.integrations.scite.policy import PolicyError
from scieflow.research.lib import http, providers
from scieflow.research.search import arxiv, crossref, europepmc, openalex, registry

REPO = Path(__file__).resolve().parents[2]
SEARCH_DIR = REPO / "src" / "scieflow" / "research" / "search"

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF_URL = "https://api.crossref.org/works"

SOURCES = {
    "openalex": (openalex, OPENALEX_URL),
    "arxiv": (arxiv, ARXIV_URL),
    "europepmc": (europepmc, EPMC_URL),
    "crossref": (crossref, CROSSREF_URL),
}
PROVIDER = {name: f"search-{name}" for name in SOURCES}

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Arxiv Demo Paper</title>
    <summary>An abstract.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v2" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


def _body(name: str):
    """(kind, body) of a well-formed answer from each source."""
    if name == "openalex":
        return "json", {"results": [{"display_name": "Demo", "publication_year": 2023}]}
    if name == "arxiv":
        return "text", ATOM
    if name == "europepmc":
        return "json", {"resultList": {"result": [{"title": "Demo", "pubYear": "2023"}]}}
    return "json", {"message": {"items": [{"title": ["Demo"]}]}}


def _mock(name: str, *, url_suffix: str = "", status: int | None = None, headers=None, body=None):
    url = SOURCES[name][1]
    pattern = re.compile(re.escape(url) + r".*")
    kind, default = _body(name)
    if status is not None:
        responses.add(responses.GET, pattern, status=status, headers=headers or {})
        return
    if kind == "text":
        responses.add(responses.GET, pattern, body=body if body is not None else default)
    else:
        responses.add(responses.GET, pattern, json=body if body is not None else default)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "agents.yml").write_text("{}\n", encoding="utf-8")
    ws = root / "workspace" / "demo"
    ws.mkdir(parents=True)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    monkeypatch.chdir(root)
    return root, ws, xdg


def _write_config(root: Path, cfg) -> None:
    (root / "config" / "integrations.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _exit_code(mod, argv) -> object:
    with pytest.raises(SystemExit) as exc:
        mod.main(argv)
    return exc.value.code


def _query_of(call, key: str):
    """`call` is a PreparedRequest (responses.calls[i].request); read one query parameter."""
    return parse_qs(urlsplit(call.url).query)[key]


# --- 1. data-shaped queries are refused before anything leaves ------------------------


@pytest.mark.parametrize("name", list(SOURCES))
@pytest.mark.parametrize("query", [
    "patient 4711 age 63", "cohort: RBD043", "/data/cohort/file.csv", "jane.doe@example.org",
])
@responses.activate
def test_data_shaped_queries_are_refused_before_any_request(project, capsys, name, query):
    mod, _ = SOURCES[name]
    code = _exit_code(mod, [query])
    assert code == 3
    assert len(responses.calls) == 0
    assert query not in capsys.readouterr().err  # the refusal names the kind, never the value


@responses.activate
def test_europepmc_year_is_composed_only_after_the_leak_check(project):
    assert _exit_code(europepmc, ["patient 4711 age 63", "--from-year", "2020"]) == 3
    assert len(responses.calls) == 0


# --- 2. redirects and other 3xx answers are refused ----------------------------------------


@pytest.mark.parametrize("name", list(SOURCES))
@responses.activate
def test_3xx_is_refused_and_the_location_is_never_requested(project, capsys, name):
    _mock(name, status=301, headers={"Location": "https://evil.example.org/x"})
    responses.add(responses.GET, "https://evil.example.org/x", json={})
    mod, _ = SOURCES[name]
    assert _exit_code(mod, ["type 2 diabetes"]) == 1
    assert len(responses.calls) == 1
    assert "HTTP_301" in capsys.readouterr().err


# --- 3. allowed queries are sent as the user typed them ------------------------------------


@pytest.mark.parametrize("query", [
    "type 2 diabetes", "parkinson*", "COVID-19 vaccine", "alpha-synuclein A53T",
])
@responses.activate
def test_allowed_queries_are_sent_unchanged(project, capsys, query):
    _mock("openalex")
    openalex.main([query, "--limit", "5"])
    assert json.loads(capsys.readouterr().out)[0]["title"] == "Demo"
    assert _query_of(responses.calls[0].request, "search") == [query]


def test_check_query_removes_only_word_final_wildcards_and_single_digits():
    assert registry.check_query("type 2 diabetes") == "type 2 diabetes"
    assert isinstance(registry.check_query("parkinson*"), providers.Checked)
    for bad in ("5-HT2A receptor", "patient 4711 age 63", "cohort: RBD043"):
        with pytest.raises(LeakError):
            registry.check_query(bad)


@pytest.mark.parametrize("query", [
    "patient 4 7 1 1 age 6 3",
    "RBD 0 4 3",
    "RBD 0 4 3 and 7",
    "2 0 2 3 cohort",
    "patient 4\u00a07\u00a01\u00a01",          # no-break spaces
    "patient \uff14 \uff17 \uff11 \uff11",      # full-width digits
    "patient 4\u200b7\u200b1\u200b1",           # zero-width spaces
])
def test_spaced_digit_runs_are_refused_as_numbers(query):
    with pytest.raises(LeakError, match="number"):
        registry.check_query(query)


@responses.activate
def test_spaced_digit_run_is_refused_before_any_request(project, capsys):
    assert _exit_code(openalex, ["patient 4 7 1 1 age 6 3"]) == 3
    assert len(responses.calls) == 0
    err = capsys.readouterr().err
    assert "still looks like data (number)" in err
    assert "4 7 1 1" not in err  # the refusal names the kind, never the value


# --- 4. policy: search on without a config file, discovery still off ----------------------


def test_absent_config_keeps_search_on_and_discovery_off(project):
    root, ws, home = project
    pol = providers.load(root)
    assert set(pol.enabled_providers()) == set(PROVIDER.values())
    for name in ("pubtator", "ctgov", "openalex", "europepmc"):
        assert not pol.allows(name)
        assert "config/integrations.yml" in pol.reason(name)


def test_discovery_provider_stays_refused_without_config(project):
    root, ws, home = project
    gw = providers.Gateway(ws, providers.load(root), sleep=lambda s: None)
    with pytest.raises(providers.ProviderRefused):
        gw.check("openalex", OPENALEX_URL)


@responses.activate
def test_switching_one_search_source_off_refuses_it(project, capsys):
    root, _, _ = project
    _write_config(root, {"providers": {"search-openalex": {"enabled": False}}})
    assert _exit_code(openalex, ["type 2 diabetes"]) == 3
    assert len(responses.calls) == 0
    _mock("arxiv")
    arxiv.main(["demo"])  # the other sources keep working
    assert len(responses.calls) == 1


def test_host_outside_the_catalogue_is_a_load_error(project, capsys):
    root, _, _ = project
    _write_config(root, {"providers": {"search-openalex": {
        "enabled": True, "hosts": ["evil.example.org"]}}})
    with pytest.raises(PolicyError, match="not in the search-openalex catalogue"):
        providers.load(root)
    assert _exit_code(openalex, ["demo"]) == 3


# --- 5. cache, ledgers and events -----------------------------------------------------------


@responses.activate
def test_second_identical_call_is_a_cache_hit_with_ledger_rows(project, capsys):
    root, ws, xdg = project
    _mock("openalex")
    openalex.main(["type 2 diabetes", "--workspace", "demo"])
    openalex.main(["type 2 diabetes", "--workspace", "demo"])
    assert len(responses.calls) == 1
    run_rows = [json.loads(x) for x in (ws / "scite" / "ledger.jsonl").read_text().splitlines()]
    assert [(r["source"], r["cached"]) for r in run_rows] == [
        (PROVIDER["openalex"], False), (PROVIDER["openalex"], True)]
    day_files = list((xdg / "scieflow" / "scite").glob("ledger-*.jsonl"))
    assert len(day_files) == 1 and len(day_files[0].read_text().splitlines()) == 2
    events = [json.loads(x) for x in (ws / "events.jsonl").read_text().splitlines()]
    calls = [e for e in events if e["type"] == "integration.call"]
    # a cache hit is ledgered (run + day) but is not a new integration.call, as for the discovery providers
    assert [e["data"]["source"] for e in calls] == [PROVIDER["openalex"]]


@responses.activate
def test_unscoped_search_writes_the_day_ledger_only(project, capsys):
    root, ws, xdg = project
    _mock("arxiv")
    arxiv.main(["demo"])
    assert not (ws / "scite" / "ledger.jsonl").exists()
    assert not (ws / "events.jsonl").exists()
    assert len(list((xdg / "scieflow" / "scite").glob("ledger-*.jsonl"))) == 1


@responses.activate
def test_malformed_body_is_ledgered_as_an_error_and_never_cached(project, capsys):
    _mock("openalex", body=["not", "an", "object"])
    _mock("openalex", body=["not", "an", "object"])
    assert _exit_code(openalex, ["demo"]) == 1
    assert _exit_code(openalex, ["demo"]) == 1
    assert len(responses.calls) == 2
    assert "MALFORMED" in capsys.readouterr().err


# --- 6. query composition is typed and checked before it is sent ----------------------------


@responses.activate
def test_europepmc_year_and_arxiv_prefix_are_composed(project, capsys):
    _mock("europepmc")
    europepmc.main(["type 2 diabetes", "--from-year", "2020"])
    assert _query_of(responses.calls[0].request, "query") == [
        "type 2 diabetes AND PUB_YEAR:[2020 TO 3000]"]
    _mock("arxiv")
    arxiv.main(["type 2 diabetes"])
    assert _query_of(responses.calls[1].request, "search_query") == ["all:type 2 diabetes"]


def test_non_integer_year_is_refused_by_the_parser(project):
    with pytest.raises(SystemExit) as exc:
        europepmc.main(["demo", "--from-year", "twenty"])
    assert exc.value.code == 2
    assert len(responses.calls) == 0


def test_plain_strings_cannot_be_forged_as_checked_or_composed(project):
    root, ws, _ = project
    with pytest.raises(TypeError):
        providers.Checked("plain text")
    with pytest.raises(TypeError):
        registry.arxiv_query("plain text")
    gw = providers.Gateway(ws, providers.load(root), sleep=lambda s: None)
    with pytest.raises(ValueError):
        gw.get_json(PROVIDER["openalex"], "search", OPENALEX_URL, checked={"search": "plain"})
    with pytest.raises(ValueError):
        gw.get_json(PROVIDER["europepmc"], "search", EPMC_URL,
                    composed={"query": registry.check_query("demo")})


# --- 7. the Gateway still refuses discovery without a run; search may run unscoped ----------


def test_gateway_without_a_run_is_refused_but_unscoped_search_is_allowed(project):
    root, ws, home = project
    pol = providers.load(root)
    with pytest.raises(PolicyError, match="run"):
        providers.Gateway(None, pol)
    gw = providers.Gateway.unscoped(pol)
    assert gw.ws is None


# --- 8. the old unguarded http.get is gone -----------------------------------------------------


def test_no_unguarded_get_remains_in_the_search_modules():
    for path in SEARCH_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                assert not (node.func.attr == "get" and isinstance(owner, ast.Name)
                            and owner.id in {"http", "requests"}), f"{path.name}:{node.lineno}"
    assert not hasattr(http, "get")
