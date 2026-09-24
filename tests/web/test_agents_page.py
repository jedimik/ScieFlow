"""The Agents page.

Roles come from `agent_config.ROLES` — there is no "coordinator" role; the
fixture project assigns `research.outline`. Assignments are written to
`config/defaults.yml`, not `config/agents.yml`, which holds the registry.
"""

import yaml

from scieflow.web import auth

DEFAULTS = ("config", "defaults.yml")


def test_the_page_lists_every_role_and_its_agent(client):
    page = client.get("/agents").text
    assert "research.outline" in page
    assert "stub" in page
    assert 'name="csrf_token"' in page


def test_previewing_a_change_shows_a_diff_and_writes_nothing(client, project):
    before = (project.root.joinpath(*DEFAULTS)).read_text()
    page = client.get("/agents", params={"assign": "research.outline=stub2"}).text
    assert "--- a/config/defaults.yml" in page
    assert "+++ b/config/defaults.yml" in page
    assert (project.root.joinpath(*DEFAULTS)).read_text() == before


def test_applying_writes_the_change(client, project):
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "assign": "research.outline=stub2"})
    assert response.status_code == 303
    written = yaml.safe_load((project.root.joinpath(*DEFAULTS)).read_text())
    assert written["assignments"]["research.outline"] == "stub2"


def test_an_unknown_role_is_reported_not_raised(client):
    page = client.get("/agents", params={"assign": "wizard=stub"}).text
    assert "unknown role" in page


def test_an_unknown_agent_is_reported_not_written(client, project):
    before = (project.root.joinpath(*DEFAULTS)).read_text()
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "assign": "research.outline=nonesuch"})
    assert response.headers["location"].startswith("/agents?error=")
    assert (project.root.joinpath(*DEFAULTS)).read_text() == before


def test_a_workspace_change_is_scoped_to_that_run(client, project):
    token = client.cookies[auth.CSRF_COOKIE]
    client.post("/agents", follow_redirects=False, data={
        auth.CSRF_FIELD: token, "slug": "r1", "assign": "research.outline=stub2"})
    defaults = yaml.safe_load((project.root.joinpath(*DEFAULTS)).read_text())
    assert defaults["assignments"]["research.outline"] == "stub"
    scoped = client.get("/api/v1/agents", params={"slug": "r1"}).json()
    assert scoped["assignments"]["research.outline"]["value"] == "stub2"


def test_applying_nothing_says_so(client):
    token = client.cookies[auth.CSRF_COOKIE]
    response = client.post("/agents", follow_redirects=False,
                           data={auth.CSRF_FIELD: token})
    assert response.headers["location"].startswith("/agents?error=")


def test_the_agents_page_needs_csrf(client):
    assert client.post(
        "/agents", data={"assign": "research.outline=stub2"}).status_code == 403
