"""M-C2 t6 (plan 5.6; Part B t6 items 1-6): `scieflow research snowball`.

Every transport is `responses` answering from the synthetic graph (graph_support.py) or from a
small inline answer. A request the test did not register fails loudly (responses raises). No
network, no e-mail address, no patient data.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
import responses
import yaml

from scieflow._vendor.researcher_core.model import is_valid_doi, normalize_doi
from scieflow.research.search import snowball
from tests.research.kernel2.graph_support import (
    OPENALEX_RE,
    OPENCITATIONS_RE,
    S2_RE,
    opencitations_answer,
    openalex_answer,
)

REPO = Path(__file__).resolve().parents[3]
SEED1 = "10.5555/seed1"
A, B, C, D = "10.5555/a", "10.5555/b", "10.5555/c", "10.5555/d"


def serve(*, openalex: bool = True, opencitations: bool = True) -> None:
    if openalex:
        responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex_answer)
    if opencitations:
        responses.add_callback(responses.GET, OPENCITATIONS_RE, callback=opencitations_answer)


def run(cli_run, argv):
    code, out, _err = cli_run(snowball.main, argv)
    return code, out


def payload_of(out: str) -> dict:
    return json.loads(out)


def urls(pattern) -> list:
    return [c.request.url for c in responses.calls if re.search(pattern, c.request.url)]


def write_integrations(root, providers_cfg) -> None:
    (root / "config" / "integrations.yml").write_text(
        yaml.safe_dump({"providers": providers_cfg}), encoding="utf-8")


def statuses(payload) -> list:
    return [(s["source"], s["status"]) for s in payload["sources"]]


# --- 1. depth, cycles and the expand-once rule (GAP acceptance) -----------------------------


@pytest.fixture
def expansions(monkeypatch):
    """Every (source, node DOI, direction) the traversal expands, counted before the Gateway
    cache: a duplicate frontier entry is a cache hit that sends no request, so the request count
    alone cannot show it."""
    calls: list = []
    for name, fetch in list(snowball.FETCH.items()):
        def wrapped(gw, doi, direction, limit, _fetch=fetch, _name=name):
            calls.append((_name, doi, direction))
            return _fetch(gw, doi, direction, limit)
        monkeypatch.setitem(snowball.FETCH, name, wrapped)
    return calls


@responses.activate
def test_forward_depth_two_terminates_and_expands_each_node_once(cli_project, cli_run, fast_send,
                                                                 expansions):
    serve()
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "2", "--direction", "forward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    payload = payload_of(out)
    assert sorted(n["doi"] for n in payload["nodes"]) == [A, B, C]
    assert SEED1 not in [n["doi"] for n in payload["nodes"]], "seeds are never discoveries"
    # Expanded nodes: the seed (level 1), then A and B (level 2). C is found, not expanded.
    for source in ("openalex", "opencitations"):
        expanded = Counter(doi for name, doi, _direction in expansions if name == source)
        assert expanded == Counter({SEED1: 1, A: 1, B: 1}), source
    edges = {(e["citing"], e["cited"], e["depth"]) for e in payload["edges"]}
    assert edges == {(f"doi:{A}", f"doi:{SEED1}", 1), (f"doi:{B}", f"doi:{SEED1}", 1),
                     (f"doi:{B}", f"doi:{A}", 2), (f"doi:{C}", f"doi:{A}", 2),
                     (f"doi:{A}", f"doi:{B}", 2)}


@responses.activate
def test_backward_cycle_terminates_and_the_seed_is_not_a_node(cli_project, cli_run, fast_send,
                                                              expansions):
    serve()
    code, out = run(cli_run, ["--seed", A, "--depth", "2", "--direction", "backward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    payload = payload_of(out)
    assert sorted(n["doi"] for n in payload["nodes"]) == sorted([SEED1, B, C, D])
    assert A not in [n["doi"] for n in payload["nodes"]]
    # A cites B and B cites A: the mutual pair ends by construction; each node is expanded once.
    expanded = Counter(doi for name, doi, _direction in expansions if name == "opencitations")
    assert expanded == Counter({A: 1, SEED1: 1, B: 1})


@responses.activate
def test_depth_outside_one_or_two_is_refused_before_any_request(cli_project, cli_run, fast_send):
    serve()
    for depth in ("3", "0"):
        code, _out = run(cli_run, ["--seed", SEED1, "--depth", depth])
        assert code == 1
    assert len(responses.calls) == 0


# --- 2. edge union (both directions) --------------------------------------------------------


@responses.activate
def test_the_same_edge_from_two_sources_is_one_edge_with_both_sources(cli_project, cli_run,
                                                                      fast_send):
    serve()
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    edges = [e for e in payload_of(out)["edges"] if e["citing"] == f"doi:{A}"]
    assert len(edges) == 1
    assert edges[0]["cited"] == f"doi:{SEED1}"
    assert edges[0]["sources"] == ["openalex", "opencitations"]


@responses.activate
def test_backward_edges_are_stored_citing_to_cited(cli_project, cli_run, fast_send):
    serve()
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "backward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    edges = payload_of(out)["edges"]
    assert edges == [{"citing": f"doi:{SEED1}", "cited": f"doi:{D}",
                      "sources": ["openalex", "opencitations"], "depth": 1}]


# --- 3. node dedupe ---------------------------------------------------------------------------


@responses.activate
def test_an_opencitations_doi_only_record_merges_with_the_openalex_record(cli_project, cli_run,
                                                                          fast_send):
    serve()
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    nodes = [n for n in payload_of(out)["nodes"] if n["doi"] == A]
    assert len(nodes) == 1, "one node per DOI"
    assert nodes[0]["title"] == "Synthetic paper A"
    assert nodes[0]["year"] == 2020
    assert set(nodes[0]["signals"]["kernel"]["sources"]) == {"openalex", "opencitations"}


# --- 4. isolation: a failing, switched-off or refused source leaves the others ---------------


@responses.activate
def test_a_rate_limited_source_costs_only_itself(cli_project, cli_run, fast_send):
    serve(openalex=False)
    responses.add(responses.GET, OPENALEX_RE, status=429)
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward",
                              "--sources", "openalex,opencitations"])
    assert code == 0
    payload = payload_of(out)
    assert statuses(payload) == [("openalex", "failed"), ("opencitations", "ok")]
    assert sorted(n["doi"] for n in payload["nodes"]) == [A, B]


@responses.activate
def test_semantic_scholar_is_off_by_default_and_sends_nothing(cli_project, cli_run, fast_send):
    serve()
    responses.add_callback(responses.GET, S2_RE, callback=lambda r: (500, {}, "{}"))
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward"])
    assert code == 0
    payload = payload_of(out)
    assert ("semantic-scholar", "off") in statuses(payload)
    assert urls(r"api\.semanticscholar") == []


@responses.activate
def test_an_l2_run_refuses_every_listed_source_before_any_request(cli_project, cli_run, fast_send):
    root, ws = cli_project
    serve()
    (ws / "data.yml").write_text(yaml.safe_dump({
        "version": 1, "default_level": "L2", "levels": [], "pending_releases": [],
        "released": [], "imports": []}, sort_keys=False), encoding="utf-8")
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--workspace", "demo",
                              "--sources", "openalex,opencitations"])
    assert code == 3
    assert statuses(payload_of(out)) == [("openalex", "refused"), ("opencitations", "refused")]
    assert len(responses.calls) == 0


# --- 5. bounds ----------------------------------------------------------------------------------


@responses.activate
def test_more_than_ten_seeds_or_a_bad_doi_is_refused_before_any_request(cli_project, cli_run,
                                                                        fast_send):
    serve()
    eleven = [f"--seed=10.5555/s{i}" for i in range(11)]
    assert run(cli_run, eleven)[0] == 1
    assert run(cli_run, ["--seed", "not a doi"])[0] == 1
    assert len(responses.calls) == 0


@responses.activate
def test_ten_seeds_are_accepted(cli_project, cli_run, fast_send):
    serve()
    argv = []
    for i in range(10):
        argv += ["--seed", f"10.5555/unknown{i}"]
    code, _out = run(cli_run, argv + ["--depth", "1", "--sources", "openalex,opencitations"])
    assert code == 0


@responses.activate
def test_limit_caps_the_neighbours_per_node_and_source(cli_project, cli_run, fast_send):
    serve()
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward",
                              "--sources", "openalex,opencitations", "--limit", "1"])
    assert code == 0
    payload = payload_of(out)
    assert [n["doi"] for n in payload["nodes"]] == [A]
    assert payload["counts"]["retrieved"] == 2
    assert run(cli_run, ["--seed", SEED1, "--limit", "0"])[0] == 1
    assert run(cli_run, ["--seed", SEED1, "--limit", "101"])[0] == 1


# --- 6. record, replay, miss ------------------------------------------------------------------


@responses.activate
def test_record_then_replay_is_byte_identical_and_a_miss_exits_six(cli_project, cli_run,
                                                                   fast_send):
    serve()
    argv = ["--seed", SEED1, "--depth", "1", "--direction", "forward",
            "--sources", "openalex,opencitations", "--workspace", "demo"]
    code, recorded = run(cli_run, argv + ["--network", "record"])
    assert code == 0
    responses.calls.reset()
    code, replayed = run(cli_run, argv + ["--network", "replay"])
    assert code == 0
    assert replayed == recorded
    assert len(responses.calls) == 0
    code, _out = run(cli_run, ["--seed", D, "--depth", "1", "--workspace", "demo",
                               "--sources", "openalex,opencitations", "--network", "replay"])
    assert code == 6
    assert len(responses.calls) == 0


# --- 7. Semantic Scholar edges (switched on for this test) -----------------------------------


def s2_answer(request):
    if request.url.split("?")[0].endswith("/graph/v1/paper/DOI:" + SEED1 + "/citations"):
        body = {"data": [
            {"citingPaper": {"paperId": "p-a", "externalIds": {"DOI": "10.5555/A"},
                             "title": "Synthetic paper A", "year": 2020, "citationCount": 3}},
            {"citingPaper": None},
        ]}
        return 200, {"Content-Type": "application/json"}, json.dumps(body)
    return 404, {"Content-Type": "application/json"}, "{}"


@responses.activate
def test_semantic_scholar_citations_are_read_and_an_empty_side_is_skipped(cli_project, cli_run,
                                                                          fast_send):
    root, _ws = cli_project
    write_integrations(root, {"search-semantic-scholar": {"enabled": True}})
    responses.add_callback(responses.GET, S2_RE, callback=s2_answer)
    code, out = run(cli_run, ["--seed", SEED1, "--depth", "1", "--direction", "forward",
                              "--sources", "semantic-scholar"])
    assert code == 0
    payload = payload_of(out)
    assert [n["doi"] for n in payload["nodes"]] == [A]
    assert payload["nodes"][0]["title"] == "Synthetic paper A"
    assert payload["counts"]["retrieved"] == 1
    assert payload["edges"] == [{"citing": f"doi:{A}", "cited": f"doi:{SEED1}",
                                 "sources": ["semantic-scholar"], "depth": 1}]


# --- 8. researcher's recorded answers (converted fixtures; the request is not compared) -----------

RECORDED = (
    REPO / "tests" / "fixtures" / "kernel2" / "graph" / "recorded"
)


def recorded_body(name: str):
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))["body"]


def serve_body(pattern: str, body) -> None:
    responses.add(responses.GET, re.compile(pattern), json=body, status=200)


@pytest.mark.parametrize("name, pattern, seed, direction, field", [
    ("opencitations-references-peerj4375.json",
     r"https://api\.opencitations\.net/index/v1/references/10\.7717/peerj\.4375.*",
     "10.7717/peerj.4375", "backward", "cited"),
    ("opencitations-citations-s11192.json",
     r"https://api\.opencitations\.net/index/v1/citations/10\.1007/s11192-021-04026-6.*",
     "10.1007/s11192-021-04026-6", "forward", "citing"),
])
@responses.activate
def test_recorded_opencitations_answers_become_doi_only_nodes(cli_project, cli_run, fast_send,
                                                              name, pattern, seed, direction,
                                                              field):
    body = recorded_body(name)
    serve_body(pattern, body)
    code, out = run(cli_run, ["--seed", seed, "--depth", "1", "--direction", direction,
                              "--sources", "opencitations", "--limit", "100"])
    assert code == 0
    payload = payload_of(out)
    expected = {normalize_doi(d) for edge in body for d in str(edge[field]).split()}
    expected = {d for d in expected if is_valid_doi(d)} - {normalize_doi(seed)}
    assert {n["doi"] for n in payload["nodes"]} == expected
    assert all(n["title"] is None for n in payload["nodes"]), "OpenCitations carries no metadata"
    assert payload["sources"] == [{"source": "opencitations", "status": "ok",
                                   "count": payload["counts"]["retrieved"]}]


@responses.activate
def test_recorded_semantic_scholar_citations_parse_to_nodes(cli_project, cli_run, fast_send):
    root, _ws = cli_project
    body = recorded_body("semantic-scholar-citations-peerj4375.json")
    serve_body(r"https://api\.semanticscholar\.org/graph/v1/paper/DOI:10\.7717/peerj\.4375/"
               r"citations.*", body)
    write_integrations(root, {"search-semantic-scholar": {"enabled": True}})
    code, out = run(cli_run, ["--seed", "10.7717/peerj.4375", "--depth", "1",
                              "--sources", "semantic-scholar", "--limit", "100"])
    assert code == 0
    payload = payload_of(out)
    expected = {normalize_doi(i["citingPaper"]["externalIds"]["DOI"]) for i in body["data"]
                if i.get("citingPaper")}
    assert len(expected) == 5
    assert {n["doi"] for n in payload["nodes"]} == expected
    assert payload["counts"]["retrieved"] == 5


@responses.activate
def test_the_limit_bounds_the_neighbours_even_when_a_source_answers_more(cli_project, cli_run,
                                                                         fast_send):
    root, _ws = cli_project
    body = recorded_body("semantic-scholar-citations-peerj4375.json")
    serve_body(r"https://api\.semanticscholar\.org/graph/v1/paper/DOI:10\.7717/peerj\.4375/"
               r"citations.*", body)
    write_integrations(root, {"search-semantic-scholar": {"enabled": True}})
    code, out = run(cli_run, ["--seed", "10.7717/peerj.4375", "--depth", "1",
                              "--sources", "semantic-scholar", "--limit", "2"])
    assert code == 0
    assert payload_of(out)["counts"]["retrieved"] == 2
