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
    response = post(client, "/start", slug="../escape", goal="a goal")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/start?error=")
    assert "slug" in client.get(response.headers["location"]).text.lower()
    assert not (project.root.parent / "escape").exists()


def test_a_duplicate_slug_is_refused_on_the_page(client, project):
    post(client, "/start", slug="dup", goal="the first goal")
    response = post(client, "/start", slug="dup", goal="the second goal")
    assert response.headers["location"].startswith("/start?error=")
    assert (project.run_dir("dup") / "goal.md").read_text() == "the first goal"


def test_an_empty_goal_is_refused_on_the_page(client, project):
    response = post(client, "/start", slug="no-goal", goal="   ")
    assert response.headers["location"].startswith("/start?error=")
    assert not project.run_dir("no-goal").exists()


def test_goal_text_is_escaped_when_the_error_page_echoes_it(client):
    response = post(client, "/start", slug="../bad", goal="<script>alert('x')</script>")
    page = client.get(response.headers["location"]).text
    assert "<script>alert" not in page


def test_the_api_creates_a_run(client, project):
    response = post(client, "/api/v1/runs", slug="via-api", goal="a goal")
    assert response.status_code == 200
    assert response.json()["status"]["run"] == "via-api"


def test_the_dashboard_links_to_start(client):
    assert 'href="/start"' in client.get("/").text


def test_the_wizard_points_at_the_agents_page_for_staffing(client):
    """Staffing has one editor, and it is not this form."""
    assert 'href="/agents"' in client.get("/start").text
