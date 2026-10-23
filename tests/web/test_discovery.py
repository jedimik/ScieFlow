"""W7: the hypothesis tournament view, GET /runs/{slug}/discovery.

It renders the fixed contract path ws/report/tournament.json (never a path from
the request), validated against the hypothesis-tournament schema. Every test
builds its own report in the tmp copy `rv1`; no committed fixture is assumed.
Novelty verdicts are deferred: the page says so and never reads them.
"""

import json
import os
import sys
import threading

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
    assert "reached through a symlink; not shown" in response.text
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
    assert "reached through a symlink; not shown" in response.text
    assert 'class="top"' not in response.text


def test_a_report_directory_symlinked_inside_the_workspace_is_refused(client, review_project):
    """O7: a symlink at report/ is refused even when its target is inside the run."""
    ws = review_project.run_dir("rv1")
    real = ws / "elsewhere-in-run"
    real.mkdir()
    (real / "tournament.json").write_text(json.dumps(tournament()))
    (ws / "report").symlink_to(real, target_is_directory=True)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "reached through a symlink; not shown" in response.text
    assert 'class="top"' not in response.text


def test_a_report_file_symlinked_inside_the_workspace_is_refused(client, review_project):
    """O7: a symlink at tournament.json is refused even when its target is inside the run."""
    real = review_project.run_dir("rv1") / "real-tournament.json"
    real.write_text(json.dumps(tournament()))
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(real)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "reached through a symlink; not shown" in response.text
    assert 'class="top"' not in response.text


def test_a_report_that_is_a_regular_file_is_a_notice(client, review_project):
    (review_project.run_dir("rv1") / "report").write_text("not a directory")
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "report/ is not a directory; tournament.json not shown" in response.text


def test_the_loader_leaks_no_descriptors_on_any_path(review_project):
    ws = review_project.run_dir("rv1")
    write_report(review_project, tournament())
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(50):
        assert review_views.load_tournament(ws)[1] == ""
    path = report_path(review_project)
    path.rename(ws / "moved.json")
    path.symlink_to(ws / "moved.json")
    for _ in range(50):
        assert "reached through a symlink" in review_views.load_tournament(ws)[1]
    assert len(os.listdir("/proc/self/fd")) == before


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


# --- robustness: a corrupt or hostile report never 500s the page (R4 C1, C2, C4) --------


def _release_blocked_reader(path, thread):
    """A reader blocked opening a FIFO returns only when a writer opens it: do that so a
    failing test ends instead of hanging the run."""
    if thread.is_alive():
        os.close(os.open(path, os.O_WRONLY))


def _run_with_timeout(target, seconds=5):
    box = {}

    def run():
        box["value"] = target()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    return thread, box


def test_a_deeply_nested_tournament_is_a_notice_not_a_server_error(client, review_project):
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[" * 400_000)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "nested too deeply" in response.text


def test_a_deeply_nested_gate_file_is_a_notice_on_the_discovery_page(client, review_project):
    gates_dir = review_project.run_dir("rv1") / "gates"
    gates_dir.mkdir(exist_ok=True)
    (gates_dir / "zz-deep.json").write_text("[" * 100_000)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "gates could not be read" in response.text


def test_a_fifo_tournament_returns_promptly_with_a_notice(review_project):
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    thread, box = _run_with_timeout(
        lambda: review_views.load_tournament(review_project.run_dir("rv1")))
    returned_promptly = not thread.is_alive()
    _release_blocked_reader(path, thread)
    assert returned_promptly, "load_tournament blocked on a FIFO"
    data, notice = box["value"]
    assert data is None
    assert "not a regular file" in notice


def test_a_fifo_tournament_does_not_hang_the_page(client, review_project):
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    thread, box = _run_with_timeout(lambda: client.get(PAGE))
    returned_promptly = not thread.is_alive()
    _release_blocked_reader(path, thread)
    assert returned_promptly, "the discovery page blocked on a FIFO"
    assert box["value"].status_code == 200
    assert "not a regular file" in box["value"].text


def test_a_directory_at_the_tournament_path_is_a_notice(client, review_project):
    path = report_path(review_project)
    path.mkdir(parents=True)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "not a regular file" in response.text


def test_the_read_is_bounded_even_when_the_size_reports_small(review_project, monkeypatch):
    """A file that lies about its size (fstat) is still read at most REPORT_LIMIT + 1 bytes.

    Note: the old stat-based check also refuses this file, so this guards the new bounded
    read rather than reproducing a RED state."""
    path = report_path(review_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b" " * (review_views.REPORT_LIMIT + 1))
    real_fstat = os.fstat

    def small(fd):
        st = real_fstat(fd)
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                               st.st_gid, 0, int(st.st_atime), int(st.st_mtime),
                               int(st.st_ctime)))

    monkeypatch.setattr(review_views.os, "fstat", small)
    data, notice = review_views.load_tournament(review_project.run_dir("rv1"))
    assert data is None
    assert "larger than 2 MiB" in notice


def test_a_gate_file_that_is_not_an_object_is_skipped_on_the_discovery_page(client,
                                                                             review_project):
    ws = review_project.run_dir("rv1")
    gate = core_gates.open_gate(review_project, ws, "hypothesis-selection", "Pick them")
    (ws / "gates" / "zz-bad.json").write_text("[1, 2]")
    response = client.get(PAGE)
    assert response.status_code == 200
    assert f"/runs/rv1#gate-{gate['id']}" in response.text


def test_a_huge_elo_is_shown_truncated_not_a_server_error(client, review_project):
    doc = tournament()
    doc["hypotheses"][0]["elo"] = int("1" * 4000)
    write_report(review_project, doc)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "1" * 32 in response.text
    assert "1" * 33 not in response.text

