"""M-C1 t2 (Part A 5.6): the Gateway's network modes, through the search commands.

Every transport is faked (http.send is replaced); no call reaches a real service. Synthetic data.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
from types import SimpleNamespace

import pytest
import yaml

from scieflow.core import events
from scieflow.integrations.scite.ledger import default_dir
from scieflow.research.lib import providers
from scieflow.research.search import arxiv, crossref, europepmc, openalex, registry

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF_URL = "https://api.crossref.org/works"
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

# name -> (module, API url, provider, op)
SOURCES = {
    "openalex": (openalex, OPENALEX_URL, "search-openalex", "search"),
    "arxiv": (arxiv, ARXIV_URL, "search-arxiv", "query"),
    "europepmc": (europepmc, EPMC_URL, "search-europepmc", "search"),
    "crossref": (crossref, CROSSREF_URL, "search-crossref", "search"),
}


def _json_answer(body):
    return SimpleNamespace(status_code=200, text=json.dumps(body), json=lambda: body)


def _text_answer(text):
    def no_json():
        raise ValueError("not JSON")
    return SimpleNamespace(status_code=200, text=text, json=no_json)


ANSWERS = {
    OPENALEX_URL: lambda: _json_answer({"results": [
        {"display_name": "Demo", "publication_year": 2023, "doi": "https://doi.org/10.5555/d.1"}]}),
    ARXIV_URL: lambda: _text_answer(ATOM),
    EPMC_URL: lambda: _json_answer({"resultList": {"result": [
        {"title": "Demo", "pubYear": "2023", "doi": "10.5555/d.2"}]}}),
    CROSSREF_URL: lambda: _json_answer({"message": {"items": [
        {"DOI": "10.5555/d.3", "title": ["Demo"], "published": {"date-parts": [[2023]]}}]}}),
}


class Wire:
    """The fake transport: records every call; an unexpected URL fails the test loudly."""

    def __init__(self):
        self.calls = []
        self.answers = dict(ANSWERS)

    def __call__(self, method, url, **kwargs):
        self.calls.append(url)
        if url not in self.answers:
            raise AssertionError(f"transport called for {url}")
        return self.answers[url]()


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(providers.http, "send", w)
    return w


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


def run(mod, argv, capsys):
    """(exit code, stdout, stderr) of one search command."""
    try:
        mod.main(argv)
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    out, err = capsys.readouterr()
    return code, out, err


def snapshot_files(ws):
    return sorted((ws / "snapshots" / "literature").rglob("*.json"))


def snapshot_events(ws):
    return events.read(ws, types=("literature.snapshot",))


def declare_l2(ws):
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")


def write_network(ws, mode):
    (ws / "network.yml").write_text(yaml.safe_dump({
        "version": 1, "mode": mode, "set_by": "human", "set_at": "2026-10-27T15:00:00Z",
        "reason": "test"}), encoding="utf-8")


# --- record ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(SOURCES))
def test_record_sends_stores_a_snapshot_and_emits_one_event(project, wire, capsys, name):
    _, ws = project
    mod, url, provider, op = SOURCES[name]
    code, _, err = run(mod, [QUERY, "--workspace", "demo", "--network", "record"], capsys)
    assert code == 0, err
    assert wire.calls == [url]
    files = snapshot_files(ws)
    assert len(files) == 1 and files[0].parent.name == provider
    [evt] = snapshot_events(ws)
    assert evt["data"]["action"] == "recorded"
    assert evt["data"]["provider"] == provider and evt["data"]["op"] == op
    assert re.fullmatch(r"[0-9a-f]{64}", evt["data"]["response_hash"])
    assert QUERY not in json.dumps(evt)


def test_record_reads_no_cache_and_goes_to_the_transport_again(project, wire, capsys):
    _, ws = project
    assert run(openalex, [QUERY, "--workspace", "demo", "--network", "record"], capsys)[0] == 0
    assert run(openalex, [QUERY, "--workspace", "demo", "--network", "record"], capsys)[0] == 0
    assert wire.calls == [OPENALEX_URL, OPENALEX_URL]


# --- replay ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(SOURCES))
def test_replay_round_trip_is_byte_identical_and_makes_no_call(project, wire, capsys, name):
    _, ws = project
    mod, url, _, _ = SOURCES[name]
    code, recorded, err = run(mod, [QUERY, "--workspace", "demo", "--network", "record"], capsys)
    assert code == 0, err
    wire.answers.clear()          # from here on any transport call fails the test
    wire.calls.clear()
    first = run(mod, [QUERY, "--workspace", "demo", "--network", "replay"], capsys)
    second = run(mod, [QUERY, "--workspace", "demo", "--network", "replay"], capsys)
    assert first[0] == second[0] == 0
    assert first[1] == second[1] == recorded
    assert wire.calls == []


@pytest.mark.parametrize("name", list(SOURCES))
def test_replay_miss_is_a_typed_error_and_never_goes_live(project, wire, capsys, name):
    _, ws = project
    mod, _, provider, op = SOURCES[name]
    code, out, err = run(mod, [QUERY, "--workspace", "demo", "--network", "replay"], capsys)
    assert code == 6
    assert re.fullmatch(rf"offline-miss: {provider} {op} \(key [0-9a-f]{{12}}\): "
                        r"run demo is replay; nothing was sent", err.strip())
    assert QUERY not in err and out == ""
    assert wire.calls == []
    assert snapshot_files(ws) == [] and snapshot_events(ws) == []


def test_a_replay_file_misses_the_same_way_without_a_flag(project, wire, capsys):
    _, ws = project
    write_network(ws, "replay")
    code, _, err = run(openalex, [QUERY, "--workspace", "demo"], capsys)
    assert code == 6 and err.startswith("offline-miss: search-openalex search (key ")
    assert wire.calls == []


def test_a_command_flag_cannot_loosen_a_replaying_run(project, wire, capsys):
    _, ws = project
    write_network(ws, "replay")
    code, _, err = run(openalex, [QUERY, "--workspace", "demo", "--network", "live"], capsys)
    assert code == 3 and "cannot loosen" in err
    assert wire.calls == []


def test_a_no_egress_flag_outside_a_dispatch_needs_a_workspace(project, wire, capsys):
    code, _, err = run(openalex, [QUERY, "--network", "record"], capsys)
    assert code == 1 and "needs --workspace" in err
    assert wire.calls == []


def test_a_gateway_on_a_replaying_run_uses_the_runs_own_mode(project, wire):
    root, ws = project
    gw = providers.Gateway(ws, providers.load(root), network="record")
    checked = {"search": registry.check_query(QUERY)}
    recorded = gw.get_json("search-openalex", "search", OPENALEX_URL,
                           checked=checked, fixed={"per-page": 25})
    write_network(ws, "replay")
    wire.answers.clear()
    wire.calls.clear()
    replayed = providers.Gateway(ws, providers.load(root)).get_json(
        "search-openalex", "search", OPENALEX_URL, checked=checked, fixed={"per-page": 25})
    assert replayed == recorded
    assert wire.calls == []


# --- offline -----------------------------------------------------------------------------------

def test_offline_answers_from_a_snapshot_then_from_the_cache_then_misses(project, wire, capsys):
    _, ws = project
    code, recorded, _ = run(openalex, [QUERY, "--workspace", "demo", "--network", "record"], capsys)
    assert code == 0
    wire.answers.clear()
    wire.calls.clear()
    code, out, err = run(openalex, [QUERY, "--workspace", "demo", "--network", "offline"], capsys)
    assert code == 0 and out == recorded and wire.calls == []     # snapshot hit
    shutil.rmtree(ws / "snapshots")
    code, out, err = run(openalex, [QUERY, "--workspace", "demo", "--network", "offline"], capsys)
    assert code == 0 and out == recorded and wire.calls == []     # cache hit (record cached it)
    shutil.rmtree(default_dir() / "cache")
    code, out, err = run(openalex, [QUERY, "--workspace", "demo", "--network", "offline"], capsys)
    assert code == 6 and wire.calls == [] and out == ""           # nothing answers: a miss


# --- L2 runs -----------------------------------------------------------------------------------

def test_an_l2_run_answers_a_snapshot_without_transport_and_refuses_a_miss(project, wire, capsys):
    _, ws = project
    assert run(openalex, [QUERY, "--workspace", "demo", "--network", "record"], capsys)[0] == 0
    declare_l2(ws)
    wire.answers.clear()
    wire.calls.clear()
    code, _, err = run(openalex, [QUERY, "--workspace", "demo"], capsys)
    assert code == 0 and wire.calls == []
    shutil.rmtree(ws / "snapshots")
    code, _, err = run(openalex, [QUERY, "--workspace", "demo"], capsys)
    assert code == 3 and "data protection" in err and "l2_providers" in err
    assert wire.calls == []


# --- the Gateway's surface ---------------------------------------------------------------------

def test_check_keeps_its_signature():
    assert list(inspect.signature(providers.Gateway.check).parameters) == ["self", "provider", "url"]
