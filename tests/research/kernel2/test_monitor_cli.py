"""M-C2 t7 (plan 5.7; Part B t7 items 2, 3, 5): `scieflow research monitor`.

The search transport is `responses` answering OpenAlex from a mutable synthetic set of works. The
monitor's diff is the vendored run_monitor; a record's identity is its DOI. No network, no patient
data, no e-mail address.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import responses

from scieflow.core import events
from scieflow.integrations.scite.ledger import default_dir
from scieflow.research import monitor

OPENALEX_RE = re.compile(r"https://api\.openalex\.org/works.*")
TOPIC = "synthetic topic alpha"
P1, P2, P3, P4, P5 = (f"10.5555/paper{i}" for i in range(1, 6))
SET_A = [P1, P2, P3]
SET_B = [P1, P2, P4, P5]          # P3 gone, P4 and P5 new
ANSWERS: dict = {}                 # query text -> list of DOIs OpenAlex currently returns


def work(doi):
    return {
        "id": f"https://openalex.org/W{abs(hash(doi)) % 10**9}",
        "doi": f"https://doi.org/{doi}",
        "display_name": f"Synthetic paper {doi[-1]}",
        "publication_year": 2020,
        "type": "article",
        "authorships": [],
        "cited_by_count": 1,
    }


def openalex(request):
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(request.url).query).get("search", [""])[0]
    dois = ANSWERS.get(query, [])
    return 200, {"Content-Type": "application/json"}, json.dumps({"results": [work(d) for d in dois]})


def ws_dir(cli_project) -> Path:
    return cli_project[1]


def state_path(cli_project, name="alerts") -> Path:
    return ws_dir(cli_project) / "monitors" / f"{name}.json"


def add(cli_run, name="alerts", *extra):
    return cli_run(monitor.main, ["add", "demo", name, "--query", TOPIC, "--source", "openalex",
                                  "--ts", "2026-10-01T00:00:00Z", *extra])


def run_once(cli_run, ts, name="alerts", *extra):
    code, out, err = cli_run(monitor.main, ["run", "demo", name, "--ts", ts, *extra])
    return code, (json.loads(out) if out.strip() else None), err


@pytest.fixture(autouse=True)
def _answers():
    ANSWERS.clear()
    ANSWERS[TOPIC] = list(SET_A)
    yield
    ANSWERS.clear()


@responses.activate
def test_first_run_reports_everything_then_only_the_new_papers(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    code, _out, err = add(cli_run)
    assert code == 0, err
    code, out, _ = run_once(cli_run, "2026-10-02T00:00:00Z")
    assert code == 0 and out["new"] == 3 and out["new_dois"] == SET_A
    code, out, _ = run_once(cli_run, "2026-10-03T00:00:00Z")
    assert code == 0 and out["new"] == 0, "a rerun over the same set reports nothing new"
    # The Gateway caches a search answer for the policy's search TTL (7 days). A rerun that must see
    # the source's new answer therefore starts from an empty search cache. Known limitation: within
    # the TTL a rerun repeats the cached answer (reported in the t7 result, not fixed here).
    ANSWERS[TOPIC] = list(SET_B)
    shutil.rmtree(default_dir() / "cache", ignore_errors=True)
    code, out, _ = run_once(cli_run, "2026-10-04T00:00:00Z")
    assert code == 0 and out["new_dois"] == [P4, P5], "exactly the two new papers, in fan-out order"
    state = json.loads(state_path(cli_project).read_text(encoding="utf-8"))
    assert state["run_count"] == 3, "three runs (the add is not a run)"
    assert state["seen_ids"] == [f"doi:{d}" for d in (P1, P2, P3, P4, P5)], "the seen list only grows"


@responses.activate
def test_replays_with_the_same_ts_are_byte_identical(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    add(cli_run)
    run_once(cli_run, "2026-10-02T00:00:00Z")
    first = {p.name: p.read_bytes() for p in (ws_dir(cli_project) / "monitors").iterdir()}
    for p in (ws_dir(cli_project) / "monitors").iterdir():
        p.unlink()
    add(cli_run)
    run_once(cli_run, "2026-10-02T00:00:00Z")
    second = {p.name: p.read_bytes() for p in (ws_dir(cli_project) / "monitors").iterdir()}
    assert first == second and set(first) == {"alerts.json", "alerts.last.json"}


@responses.activate
def test_a_refused_query_at_add_saves_nothing(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    code, _out, err = cli_run(monitor.main, ["add", "demo", "leaky", "--query",
                                             "jane.doe@example.org", "--source", "openalex"])
    assert code == 3
    assert not state_path(cli_project, "leaky").exists()
    assert len(responses.calls) == 0


@responses.activate
def test_a_miss_at_run_exits_6_and_leaves_the_state_unchanged(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    add(cli_run)
    run_once(cli_run, "2026-10-02T00:00:00Z")
    before = state_path(cli_project).read_bytes()
    code, out, _err = run_once(cli_run, "2026-10-03T00:00:00Z", "alerts", "--network", "replay")
    assert code == 6
    assert state_path(cli_project).read_bytes() == before


@responses.activate
def test_a_failed_source_exits_1_and_leaves_the_state_unchanged(cli_project, cli_run, fast_send):
    add(cli_run)
    responses.add(responses.GET, OPENALEX_RE, status=500, body="down")
    before = state_path(cli_project).read_bytes()
    code, _out, _err = run_once(cli_run, "2026-10-02T00:00:00Z")
    assert code == 1
    assert state_path(cli_project).read_bytes() == before


@pytest.mark.parametrize("name", ["Bad Name", "x" * 49, "UPPER"])
def test_a_bad_monitor_name_exits_1(cli_project, cli_run, name):
    code, _out, _err = cli_run(monitor.main, ["add", "demo", name, "--query", TOPIC,
                                              "--source", "openalex"])
    assert code == 1


@responses.activate
def test_list_and_show_report_counts_and_never_the_query(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    add(cli_run)
    run_once(cli_run, "2026-10-02T00:00:00Z")
    code, out, _ = cli_run(monitor.main, ["list", "demo", "--json"])
    assert code == 0 and TOPIC not in out
    rows = json.loads(out)["monitors"]
    assert rows == [{"name": "alerts", "strategies": 1, "seen": 3, "last_run": "2026-10-02T00:00:00Z",
                     "run_count": 1}]
    code, out, _ = cli_run(monitor.main, ["show", "demo", "alerts", "--json"])
    assert code == 0 and TOPIC not in out
    shown = json.loads(out)
    assert shown["strategies"] == [{"source": "openalex", "label": "openalex"}]
    assert shown["run_count"] == 1


@responses.activate
def test_add_and_run_emit_literature_monitor_events(cli_project, cli_run, fast_send):
    responses.add_callback(responses.GET, OPENALEX_RE, callback=openalex)
    add(cli_run)
    run_once(cli_run, "2026-10-02T00:00:00Z")
    evs = [e for e in events.read(ws_dir(cli_project)) if e["type"] == "literature.monitor"]
    assert [(e["data"]["action"], e["data"]["name"], e["data"]["new"]) for e in evs] == [
        ("added", "alerts", 0), ("ran", "alerts", 3)]
    assert all(TOPIC not in json.dumps(e) for e in evs)
