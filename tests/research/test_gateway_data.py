"""M-B t4, Part A 5.7: the provider Gateway refuses literature requests that would carry an L2
run's data. Mocked HTTP only; no network. Synthetic data only.
"""

import re

import pytest
import responses
import yaml

from scieflow.core import events
from scieflow.research.lib import providers
from scieflow.research.search import arxiv, crossref, europepmc, openalex

SOURCES = {"openalex": openalex, "arxiv": arxiv, "europepmc": europepmc, "crossref": crossref}
OPENALEX_URL = "https://api.openalex.org/works"


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


def declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False))


def write_policy(root, text):
    (root / "config" / "data-policy.yml").write_text(text)


def _exit_code(mod, argv):
    with pytest.raises(SystemExit) as exc:
        mod.main(argv)
    return exc.value.code


def _mock_openalex():
    responses.add(responses.GET, re.compile(re.escape(OPENALEX_URL) + r".*"),
                  json={"results": [{"display_name": "Demo", "publication_year": 2023}]})


# --- scoped searches ------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SOURCES))
@responses.activate
def test_every_search_source_refuses_an_l2_run_before_any_request(project, capsys, name):
    root, ws = project
    declare_l2(ws)
    assert _exit_code(SOURCES[name], ["type 2 diabetes", "--workspace", "demo"]) == 3
    assert len(responses.calls) == 0
    err = capsys.readouterr().err
    assert "data protection" in err and "l2_providers" in err


@responses.activate
def test_a_listed_provider_still_runs_for_an_l2_run(project):
    root, ws = project
    declare_l2(ws)
    write_policy(root, yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "search-openalex", "reason": "test: reviewed provider"}]}))
    _mock_openalex()
    openalex.main(["type 2 diabetes", "--workspace", "demo"])
    assert len(responses.calls) >= 1


@responses.activate
def test_invalid_policy_refuses_a_scoped_search(project):
    root, ws = project
    write_policy(root, "version: 1\nenforcement: maybe\n")
    assert _exit_code(openalex, ["type 2 diabetes", "--workspace", "demo"]) == 3
    assert len(responses.calls) == 0


# --- the Gateway itself ---------------------------------------------------------------------


def test_gateway_check_refuses_every_catalogue_provider_for_an_l2_run(project):
    root, ws = project
    declare_l2(ws)
    gw = providers.Gateway(ws, providers.load(root))
    for name, spec in providers.CATALOGUE.items():
        with pytest.raises(providers.ProviderRefused, match="data protection"):
            host = spec["hosts"][0] if spec["hosts"] else "guidelines.journal.example"  # M-F 5.7
            gw.check(name, f"https://{host}/x")
    assert any(spec.get("run_hosts") for spec in providers.CATALOGUE.values())  # M-F-D17


def test_gateway_check_passes_a_listed_provider_and_refuses_the_rest(project):
    root, ws = project
    declare_l2(ws)
    write_policy(root, yaml.safe_dump({"version": 1, "l2_providers": [
        {"name": "search-openalex", "reason": "test"}]}))
    gw = providers.Gateway(ws, providers.load(root))
    assert gw.check("search-openalex", OPENALEX_URL) is None
    with pytest.raises(providers.ProviderRefused, match="data protection"):
        gw.check("search-arxiv", "https://export.arxiv.org/api/query")


def test_unscoped_gateway_inside_a_dispatch_uses_the_run_level(project, monkeypatch):
    root, ws = project
    declare_l2(ws)
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(ws / ".uv-cache"))
    gw = providers.Gateway.unscoped(providers.load(root))
    with pytest.raises(providers.ProviderRefused, match="data protection"):
        gw.check("search-openalex", OPENALEX_URL)


def test_unscoped_gateway_outside_a_dispatch_is_unchanged(project):
    root, ws = project
    declare_l2(ws)
    gw = providers.Gateway.unscoped(providers.load(root))
    assert gw.check("search-openalex", OPENALEX_URL) is None


def test_unscoped_gateway_inside_a_dispatch_for_an_l0_run_is_unchanged(project, monkeypatch):
    root, ws = project
    monkeypatch.setenv("SCIEFLOW_DISPATCH", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(ws / ".uv-cache"))
    gw = providers.Gateway.unscoped(providers.load(root))
    assert gw.check("search-openalex", OPENALEX_URL) is None


def test_refusal_names_the_run_and_writes_no_job_refused_event(project, capsys):
    # M-B t4 review F6(d): the gateway refusal writes no job.refused event (no job exists).
    root, ws = project
    declare_l2(ws)
    with pytest.raises(providers.ProviderRefused) as exc:
        providers.Gateway(ws, providers.load(root)).check("search-openalex", OPENALEX_URL)
    assert "demo" in str(exc.value)
    assert events.read(ws, types=("job.refused",)) == []
