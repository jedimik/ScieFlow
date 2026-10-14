"""W3 round view, W4 matrix and decisions, W5 blocker banner, review home and nav.

`review_project` is the fixture round (slug rv1, round 1). Its cluster K1 is a
blocker (the retracted reference novak2020). Every test that changes a review
file changes the copy under the test's own tmp project, never the committed
fixture.
"""

import json

import pytest
import yaml

from scieflow.core.review import service as review_service
from scieflow.web import auth, review_views

MATRIX = ("review", "round-1", "matrix.json")
PASS_EVIDENCE = ("review", "round-1", "codex-review-on-claude-paper.evidence.json")
BUNDLE = ("review", "round-1", "evidence", "bundle.json")
MATRIX_PAGE = "/runs/rv1/review/1/matrix"
DECIDE = "/runs/rv1/review/1/decisions"
ROUND_PAGE = "/runs/rv1/review/1?author=claude-paper"


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def ws_file(project, *parts):
    return project.run_dir("rv1").joinpath(*parts)


def edit_json(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def set_review_decisions(project, mode):
    path = project.run_dir("rv1") / "config.yml"
    doc = yaml.safe_load(path.read_text()) or {}
    doc.setdefault("review", {})["review_decisions"] = mode
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


# --- review home ------------------------------------------------------------------


def test_the_review_home_lists_the_round_and_its_blocker(client, review_project):
    page = client.get("/runs/rv1/review").text
    assert "/runs/rv1/review/1" in page and "/runs/rv1/review/1/matrix" in page
    assert "Retracted source novak2020" in page
    assert "/agents?slug=rv1" in page


def test_the_review_home_of_a_run_without_review_is_an_empty_state(client):
    response = client.get("/runs/r1/review")
    assert response.status_code == 200
    assert "No review round yet" in response.text


def test_the_review_home_of_an_unknown_run_is_404(client):
    assert client.get("/runs/no-such-run/review").status_code == 404


def test_the_review_home_degrades_without_the_research_extra(client, review_project,
                                                              monkeypatch):
    monkeypatch.setattr(review_views, "_review", lambda: None)
    response = client.get("/runs/rv1/review")
    assert response.status_code == 200
    assert "review needs the research extra" in response.text


def test_a_script_in_a_blocker_reference_is_escaped_on_every_review_page(client, review_project):
    edit_json(ws_file(review_project, *BUNDLE),
              lambda d: next(f for f in d["flags"] if f["id"] == "F1").update(
                  detail="<script>alert(1)</script>"))
    for path in ("/runs/rv1", "/runs/rv1/review", MATRIX_PAGE):
        page = client.get(path).text
        assert "<script>alert(1)" not in page, path


# --- W3: the round view ------------------------------------------------------------


def test_the_round_view_shows_blind_and_evidence_passes_side_by_side(client, review_project):
    page = client.get(ROUND_PAGE).text
    assert "blind" in page and "evidence" in page
    assert "strengthened" in page
    assert "In contrast to Smith et al." in page
    assert "https://doi.org/10.5555/rbd.2019.001" in page


def test_the_round_view_defaults_to_the_first_author(client, review_project):
    page = client.get("/runs/rv1/review/1").text
    assert 'value="claude-paper" selected' in page


def test_the_round_view_of_an_unknown_author_is_404(client, review_project):
    assert client.get("/runs/rv1/review/1?author=nobody").status_code == 404


def test_the_round_view_of_an_unknown_round_is_404(client, review_project):
    assert client.get("/runs/rv1/review/9").status_code == 404


def test_a_non_integer_round_is_refused(client, review_project):
    assert client.get("/runs/rv1/review/one").status_code in (404, 422)


def test_the_round_view_shows_the_scite_status_of_the_bundle(client, review_project):
    assert "scite status: ok" in client.get(ROUND_PAGE).text


def test_a_missing_review_file_shows_a_placeholder(client, review_project):
    ws_file(review_project, *PASS_EVIDENCE).unlink()
    assert "not (yet) available" in client.get(ROUND_PAGE).text


def test_a_corrupt_review_file_is_a_notice_not_a_server_error(client, review_project):
    ws_file(review_project, *PASS_EVIDENCE).write_text("{not json")
    response = client.get(ROUND_PAGE)
    assert response.status_code == 200
    assert "Traceback" not in response.text


def test_a_script_in_a_reviewer_comment_is_escaped(client, review_project):
    edit_json(ws_file(review_project, *PASS_EVIDENCE),
              lambda d: d["minor"][0].update(comment="<script>alert(1)</script>"))
    page = client.get(ROUND_PAGE).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)" in page


