"""T-12 t4: the Agents page's scoped forms, preview and apply (browser surface).

Synthetic temporary projects only (the `project` and `client` fixtures). The
browser never writes a command template, `enabled` or a promotion: those tests
check that a forged form is refused with its kind and that nothing is written.
"""

import re
from pathlib import Path

import pytest
import yaml

from scieflow.web import auth

NEWS = ("agent: claude\nlookback_days: 30\ninterests:\n"
        "  - name: Snakemake\n    keywords: [workflow]\n")
CODEX = (
    '  codex-a: {cmd: "codexa exec -c effort={reasoning} {prompt}", enabled: true, timeout_min: 1,'
    ' menu: {reasoning: {levels: [low, max]}}}\n'
    '  codex-b: {cmd: "codexb exec -c effort={reasoning} {prompt}", enabled: true, timeout_min: 1,'
    ' menu: {reasoning: {levels: [minimal, xhigh]}}}\n')


def _agents_yml(project) -> Path:
    return project.root / "config" / "agents.yml"


def _add_agents(project, text):
    path = _agents_yml(project)
    path.write_text(path.read_text() + text)


def _news(project):
    (project.root / "config" / "news.yml").write_text(NEWS)


def _csrf(client):
    return client.cookies[auth.CSRF_COOKIE]


def _post(client, route, fields, *, csrf=True):
    data = _body(list(fields))
    if csrf:
        data[auth.CSRF_FIELD] = _csrf(client)
    return client.post(route, data=data, follow_redirects=False)


def _body(pairs):
    """A form body from (name, value) pairs: a repeated name becomes a list value."""
    body: dict = {}
    for name, value in pairs:
        if name in body:
            body[name] = (body[name] if isinstance(body[name], list) else [body[name]]) + [value]
        else:
            body[name] = value
    return body


def _preview(client, fields, *, scope="project", slug=None):
    base = [("scope", scope)] + ([("slug", slug)] if slug else [])
    return _post(client, "/agents/preview", base + list(fields))


def _select_values(page, name):
    block = re.search(rf'<select name="{re.escape(name)}"[^>]*>(.*?)</select>', page, re.S)
    assert block, f"no select named {name}"
    return re.findall(r'<option value="([^"]*)"', block.group(1))


def _forms(page, method):
    return re.findall(rf'<form\b[^>]*method="{method}"[^>]*>.*?</form>', page, re.S | re.I)


def test_each_scope_renders_its_own_form(client, project):
    _news(project)
    project_page = client.get("/agents").text
    assert 'name="agent.stub.timeout_min"' in project_page
    run_page = client.get("/agents", params={"slug": "r1"}).text
    assert 'name="agent.stub.timeout_min"' in run_page
    assert 'name="scope" value="workspace"' in run_page
    news_page = client.get("/agents", params={"scope": "news"}).text
    assert 'name="news.agent"' in news_page
    assert 'name="agent.stub.timeout_min"' not in news_page


@pytest.mark.parametrize("params, message", [
    ({"scope": "news", "slug": "r1"}, "takes no run slug"),
    ({"scope": "workspace"}, "needs a run slug"),
    ({"scope": "elsewhere"}, "unknown scope"),
])
def test_a_bad_scope_is_an_error_with_no_form(client, project, params, message):
    _news(project)
    page = client.get("/agents", params=params).text
    assert message in page
    assert 'name="agent.stub.timeout_min"' not in page
    assert 'name="news.agent"' not in page


def test_claude_effort_offers_exactly_default_and_extended_thinking(client):
    page = client.get("/agents").text
    assert _select_values(page, "agent.stub.reasoning") == ["", "default", "extended-thinking"]


def test_placeholder_agents_offer_their_own_levels(client, project):
    _add_agents(project, CODEX)
    page = client.get("/agents").text
    assert _select_values(page, "agent.codex-a.reasoning") == ["", "low", "max"]
    assert _select_values(page, "agent.codex-b.reasoning") == ["", "minimal", "xhigh"]


