"""The charter panel: read it, edit it, revert it, from the browser."""

import pytest

from scieflow.core.run import charter
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_api_reports_an_empty_charter_for_a_run_without_one(client):
    body = client.get("/api/v1/runs/r1/charter").json()
    assert body == {"current": 0, "text": "", "versions": []}


def test_setting_the_charter_over_the_api(client, project):
    assert post(client, "/api/v1/runs/r1/charter",
                text="Find a better catalyst.").status_code == 200
    assert charter.current_text(project.run_dir("r1")) == "Find a better catalyst."


def test_the_run_page_shows_the_charter_and_an_edit_form(client, project):
    charter.set_text(project.run_dir("r1"), "Find a better catalyst.")
    page = client.get("/runs/r1").text
    assert "Find a better catalyst." in page
    assert 'action="/runs/r1/charter"' in page


def test_the_run_page_of_a_run_without_a_charter_still_renders(client):
    """Every run that predates this feature has no charter.yml."""
    page = client.get("/runs/r1")
    assert page.status_code == 200
    assert "No charter" in page.text


def test_setting_the_charter_from_the_page_redirects_back(client, project):
    response = post(client, "/runs/r1/charter", text="A goal.", note="first")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/r1"
    assert charter.current_text(project.run_dir("r1")) == "A goal."


def test_reverting_from_the_page(client, project):
    ws = project.run_dir("r1")
    charter.set_text(ws, "Original.")
    charter.set_text(ws, "Drifted.")
    assert post(client, "/runs/r1/charter", action="revert",
                version="1").status_code == 303
    assert charter.current_text(ws) == "Original."


def test_reverting_to_a_missing_version_explains_itself(client, project):
    charter.set_text(project.run_dir("r1"), "Only one.")
    response = post(client, "/runs/r1/charter", action="revert", version="99")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert "version" in client.get(response.headers["location"]).text
    assert charter.current_text(project.run_dir("r1")) == "Only one."


def test_empty_charter_text_is_refused(client, project):
    response = post(client, "/runs/r1/charter", text="   ")
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert charter.current_text(project.run_dir("r1")) == ""


def test_charter_text_is_escaped_on_the_page(client, project):
    """Charter text can come from an agent's proposal, so it is not trusted
    markup."""
    charter.set_text(project.run_dir("r1"), "<script>alert('x')</script>")
    page = client.get("/runs/r1").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_charter_route_on_an_unknown_run_is_404(client):
    assert client.get("/api/v1/runs/nope/charter").status_code == 404


def test_adopting_a_proposal_from_the_gate_form(client, project):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Adopted from the browser.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])

    assert post(client, f"/runs/r1/gates/{gate['id']}",
                answer="adopt").status_code == 303
    assert charter.current_text(ws) == "Adopted from the browser."


def test_a_failed_adoption_from_the_page_leaves_the_gate_open(client, project):
    """A deleted proposal file must not silently record `adopt` — the gate
    stays open so a human can fix the file and try again."""
    from scieflow.core import gates

    ws = project.run_dir("r1")
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Here now, gone later.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[proposal])
    proposal.unlink()

    response = post(client, f"/runs/r1/gates/{gate['id']}", answer="adopt")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert charter.current_text(ws) == ""
    assert gates.get(ws, gate["id"])["state"] == "open"


def test_a_proposal_naming_a_path_outside_the_run_is_refused_from_the_page(client, project, tmp_path):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    outside = tmp_path / "outside.md"
    outside.write_text("Not this run's business.")
    gate = gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                           options=["adopt", "decline"], files=[outside])

    response = post(client, f"/runs/r1/gates/{gate['id']}", answer="adopt")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert charter.current_text(ws) == ""
    assert gates.get(ws, gate["id"])["state"] == "open"


def test_the_gate_form_shows_the_proposed_charter_text(client, project):
    """The human answering `adopt` should see what they are adopting, not
    just the agent-authored question — that is what makes the approval
    informed."""
    from scieflow.core import gates

    ws = project.run_dir("r1")
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("<script>alert('x')</script> the real plan")
    gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                    options=["adopt", "decline"], files=[proposal])

    page = client.get("/runs/r1").text
    assert "the real plan" in page
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_the_gate_form_degrades_when_the_proposal_cannot_be_previewed(client, project):
    """A gate whose proposal has already gone missing must still render the
    page — the preview is best-effort, not load-bearing."""
    from scieflow.core import gates

    ws = project.run_dir("r1")
    proposal = ws / "proposals" / "charter.md"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("Gone before anyone looks.")
    gates.open_gate(project, ws, "charter-adoption", "Adopt?",
                    options=["adopt", "decline"], files=[proposal])
    proposal.unlink()

    response = client.get("/runs/r1")
    assert response.status_code == 200