def test_a_doi_that_is_not_a_doi_renders_as_plain_text(client, review_project):
    edit_json(ws_file(review_project, *PASS_EVIDENCE),
              lambda d: d["major"][0]["evidence"][0].update(doi="javascript:alert(1)"))
    page = client.get(ROUND_PAGE).text
    assert 'href="javascript:' not in page
    assert "javascript:alert(1)" in page


# --- W4: the matrix and decisions ---------------------------------------------------


def test_blocker_clusters_are_pinned_above_the_others(client, review_project):
    edit_json(ws_file(review_project, *MATRIX), lambda d: d["clusters"].reverse())
    page = client.get(MATRIX_PAGE).text
    blocker = page.index("Retracted source novak2020")
    assert blocker < page.index("Define RBD at first use")
    assert blocker < page.index("Diagnostic-accuracy claim overstated")


def test_an_open_blocker_shows_its_notice_and_an_overrule_form(client, review_project):
    page = client.get(MATRIX_PAGE).text
    assert "10.5555/rbd.2020.014" in page
    assert "Crossref update-to: retraction (2023)" in page
    assert 'name="overrule_reason"' in page
    assert 'value="K1"' in page


def test_non_blocker_clusters_offer_accept_reject_and_defer(client, review_project):
    page = client.get(MATRIX_PAGE).text
    for word in ('value="accept"', 'value="reject"', 'value="defer"'):
        assert word in page
    assert 'value="K3"' in page


def test_a_decision_is_recorded_as_a_human_and_redirects_to_the_matrix(client, review_project):
    response = post(client, DECIDE, cluster="K2", decision="accept")
    assert response.status_code == 303
    assert response.headers["location"].startswith(MATRIX_PAGE + "?ok=")
    doc = review_service.decisions(review_project, "rv1", 1)
    assert doc["decisions"]["K2"]["decision"] == "accept"
    assert doc["decisions"]["K2"]["actor"] == "human"


def test_a_decision_note_is_shown_escaped(client, review_project):
    post(client, DECIDE, cluster="K2", decision="accept", note="<script>alert(1)</script>")
    page = client.get(MATRIX_PAGE).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_script_in_a_cluster_title_is_escaped_on_the_matrix(client, review_project):
    edit_json(ws_file(review_project, *MATRIX),
              lambda d: next(c for c in d["clusters"] if c["cluster"] == "K2").update(
                  title="<script>alert(1)</script>"))
    page = client.get(MATRIX_PAGE).text
    assert "<script>alert(1)</script>" not in page


def test_a_blank_overrule_reason_is_refused_in_place_and_nothing_is_written(client,
                                                                             review_project):
    response = post(client, DECIDE, cluster="K1", decision="reject")
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "overrule" in response.headers["location"]
    assert review_service.decisions(review_project, "rv1", 1) is None


def test_an_overrule_with_a_reason_is_recorded_and_lifts_the_banner(client, review_project):
    response = post(client, DECIDE, cluster="K1", decision="reject",
                    overrule_reason="checked by hand: not retracted")
    assert response.status_code == 303
    assert "ok=" in response.headers["location"]
    doc = review_service.decisions(review_project, "rv1", 1)
    assert doc["decisions"]["K1"]["overrule_reason"] == "checked by hand: not retracted"
    assert review_service.blockers(review_project, "rv1") == []
    page = client.get(MATRIX_PAGE).text
    assert "overruled" in page
    assert 'name="overrule_reason"' not in page