def test_an_agent_without_effort_has_no_effort_control(client):
    page = client.get("/agents").text
    assert 'name="agent.sleepy.reasoning"' not in page
    assert 'name="agent.sleepy.model"' in page


def test_timeout_is_a_number_input(client):
    page = client.get("/agents").text
    tag = re.search(r'<input[^>]*name="agent\.stub\.timeout_min"[^>]*>', page)
    assert tag and 'type="number"' in tag.group(0)


def test_every_post_form_carries_csrf_and_no_get_form_does(client):
    page = client.get("/agents").text
    posts = _forms(page, "post")
    assert posts
    assert all('name="csrf_token"' in form for form in posts)
    assert not any("csrf_token" in form for form in _forms(page, "get"))


def test_markup_in_values_and_keys_renders_as_text(client, project):
    text = _agents_yml(project).read_text().replace(
        "family: claude, ", "family: claude, model: '\"><img src=x onerror=1>', ", 1)
    _agents_yml(project).write_text(text)
    page = client.get("/agents").text
    assert "<img src=x onerror=1>" not in page
    assert "&lt;img src=x onerror=1&gt;" in page
    response = _preview(client, [("<img src=x onerror=1>", "1")])
    assert response.status_code == 400
    assert "<img src=x onerror=1>" not in response.text
    assert "&lt;img src=x onerror=1&gt;" in response.text


def test_templates_and_view_code_have_no_dialogs_and_no_safe_filter():
    web = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "web"
    sources = [web / "agent_views.py", *sorted((web / "templates").glob("*.html"))]
    unsafe_filter = re.compile(r"\{[{%][^}%]*\|\s*safe\b")
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for banned in ("alert(", "confirm(", "prompt("):
            assert banned not in text, f"{path.name} must not use a browser dialog"
        assert not unsafe_filter.search(text), f"{path.name} must not disable escaping"


def test_preview_writes_nothing_and_shows_only_the_changed_keys(client, project):
    before = _agents_yml(project).read_text()
    response = _preview(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1"),
        ("agent.stub.model", ""), ("init.agent.stub.model", ""),
    ])
    assert response.status_code == 200
    assert "timeout_min" in response.text
    assert 'name="revision"' in response.text
    assert _agents_yml(project).read_text() == before


