"""W6: the read-only "Review outcome — round N" panel on the drafts page, and the
W8 merge refusal as the drafts page shows it.

Decisions are recorded inside each test through review_service.decide (the
committed fixture has no decisions file, and a hand-written one is ignored by
core). Every change is made to the tmp copy `rv1`, never to the fixture.
"""

from pathlib import Path

from scieflow.core import service as core_service
from scieflow.core.review import revise
from scieflow.core.review import service as review_service
from scieflow.web import auth

DRAFTS = "/runs/rv1/drafts"
MATRIX = ("review", "round-1", "matrix.json")


def ws_file(project, *parts):
    return project.run_dir("rv1").joinpath(*parts)


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def decide(project, decisions):
    review_service.decide(project, "rv1", 1, decisions, actor="human")


def test_no_decisions_means_no_review_outcome_panel(client, review_project):
    assert "Review outcome" not in client.get(DRAFTS).text


def test_the_panel_shows_an_accepted_cluster_and_its_member_comment(client, review_project):
    decide(review_project, {"K2": {"decision": "accept", "note": "agreed with the reviewers"}})
    page = client.get(DRAFTS).text
    assert "Review outcome — round 1" in page
    assert "Diagnostic-accuracy claim overstated" in page
    assert "agreed with the reviewers" in page
    # The comment text the panel must show, taken from core's own accepted view.
    comment = revise.clusters_for(Path(review_project.run_dir("rv1")), 1,
                                  "claude-paper")["accepted"][0]["comments"][0]
    assert comment["reviewer"] in page
    assert (comment.get("problem") or comment.get("comment")) in page.replace("&#39;", "'")


def test_the_panel_links_to_the_review_home_and_the_matrix(client, review_project):
    decide(review_project, {"K2": {"decision": "accept"}})
    page = client.get(DRAFTS).text
    assert 'href="/runs/rv1/review"' in page
    assert 'href="/runs/rv1/review/1/matrix"' in page


def test_the_panel_has_no_form(client, review_project):
    decide(review_project, {"K2": {"decision": "accept"}})
    page = client.get(DRAFTS).text
    panel = page[page.index("Review outcome"):]
    panel = panel[:panel.index("</section>")]
    assert "<form" not in panel


def test_a_script_in_a_decision_note_is_escaped_in_the_panel(client, review_project):
    decide(review_project, {"K2": {"decision": "accept",
                                   "note": "<script>alert(1)</script>"}})
    page = client.get(DRAFTS).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_a_corrupt_pass_under_an_accepted_cluster_is_a_per_draft_notice(client, review_project):
    """A RevisionError from core is one notice for that draft; the page stays up."""
    decide(review_project, {"K2": {"decision": "accept"}})
    ws_file(review_project, "review", "round-1",
            "codex-review-on-claude-paper.blind.json").write_text("{not json")
    response = client.get(DRAFTS)
    assert response.status_code == 200
    assert "Review outcome — round 1" in response.text
    assert "review outcome for claude-paper could not be read" in response.text


def test_a_corrupt_matrix_makes_the_decisions_stale_so_no_panel_is_shown(client, review_project):
    """Core's decisions are current only against the matrix they were made on, so a
    corrupt matrix leaves the round undecided: no panel, and no server error."""
    decide(review_project, {"K2": {"decision": "accept"}})
    ws_file(review_project, *MATRIX).write_text("{not json")
    response = client.get(DRAFTS)
    assert response.status_code == 200
    assert "Review outcome" not in response.text
    assert "Drafts" in response.text


def test_the_panel_key_does_not_collide_with_the_workbench_view(review_project):
    assert "review_outcome" not in core_service.workbench(review_project, "rv1")


def test_a_merge_with_an_open_blocker_shows_the_refusal_and_never_says(client, review_project,
                                                                       monkeypatch):
    """W8 display: the guard runs before `say`, so no agent turn is dispatched."""
    def must_not_run(*args, **kwargs):
        raise AssertionError("say was reached although a blocker is open")

    monkeypatch.setattr(core_service, "say", must_not_run)
    response = post(client, DRAFTS, action="merge")
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "merge refused" in page