def test_an_unknown_decision_word_is_refused(client, review_project):
    response = post(client, DECIDE, cluster="K3", decision="maybe")
    assert "error=" in response.headers["location"]
    assert review_service.decisions(review_project, "rv1", 1) is None


def test_an_unknown_cluster_is_refused(client, review_project):
    response = post(client, DECIDE, cluster="K99", decision="accept")
    assert "error=" in response.headers["location"]


def test_a_decision_on_an_unknown_round_is_404(client, review_project):
    assert post(client, "/runs/rv1/review/9/decisions", cluster="K2",
                decision="accept").status_code == 404


def test_coordinator_mode_shows_a_recorded_verdict_without_its_form(client, review_project):
    set_review_decisions(review_project, "coordinator")
    review_service.decide(review_project, "rv1", 1, {"K2": {"decision": "accept"}},
                          actor="human")
    page = client.get(MATRIX_PAGE).text
    assert 'value="K2"' not in page
    assert 'value="K3"' in page
    assert 'value="K1"' in page
    assert "accept" in page and "human" in page


def test_human_mode_keeps_every_form_even_when_decided(client, review_project):
    review_service.decide(review_project, "rv1", 1, {"K2": {"decision": "accept"}},
                          actor="human")
    assert 'value="K2"' in client.get(MATRIX_PAGE).text


def test_a_blocker_flag_no_cluster_covers_gets_an_informational_notice(client, review_project):
    edit_json(ws_file(review_project, *MATRIX),
              lambda d: d.__setitem__("clusters",
                                      [c for c in d["clusters"] if c["cluster"] != "K1"]))
    page = client.get(MATRIX_PAGE).text
    assert "fix the citation and build a new round" in page
    assert 'name="overrule_reason"' not in page


def test_an_open_review_gate_is_listed_until_its_cluster_is_decided(client, review_project):
    from scieflow.core import gates as core_gates

    ws = review_project.run_dir("rv1")
    question = "[review round 1 · cluster K3 · matrix aaaaaaaaaaaa] Decide K3"
    gate = core_gates.open_gate(review_project, ws, "review-decision", question)
    page = client.get(MATRIX_PAGE).text
    assert "Decide K3" in page
    assert f"/runs/rv1#gate-{gate['id']}" in page
    review_service.decide(review_project, "rv1", 1, {"K3": {"decision": "defer"}},
                          actor="human")
    assert "Decide K3" not in client.get(MATRIX_PAGE).text


def test_a_corrupt_matrix_leaves_the_matrix_page_up_with_a_notice(client, review_project):
    ws_file(review_project, *MATRIX).write_text("{not json")
    response = client.get(MATRIX_PAGE)
    assert response.status_code == 200
    assert "matrix.json" in response.text


def test_the_matrix_page_degrades_without_the_research_extra(client, review_project,
                                                             monkeypatch):
    monkeypatch.setattr(review_views, "_review", lambda: None)
    response = client.get(MATRIX_PAGE)
    assert response.status_code == 200
    assert "review needs the research extra" in response.text


def test_a_corrupt_gate_file_leaves_the_matrix_up_with_a_notice(client, review_project):
    """O4: one unreadable gate file must not turn the matrix page into a 500."""
    gates = ws_file(review_project, "gates")
    gates.mkdir(exist_ok=True)
    (gates / "zz-corrupt.json").write_text("{not json")
    response = client.get(MATRIX_PAGE)
    assert response.status_code == 200
    assert "open gates could not be read" in response.text
    assert "Diagnostic-accuracy claim overstated" in response.text


def test_a_gate_file_that_is_not_an_object_is_a_notice_too(client, review_project):
    gates = ws_file(review_project, "gates")
    gates.mkdir(exist_ok=True)
    (gates / "zz-list.json").write_text("[]")
    response = client.get(MATRIX_PAGE)
    assert response.status_code == 200
    assert "open gates could not be read" in response.text
    assert "Diagnostic-accuracy claim overstated" in response.text


# --- W5: the blocker banner, the run page and the nav links ---------------------------


def test_the_run_page_shows_the_blocker_banner(client, review_project):
    page = client.get("/runs/rv1").text
    assert "open blocker" in page
    assert "/runs/rv1/review/1/matrix" in page


