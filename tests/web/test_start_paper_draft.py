"""Starting a paper-draft run from the browser (plan section 5.4, P6)."""

from scieflow.core import service
from scieflow.core.run import status
from scieflow.web import auth

PAPER_PHASES = ["intake", "outline", "draft", "cross-review", "merge", "verify", "handoff"]


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_start_page_creates_a_native_paper_run(client, project):
    response = post(client, "/start", slug="paper-form", goal="Synthetic paper goal.",
                    workflow="paper-draft", approval="per-campaign",
                    max_iterations="0", max_experiment_runs="0", max_wall_minutes="0")
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/paper-form"
    ws = project.run_dir("paper-form")
    st = status.read_status(ws)
    assert st["workflow"] == "paper-draft" and st["slug"] == "paper-form"
    assert list(st["phases"]) == PAPER_PHASES
    assert not (ws / "budget.yml").exists()
    run = service.run_detail(project, "paper-form")["run"]
    assert run["kind"] == "paper-draft" and run["phase"] == "intake"


def test_start_page_paper_draft_needs_an_explicit_approval(client, project):
    response = post(client, "/start", slug="paper-noapproval", goal="Synthetic paper goal.",
                    workflow="paper-draft", approval="",
                    max_iterations="0", max_experiment_runs="0", max_wall_minutes="0")
    assert response.status_code == 200
    assert "requires explicit --approval" in response.text
    assert not project.run_dir("paper-noapproval").exists()


def test_start_page_paper_draft_budget_is_all_or_none(client, project):
    response = post(client, "/start", slug="paper-partial", goal="Synthetic paper goal.",
                    workflow="paper-draft", approval="per-campaign",
                    max_iterations="0", max_experiment_runs="0", max_wall_minutes="5")
    assert response.status_code == 200
    assert "all three" in response.text
    assert not project.run_dir("paper-partial").exists()