def test_preview_never_shows_a_command_template(client):
    response = _preview(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")])
    assert response.status_code == 200
    assert "stub_agent" not in response.text


def test_apply_writes_the_previewed_change_and_reads_back(client, project):
    page = _preview(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")]).text
    changes = re.findall(r'name="change" value="([^"]*)"', page)
    revision = re.search(r'name="revision" value="([0-9a-f]{64})"', page).group(1)
    assert changes == ["set stub.timeout_min=7"]
    response = _post(client, "/agents/apply", [
        ("scope", "project"), ("revision", revision)] + [("change", c) for c in changes])
    assert response.status_code == 303
    assert response.headers["location"].startswith("/agents?scope=project")
    registry = yaml.safe_load(_agents_yml(project).read_text())
    assert registry["agents"]["stub"]["timeout_min"] == 7


def test_a_stale_preview_is_refused_and_writes_nothing(client, project):
    page = _preview(client, [
        ("agent.stub.timeout_min", "7"), ("init.agent.stub.timeout_min", "1")]).text
    changes = re.findall(r'name="change" value="([^"]*)"', page)
    revision = re.search(r'name="revision" value="([0-9a-f]{64})"', page).group(1)
    edited = _agents_yml(project).read_text() + "# edited elsewhere\n"
    _agents_yml(project).write_text(edited)
    response = _post(client, "/agents/apply", [
        ("scope", "project"), ("revision", revision)] + [("change", c) for c in changes])
    assert response.status_code == 409
    assert "changed since it was previewed" in response.text
    assert _agents_yml(project).read_text() == edited


@pytest.mark.parametrize("fields, kind", [
    ([("agent.stub.cmd", "echo hi")], "deliberately CLI-only"),
    ([("agent.stub.enabled", "false")], "deliberately CLI-only"),
    ([("promote", "research.outline")], "CLI-only"),
    ([("agent.stub.timeout", "3")], "unsupported"),
])
def test_forged_controls_are_refused_with_their_kind(client, project, fields, kind):
    before = _agents_yml(project).read_text()
    response = _preview(client, fields)
    assert response.status_code == 400
    assert kind in response.text
    assert _agents_yml(project).read_text() == before


def test_news_refuses_forged_and_role_fields(client, project):
    _news(project)
    before = (project.root / "config" / "news.yml").read_text()
    for fields, kind in [
        ([("news.timeout_min", "5")], "unsupported"),
        ([("role.research.outline.0.agent", "stub")], "no roles"),
    ]:
        response = _preview(client, fields, scope="news")
        assert response.status_code == 400
        assert kind in response.text
    assert (project.root / "config" / "news.yml").read_text() == before


def test_a_preview_without_a_scope_is_refused(client):
    response = _post(client, "/agents/preview", [("agent.stub.timeout_min", "7")])
    assert response.status_code == 400
    assert "choose a scope" in response.text


def test_apply_needs_the_previewed_revision(client, project):
    before = _agents_yml(project).read_text()
    response = _post(client, "/agents/apply", [
        ("scope", "project"), ("change", "set stub.timeout_min=7")])
    assert response.status_code == 400
    assert "preview the change first" in response.text
    assert _agents_yml(project).read_text() == before


def test_a_workspace_change_is_scoped_to_its_run(client, project):
    page = _preview(client, [("agent.stub.timeout_min", "9")], scope="workspace", slug="r1").text
    changes = re.findall(r'name="change" value="([^"]*)"', page)
    revision = re.search(r'name="revision" value="([0-9a-f]{64})"', page).group(1)
    response = _post(client, "/agents/apply", [
        ("scope", "workspace"), ("slug", "r1"), ("revision", revision)]
        + [("change", c) for c in changes])
    assert response.status_code == 303
    run = client.get("/api/v1/agents", params={"scope": "workspace", "slug": "r1"}).json()
    project_view = client.get("/api/v1/agents", params={"scope": "project"}).json()
    assert run["agents"]["stub"]["timeout_min"]["effective"] == 9
    assert project_view["agents"]["stub"]["timeout_min"]["stored"] == 1


def _role_fields(role, rows):
    """Role-row form fields from (agent, model, effort) rows, as the page posts them."""
    out = []
    for index, (agent, model, effort) in enumerate(rows):
        out += [(f"role.{role}.{index}.agent", agent), (f"role.{role}.{index}.model", model),
                (f"role.{role}.{index}.effort", effort)]
    return out


def _preview_page(client, fields, *, scope="project", slug=None):
    response = _preview(client, fields, scope=scope, slug=slug)
    assert response.status_code == 200, response.text
    return response.text


def _apply_previewed(client, page, *, scope="project", slug=None):
    changes = re.findall(r'name="change" value="([^"]*)"', page)
    revision = re.search(r'name="revision" value="([0-9a-f]{64})"', page).group(1)
    base = [("scope", scope)] + ([("slug", slug)] if slug else []) + [("revision", revision)]
    return _post(client, "/agents/apply", base + [("change", c) for c in changes])


def _defaults(project):
    return yaml.safe_load((project.root / "config" / "defaults.yml").read_text())


def test_role_model_only_is_written_as_agent_at_model(client):
    page = _preview_page(client, _role_fields("research.outline", [("stub", "claude-x", "")])
                         + [("init.role.research.outline", "stub")])
    assert re.findall(r'name="change" value="([^"]*)"', page) == ["assign research.outline=stub@claude-x"]


def test_role_effort_only_is_written_as_agent_at_slash_effort(client, project):
    page = _preview_page(client, _role_fields("research.outline", [("stub", "", "extended-thinking")])
                         + [("init.role.research.outline", "stub")])
    assert re.findall(r'name="change" value="([^"]*)"', page) == [
        "assign research.outline=stub@/extended-thinking"]
    assert _apply_previewed(client, page).status_code == 303
    assert _defaults(project)["assignments"]["research.outline"] == {
        "agent": "stub", "reasoning": "extended-thinking"}


def test_role_model_and_effort_are_written_as_agent_at_model_slash_effort(client, project):
    page = _preview_page(client, _role_fields("research.outline", [("stub", "claude-x", "extended-thinking")])
                         + [("init.role.research.outline", "stub")])
    assert _apply_previewed(client, page).status_code == 303
    assert _defaults(project)["assignments"]["research.outline"] == {
        "agent": "stub", "model": "claude-x", "reasoning": "extended-thinking"}


def test_fan_out_role_second_row_is_written_as_a_second_agent(client, project):
    page = _preview_page(client, _role_fields("research.search", [("stub", "", ""), ("stub2", "", "")])
                         + [("init.role.research.search", "stub")])
    assert re.findall(r'name="change" value="([^"]*)"', page) == ["assign research.search=stub,stub2"]
    assert _apply_previewed(client, page).status_code == 303
    assert _defaults(project)["assignments"]["research.search"] == ["stub", "stub2"]


def test_stored_effort_only_role_initial_text_is_canonical(client, project):
    defaults = project.root / "config" / "defaults.yml"
    defaults.write_text(defaults.read_text().replace(
        "  research.outline: stub\n", "  research.outline: {agent: stub, reasoning: extended-thinking}\n"))
    page = client.get("/agents").text
    assert 'name="init.role.research.outline" value="stub@/extended-thinking"' in page
    changed = _preview_page(client, _role_fields("research.outline", [("stub", "claude-x", "extended-thinking")])
                            + [("init.role.research.outline", "stub@/extended-thinking")])
    assert re.findall(r'name="change" value="([^"]*)"', changed) == [
        "assign research.outline=stub@claude-x/extended-thinking"]


@pytest.mark.parametrize("row", [
    ("stub", "x,stub2", ""),
    ("stub", "a@b", ""),
    ("stub", "openai/gpt-5", ""),
    ("stub", "claude x", ""),
    ("stub", "a\nb", ""),
    ("stub", "", "a/b"),
    ("stub", "", "x,y"),
    ("stub,stub2", "", ""),
])
def test_role_row_separators_are_refused_and_nothing_is_written(client, project, row):
    before = (project.root / "config" / "defaults.yml").read_text()
    response = _preview(client, _role_fields("research.outline", [row])
                        + [("init.role.research.outline", "stub")])
    assert response.status_code == 400
    assert "research.outline" in response.text
    assert (project.root / "config" / "defaults.yml").read_text() == before


def test_a_role_row_with_a_model_but_no_agent_is_refused(client, project):
    before = (project.root / "config" / "defaults.yml").read_text()
    response = _preview(client, _role_fields("research.outline", [("", "claude-x", "")])
                        + [("init.role.research.outline", "stub")])
    assert response.status_code == 400
    assert "choose an agent" in response.text
    assert (project.root / "config" / "defaults.yml").read_text() == before


def test_claude_effort_round_trip_through_preview_and_apply(client, project):
    page = _preview_page(client, [("agent.stub.reasoning", "extended-thinking"),
                                  ("init.agent.stub.reasoning", "")])
    assert re.findall(r'name="change" value="([^"]*)"', page) == ["set stub.reasoning=extended-thinking"]
    assert _apply_previewed(client, page).status_code == 303
    view = client.get("/api/v1/agents", params={"scope": "project"}).json()
    assert view["agents"]["stub"]["reasoning"]["stored"] == "extended-thinking"


def test_agent_names_and_run_slugs_with_markup_render_as_text(client, project):
    _add_agents(project, '  "<script>x</script>": {cmd: "true", enabled: true, timeout_min: 1}\n')
    page = client.get("/agents").text
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;x&lt;/script&gt;" in page
    assert "<b>x</b>" not in client.get("/agents", params={"slug": "<b>x</b>"}).text