def test_the_run_page_has_no_banner_or_review_link_without_review(client):
    page = client.get("/runs/r1").text
    assert "open blocker" not in page
    assert 'href="/runs/r1/review"' not in page


def test_a_corrupt_matrix_leaves_the_run_page_up_with_a_notice(client, review_project):
    ws_file(review_project, *MATRIX).write_text("{not json")
    response = client.get("/runs/rv1")
    assert response.status_code == 200
    assert "matrix.json" in response.text


def test_the_run_page_degrades_without_the_research_extra(client, review_project, monkeypatch):
    monkeypatch.setattr(review_views, "_review", lambda: None)
    response = client.get("/runs/rv1")
    assert response.status_code == 200
    assert "review needs the research extra" in response.text


def test_the_run_page_links_the_review_home(client, review_project):
    assert 'href="/runs/rv1/review"' in client.get("/runs/rv1").text


def test_the_discovery_link_appears_only_when_the_discovery_folder_exists(client,
                                                                          review_project):
    assert 'href="/runs/rv1/discovery"' not in client.get("/runs/rv1").text
    ws_file(review_project, "discovery").mkdir()
    assert 'href="/runs/rv1/discovery"' in client.get("/runs/rv1").text


# --- R4 robustness: a corrupt or hostile review file is a notice, never a 500 ----------


def test_a_deeply_nested_matrix_is_a_notice_on_every_review_page(client, review_project):
    ws_file(review_project, *MATRIX).write_text("[" * 100_000)
    assert client.get("/runs/rv1").status_code == 200
    review = client.get("/runs/rv1/review")
    assert review.status_code == 200
    assert "nested too deeply" in review.text
    matrix = client.get(MATRIX_PAGE)
    assert matrix.status_code == 200
    assert "nested too deeply" in matrix.text


def test_a_deeply_nested_review_pass_is_a_notice_on_the_round_page(client, review_project):
    ws_file(review_project, *PASS_EVIDENCE).write_text("[" * 100_000)
    response = client.get(ROUND_PAGE)
    assert response.status_code == 200
    assert "nested too deeply" in response.text


MAJOR_ONE = [{"id": "M1", "problem": "p"}]
WRONG_TYPED_PASSES = [
    ({"major": 5}, True),
    ({"major": "text"}, True),
    ({"major": [{"id": "M1", "problem": "p", "evidence": 7}]}, True),
    ({"major": [{"id": "M1", "problem": "p", "evidence": ["str"]}]}, True),
    ({"major": [{"id": "M1", "problem": "p", "evidence": [{"doi": 5}]}]}, False),
    ({"minor": [{"id": "m1", "comment": ["list", "for", "text"]}]}, True),
    ({"major": MAJOR_ONE, "minor": [1, "stray"]}, True),
]


@pytest.mark.parametrize("change, notice", WRONG_TYPED_PASSES)
def test_wrong_typed_pass_fields_are_normalised_not_a_server_error(client, review_project,
                                                                     change, notice):
    """R4 C3: `major: 5` or `evidence: 7` from a reviewer file was a 500 on the round page."""
    path = ws_file(review_project, *PASS_EVIDENCE)
    doc = json.loads(path.read_text())
    doc.pop("major", None)
    doc.pop("minor", None)
    doc.update(change)
    path.write_text(json.dumps(doc))
    response = client.get(ROUND_PAGE)
    assert response.status_code == 200
    assert "Traceback" not in response.text
    assert ("review file malformed" in response.text) is notice


# --- R4 C5: a blocker-state read failure is not a 'overruled' status -------------------


def test_a_blocker_read_failure_says_state_unknown_not_overruled(client, review_project,
                                                                 monkeypatch):
    from scieflow.core.review import service as review_service

    def broken(project, slug):
        raise ValueError("blockers could not be read in this test")

    monkeypatch.setattr(review_service, "blockers", broken)
    response = client.get(MATRIX_PAGE)
    assert response.status_code == 200
    assert "blocker state unknown" in response.text
    assert "overruled" not in response.text
    assert 'name="overrule_reason"' not in response.text

