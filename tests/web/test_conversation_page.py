"""Talking to a run's coordinator from the browser."""

from scieflow.core.run import conversation
from scieflow.web import auth


def post(client, path, **form):
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form},
                       follow_redirects=False)


def test_the_api_reports_an_empty_conversation(client):
    body = client.get("/api/v1/runs/r1/conversation").json()
    assert body["agent"] == "" and body["turns"] == []
    assert body["can_converse"] is False


def test_the_run_page_offers_a_chat_panel(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    page = client.get("/runs/r1").text
    assert 'action="/runs/r1/say"' in page
    assert 'name="csrf_token"' in page


def test_the_panel_explains_itself_when_no_agent_is_chosen(client):
    page = client.get("/runs/r1").text
    assert "choose an agent" in page.lower()


def test_saying_something_from_the_page(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    response = post(client, "/runs/r1/say", message="What next?")
    assert response.status_code == 303
    assert conversation.read(project.run_dir("r1"))["turns"][0]["text"] == "What next?"


def test_an_empty_message_is_refused_with_an_explanation(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    response = post(client, "/runs/r1/say", message="   ")
    assert response.headers["location"].startswith("/runs/r1?error=")
    assert conversation.read(project.run_dir("r1"))["turns"] == []


def test_turn_text_is_escaped_on_the_page(client, project):
    """Turn text is whatever an agent wrote — never trusted markup."""
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.add_turn(ws, role="agent", text="<script>alert('x')</script>")
    page = client.get("/runs/r1").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_switching_the_agent_from_the_page(client, project):
    conversation.set_agent(project.run_dir("r1"), "stub")
    assert post(client, "/runs/r1/say", action="agent",
                agent="stub2").status_code == 303
    assert conversation.read(project.run_dir("r1"))["agent"] == "stub2"


def test_the_conversation_route_on_an_unknown_run_is_404(client):
    assert client.get("/api/v1/runs/nope/conversation").status_code == 404


def test_the_hand_over_picker_offers_only_agents_that_could_hold_it(client, project):
    """Finding (minors, 2026-09-25 review): the picker used to list every
    registry entry, including `sleepy` (no session commands at all) and
    `stub_disabled` (`enabled: false`) — offering either as a hand-over
    target would only get refused the moment it was submitted."""
    conversation.set_agent(project.run_dir("r1"), "stub")
    page = client.get("/runs/r1").text
    assert ">stub2<" in page   # stub2 appears nowhere on this page but the picker
    assert "sleepy" not in page
    assert "stub_disabled" not in page


def test_a_lost_session_is_explained_on_the_page(client, project):
    """Finding 6 (2026-09-25 review): a conversational agent whose CLI never
    actually reported a session id must say so on the page, not leave the
    conversation silently starting fresh on every turn."""
    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.add_turn(ws, role="human", text="hi")
    conversation.add_turn(ws, role="agent", text="hello", job_id="J1")
    # can_converse is True (stub has session_cmd/resume_cmd/family) but no
    # session was ever recorded — session_lost must be True.
    page = client.get("/runs/r1").text
    assert "reported no session" in page.lower()


def test_a_turn_missing_a_timestamp_does_not_500_the_page(client, project):
    """`run.html` used to render `{{ turn.ts[11:19] }}` unconditionally,
    which raised `UndefinedError` (and 500'd the whole run page) for any
    turn lacking `ts` — a hand-written or older turn record, say."""
    import yaml

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    doc = yaml.safe_load((ws / "conversation.yml").read_text())
    doc["turns"] = [{"role": "human", "text": "hi", "job_id": ""}]   # no "ts"
    (ws / "conversation.yml").write_text(yaml.safe_dump(doc))

    response = client.get("/runs/r1")
    assert response.status_code == 200
    assert "hi" in response.text
