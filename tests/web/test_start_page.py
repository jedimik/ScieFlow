"""Starting a run from the browser."""

from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_start_page_offers_every_workflow(client):
    page = client.get("/start").text
    assert "research-loop" in page and "lit-review" in page
    assert 'action="/start"' in page and 'name="csrf_token"' in page


def test_starting_a_run_creates_it_and_goes_to_its_page(client, project):
    response = post(client, "/start", slug="new-run", goal="Find a catalyst.",
                    workflow="research-loop")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/new-run"
    assert (project.run_dir("new-run") / "goal.md").read_text() == "Find a catalyst."


def test_the_budget_and_approval_from_the_form_are_applied(client, project):
    from scieflow.core.run import budget, status

    post(client, "/start", slug="new-run", goal="a goal", workflow="research-loop",
         approval="autonomous", max_iterations="7")
    ws = project.run_dir("new-run")
    assert status.read_status(ws)["approval"] == "autonomous"
    assert budget.read_budget(ws)["budgets"]["max_iterations"] == 7


def test_a_bad_slug_is_refused_on_the_page_not_with_a_traceback(client, project):
    """The refusal re-renders the form in place (200, not a redirect) so a
    long goal never has to round-trip through a URL — see `start_run`."""
    response = post(client, "/start", slug="../escape", goal="a goal")
    assert response.status_code == 200
    assert "slug" in response.text.lower()
    assert not (project.root.parent / "escape").exists()


def test_a_duplicate_slug_is_refused_on_the_page(client, project):
    post(client, "/start", slug="dup", goal="the first goal")
    response = post(client, "/start", slug="dup", goal="the second goal")
    assert response.status_code == 200
    assert "already exists" in response.text
    assert (project.run_dir("dup") / "goal.md").read_text() == "the first goal"


def test_an_empty_goal_is_refused_on_the_page(client, project):
    response = post(client, "/start", slug="no-goal", goal="   ")
    assert response.status_code == 200
    assert not project.run_dir("no-goal").exists()


def test_goal_text_is_escaped_when_the_error_page_echoes_it(client):
    """The refusal re-renders the goal into the textarea (see
    `test_a_refused_submission_keeps_what_was_typed`), so this is now a real
    check of Jinja's autoescaping, not a check of a page that never echoes
    anything back."""
    response = post(client, "/start", slug="../bad", goal="<script>alert('x')</script>")
    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;alert" in response.text


def test_a_refused_submission_keeps_what_was_typed(client, project):
    """Losing a carefully-written goal to a slug typo is the kind of thing
    nobody forgives twice — every field the user set should still be there
    to fix and resubmit, not just the goal. `agent` is the field this task
    added, and a refusal (here, the bad slug — not the agent) must not drop
    it any more than it drops workflow or approval."""
    response = post(client, "/start", slug="../escape",
                    goal="Three careful paragraphs of context nobody wants to retype.",
                    workflow="lit-review", agent="stub",
                    approval="autonomous", max_iterations="9")
    page = response.text
    assert "Three careful paragraphs of context nobody wants to retype." in page
    assert '<option value="lit-review" selected>' in page
    assert "<option selected>stub</option>" in page
    assert "<option selected>autonomous</option>" in page
    assert 'value="9"' in page


def test_the_api_creates_a_run(client, project):
    response = post(client, "/api/v1/runs", slug="via-api", goal="a goal")
    assert response.status_code == 200
    assert response.json()["status"]["run"] == "via-api"


def test_the_dashboard_links_to_start(client):
    assert 'href="/start"' in client.get("/").text


def test_the_wizard_points_at_the_agents_page_for_staffing(client):
    """Staffing has one editor, and it is not this form."""
    assert 'href="/agents"' in client.get("/start").text


def test_choosing_an_agent_launches_the_coordinator(client, project):
    from scieflow.core.run import conversation

    post(client, "/start", slug="launched", goal="a goal",
         workflow="research-loop", agent="stub")
    doc = conversation.read(project.run_dir("launched"))
    assert doc["agent"] == "stub"
    assert doc["turns"], "no first turn was taken"


def test_the_wizard_says_so_when_no_agent_can_converse(client, project, monkeypatch):
    from scieflow.core import service as service_mod

    monkeypatch.setattr(service_mod, "conversational_agents", lambda project: [])
    page = client.get("/start").text
    assert "no agent" in page.lower()
    assert 'name="agent"' not in page or "disabled" in page
