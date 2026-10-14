"""W7: the hypothesis tournament view, GET /runs/{slug}/discovery.

It renders the fixed contract path ws/report/tournament.json (never a path from
the request), validated against the hypothesis-tournament schema. Every test
builds its own report in the tmp copy `rv1`; no committed fixture is assumed.
Novelty verdicts are deferred: the page says so and never reads them.
"""

import json
import sys

from scieflow.core import gates as core_gates
from scieflow.web import review_views

PAGE = "/runs/rv1/discovery"
TWO_MIB = 2 * 1024 * 1024


def report_path(project):
    return project.run_dir("rv1") / "report" / "tournament.json"


def tournament(**changes):
    doc = {
        "version": 1, "seed": 7, "k": 32, "initial": 1200, "max_comparisons": 10,
        "top_k": 2, "no_verdict": 1, "top": ["H1", "H2"],
        "hypotheses": [
            {"id": "H1", "author": "claude-paper", "family": "claude",
             "debate_status": "consensus", "elo": 1216.04, "rank": 1,
             "wins": 2, "losses": 0, "ties": 0, "comparisons": 2},
            {"id": "H2", "author": "codex-paper", "family": "codex",
             "debate_status": "disputed", "elo": 1199.96, "rank": 2,
             "wins": 0, "losses": 1, "ties": 1, "comparisons": 2},
        ],
        "matches": [
            {"match": 1, "a": "H1", "b": "H2", "judge": "agy", "judge_family": "gemini",
             "judge_tier": "other-family", "result": "a", "winner": "H1"},
            {"match": 2, "a": "H2", "b": "H1", "judge": "agy", "judge_family": "gemini",
             "judge_tier": "other-family", "result": "no-verdict"},
        ],
    }
    doc.update(changes)
    return doc


def write_report(project, data):
    path = report_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


# --- empty states and routing ----------------------------------------------------------


def test_a_run_without_a_report_is_an_empty_state(client, review_project):
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "no tournament report yet" in response.text


def test_the_discovery_link_target_resolves_once_the_discovery_folder_exists(client,
                                                                             review_project):
    review_project.run_dir("rv1").joinpath("discovery").mkdir()
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "no tournament report yet" in response.text


def test_an_unknown_run_is_404(client):
    assert client.get("/runs/no-such-run/discovery").status_code == 404


# --- a valid report ----------------------------------------------------------------------


def test_a_valid_report_renders_the_elo_table_and_the_matches(client, review_project):
    write_report(review_project, tournament())
    page = client.get(PAGE).text
    assert "1216.0" in page and "1200.0" in page
    assert "consensus" in page and "disputed" in page
    assert "other-family" in page
    assert "seed 7" in page


def test_the_top_hypotheses_are_marked(client, review_project):
    write_report(review_project, tournament(top=["H1"], top_k=1))
    page = client.get(PAGE).text
    assert page.count('class="top"') == 1


def test_novelty_is_stated_as_not_yet_available(client, review_project):
    write_report(review_project, tournament())
    assert "novelty verdicts: not yet available in the contract" in client.get(PAGE).text


# --- notices: the report is refused, the page stays up ----------------------------------


def test_a_schema_invalid_report_is_a_notice(client, review_project):
    doc = tournament()
    del doc["version"]
    write_report(review_project, doc)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "does not match the contract" in response.text
    assert "required property" in response.text
    assert 'class="top"' not in response.text


def test_invalid_json_is_a_notice(client, review_project):
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "not valid JSON" in response.text


def test_an_oversized_report_is_refused_before_it_is_read(client, review_project):
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pad": "' + "x" * (TWO_MIB + 10) + '"}')
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "larger than 2 MiB" in response.text


def test_a_report_symlinked_outside_the_workspace_is_refused(client, review_project, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(tournament()))
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "resolves outside the run workspace" in response.text
    assert 'class="top"' not in response.text


def test_a_report_directory_symlinked_outside_the_workspace_is_refused(client, review_project,
                                                                      tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "tournament.json").write_text(json.dumps(tournament()))
    report = review_project.run_dir("rv1") / "report"
    report.symlink_to(outside, target_is_directory=True)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "resolves outside the run workspace" in response.text
    assert 'class="top"' not in response.text


def test_a_report_missing_the_research_extra_says_so(client, review_project, monkeypatch):
    write_report(review_project, tournament())
    monkeypatch.setattr(review_views, "_decisions_module", lambda: None)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "review needs the research extra" in response.text


def test_the_loader_returns_none_for_an_absent_report(review_project):
    data, notice = review_views.load_tournament(review_project.run_dir("rv1"))
    assert data is None and notice == ""


def test_the_loader_returns_the_document_for_a_valid_report(review_project):
    write_report(review_project, tournament())
    data, notice = review_views.load_tournament(review_project.run_dir("rv1"))
    assert notice == "" and data["seed"] == 7


# --- the hypothesis-selection gate state -----------------------------------------------


def test_no_hypothesis_selection_gate_is_stated(client, review_project):
    assert "no hypothesis-selection gate yet" in client.get(PAGE).text


def test_an_open_hypothesis_selection_gate_links_to_the_run_page(client, review_project):
    ws = review_project.run_dir("rv1")
    gate = core_gates.open_gate(review_project, ws, "hypothesis-selection",
                                "Which hypotheses carry forward?")
    page = client.get(PAGE).text
    assert "Which hypotheses carry forward?" in page
    assert f"/runs/rv1#gate-{gate['id']}" in page


def test_an_answered_hypothesis_selection_gate_shows_its_answer(client, review_project):
    ws = review_project.run_dir("rv1")
    gate = core_gates.open_gate(review_project, ws, "hypothesis-selection", "Pick them")
    core_gates.answer(review_project, ws, gate["id"], "H1 and H2", actor="human")
    page = client.get(PAGE).text
    assert "H1 and H2" in page
    assert f"#gate-{gate['id']}" not in page


def test_a_corrupt_gate_file_is_a_notice_on_the_discovery_page(client, review_project):
    gates_dir = review_project.run_dir("rv1") / "gates"
    gates_dir.mkdir(exist_ok=True)
    (gates_dir / "zz-corrupt.json").write_text("{not json")
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "gates could not be read" in response.text


# --- untrusted text is escaped everywhere on the page -----------------------------------


def test_script_text_in_the_report_and_gate_is_escaped(client, review_project):
    payload = "<script>alert(1)</script>"
    doc = tournament(top=[payload, "H2"])
    doc["hypotheses"][0]["id"] = payload
    doc["hypotheses"][0]["author"] = payload
    doc["matches"][0]["judge"] = payload
    write_report(review_project, doc)
    ws = review_project.run_dir("rv1")
    gate = core_gates.open_gate(review_project, ws, "hypothesis-selection", "Pick")
    core_gates.answer(review_project, ws, gate["id"], payload, actor="human")
    page = client.get(PAGE).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert payload not in page
