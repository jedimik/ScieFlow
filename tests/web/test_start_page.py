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
    long goal never has to round-trip through a URL — see `start_run`.

    Asserts the actual refusal text, not merely that the word "slug" is
    somewhere on the page — the form always contains `name="slug"`, so that
    alone would pass even if the refusal message were never rendered."""
    response = post(client, "/start", slug="../escape", goal="a goal")
    assert response.status_code == 200
    assert "not a run slug" in response.text
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


def test_a_trailing_newline_in_the_slug_is_refused_not_carried_into_a_directory(
        client, project):
    """A crafted POST bypasses the HTML `pattern` attribute entirely — this
    is the end-to-end reproduction: `slug=abc%0A` used to return 303 and
    create `workspace/'abc\\n'/`."""
    response = post(client, "/start", slug="abc\n", goal="a goal")
    assert response.status_code == 200
    assert "not a run slug" in response.text
    made = [p.name for p in (project.root / "workspace").iterdir()]
    assert not any("\n" in name for name in made), \
        f"a control character reached a directory name: {made}"
    assert "abc" not in made and "abc\n" not in made


def test_a_trailing_space_in_the_slug_names_a_trimmed_directory(client, project):
    response = post(client, "/start", slug="a b ", goal="a goal")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/a%20b"
    made = [p.name for p in (project.root / "workspace").iterdir()]
    assert "a b" in made, f"expected a trimmed directory name among {made}"
    assert "a b " not in made


def test_a_workspace_prefixed_name_redirects_to_a_page_that_exists(client, project):
    """`slug="workspace/pfx"` is a name `Project.run_dir` legitimately
    normalises to `pfx` — `quote()` treats `/` as safe, so redirecting with
    the raw submitted slug used to send the browser to `/runs/workspace/pfx`,
    which 404s, while the run was actually made at `/runs/pfx`."""
    response = post(client, "/start", slug="workspace/pfx", goal="a goal")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/pfx"
    page = client.get(response.headers["location"])
    assert page.status_code == 200


def test_a_post_creation_failure_redirects_to_the_run_with_the_error_visible(
        client, project, monkeypatch):
    """The run was made and the coordinator was recorded — a sandbox
    refusal, an exhausted budget or an oversized prompt failing `say` after
    that must not look like nothing happened. This must land on the run's
    own page (not a re-rendered, empty-looking form), with the error
    visible, and the run must actually be there to resubmit against."""
    from scieflow.core import service as service_mod

    def explode(*a, **kw):
        raise service_mod.ServiceError("the sandbox refused this run")

    monkeypatch.setattr(service_mod, "say", explode)
    response = post(client, "/start", slug="failed-turn", goal="a goal",
                    workflow="research-loop", agent="stub")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/failed-turn")
    assert project.run_dir("failed-turn").exists()
    page = client.get(response.headers["location"])
    assert "the sandbox refused this run" in page.text


def test_the_chosen_workflow_survives_creation(client, project):
    import yaml

    post(client, "/start", slug="with-workflow", goal="a goal", workflow="gap-discovery")
    cfg = yaml.safe_load((project.run_dir("with-workflow") / "config.yml").read_text())
    assert cfg["workflow"] == "gap-discovery"


def test_a_fresh_visit_defaults_the_workflow_select_to_research_loop(client):
    """No `<option>` used to be marked `selected` at all, so a browser that
    submitted the page untouched silently sent the first-listed workflow
    (`lit-review`) — not `research-loop`, the one workflow this form's own
    `init_workspace` call actually produces the shape of."""
    page = client.get("/start").text
    assert '<option value="research-loop" selected>' in page
