"""The workbench page: three panels over one run's drafts."""

import pytest

from scieflow.core.run import curation
from scieflow.web import auth


def post(client, path, **form):
    """POST the way a browser form does: the CSRF token as a field — the
    same helper every other page's test module uses (test_run_page_actions,
    test_charter_page, test_conversation_page, test_start_page). The
    `client` fixture only exchanges the loopback token for cookies; it never
    stamps a CSRF field onto a POST for you, so a bare `client.post(...)`
    against a mutating route always gets refused with 403."""
    token = client.cookies[auth.CSRF_COOKIE]
    return client.post(path, data={auth.CSRF_FIELD: token, **form})


@pytest.fixture
def drafted(project):
    ws = project.run_dir("r1")
    for agent in ("claude", "codex"):
        d = ws / "manuscript" / "drafts" / agent
        d.mkdir(parents=True)
        (d / "results.tex").write_text(f"Yield was 95\\% per {agent}.\n")
    return ws


def test_the_page_shows_every_agents_draft(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "claude" in page.text and "codex" in page.text
    assert "per claude" in page.text and "per codex" in page.text


def test_a_run_with_no_drafts_says_so(client, project):
    """REVIEW FOCUS 2: anyone opening the workbench before `paper-draft`
    Phase 3 has run has an empty `drafts/` directory. An empty shell with
    three blank panels tells them nothing about why."""
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "no drafts" in page.text.lower()
    assert "paper-draft" in page.text, "say which workflow produces them"


def test_an_unknown_run_is_404(client):
    assert client.get("/runs/nope/drafts").status_code == 404


def test_keeping_a_passage_stores_it_with_its_provenance(client, drafted):
    response = post(client, "/runs/r1/drafts", **{
        "action": "keep", "text": "Yield was 95\\%.",
        "agent": "claude", "section": "results"})
    assert response.status_code in (200, 303)
    block = curation.read(drafted)["blocks"][0]
    assert block["agent"] == "claude" and block["section"] == "results"


def test_your_own_text_is_added_from_the_same_form(client, drafted):
    post(client, "/runs/r1/drafts", action="mine", text="My own sentence.")
    blocks = curation.read(drafted)["blocks"]
    assert blocks[0]["kind"] == "mine" and blocks[0]["text"] == "My own sentence."


def test_a_block_is_edited_moved_and_removed(client, drafted):
    post(client, "/runs/r1/drafts", action="mine", text="first")
    post(client, "/runs/r1/drafts", action="mine", text="second")
    first, second = [b["id"] for b in curation.read(drafted)["blocks"]]

    post(client, "/runs/r1/drafts", action="edit", block=first, text="edited")
    post(client, "/runs/r1/drafts", action="move", block=first, position="1")
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["second", "edited"]

    post(client, "/runs/r1/drafts", action="remove", block=second)
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["edited"]


def test_a_trimmed_curation_is_restorable_from_the_page(client, drafted):
    """You trim hard to fit a merge prompt, the round goes badly, and you
    want the earlier selection back — so the versioning must be reachable
    from the only UI this feature has."""
    post(client, "/runs/r1/drafts", action="mine", text="keep me")
    version = curation.read(drafted)["version"]
    block = curation.read(drafted)["blocks"][0]["id"]
    post(client, "/runs/r1/drafts", action="remove", block=block)
    assert curation.read(drafted)["blocks"] == []

    post(client, "/runs/r1/drafts", action="revert", version=str(version))
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["keep me"]


def test_the_staging_note_is_saved(client, drafted):
    post(client, "/runs/r1/drafts", action="note", note="Tighten the results.")
    assert curation.read(drafted)["note"] == "Tighten the results."


def test_the_curation_is_shown_with_where_each_passage_came_from(client, drafted):
    curation.keep(drafted, "kept text", agent="codex", section="results")
    page = client.get("/runs/r1/drafts")
    assert "kept text" in page.text
    assert "codex" in page.text and "results" in page.text


def test_a_refusal_comes_back_as_a_message_not_a_500(client, drafted):
    response = post(client, "/runs/r1/drafts",
                    action="edit", block="nope", text="x")
    assert response.status_code == 200
    assert "block" in response.text.lower()


def test_an_unknown_action_is_refused(client, drafted):
    response = post(client, "/runs/r1/drafts", action="destroy")
    assert response.status_code in (200, 400)
    assert curation.read(drafted)["blocks"] == []


def test_a_passage_cannot_inject_script_into_the_page(client, drafted):
    """REVIEW FOCUS 1, and this app's own history: a stored-XSS bug shipped
    in an earlier milestone. A kept passage is arbitrary text from a file an
    agent wrote, rendered back into HTML.

    The two raw-tag checks below (not the bare substring `onerror=alert`,
    which correct escaping can never remove — the `=` inside it is not one
    of the five characters `&<>'"` HTML escaping touches, so it survives
    intact inside a *correctly* escaped `&lt;img ... onerror=alert(1)&gt;`
    exactly as much as inside a raw, dangerous one) mirror the payload and
    assertions `test_run_page_escapes_a_malicious_event_type_server_side`
    in `tests/web/test_pages.py` already uses for this exact `<img
    onerror>` string against a different page — a precedent this test
    follows rather than a substring check no genuinely-escaped render could
    ever satisfy."""
    payload = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
    curation.keep(drafted, payload, agent="claude", section="results")
    page = client.get("/runs/r1/drafts")
    assert "<script>alert(\"xss\")</script>" not in page.text
    assert "<img src=x onerror=alert(1)>" not in page.text
    assert "&lt;script&gt;" in page.text, "shown as text, so you can see what it says"


def test_agent_written_latex_is_escaped_too(client, drafted):
    """The draft source itself reaches HTML, and an agent wrote it."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        '<script>alert("from the tex")</script>')
    page = client.get("/runs/r1/drafts")
    assert "<script>alert" not in page.text


def test_the_run_page_links_to_the_workbench(client, drafted):
    page = client.get("/runs/r1")
    assert "/runs/r1/drafts" in page.text


def test_the_page_offers_the_merging_agent_and_the_send_button(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert 'value="merge"' in page.text, "the button that sends the round"
    assert "/runs/r1/say" in page.text or 'name="agent"' in page.text, (
        "the merging agent is switchable from here")


def test_selection_capture_posts_the_agent_and_section(client, drafted):
    """The whole capture mechanism: `window.getSelection()` plus the two
    provenance fields. No editor framework, no build step."""
    page = client.get("/runs/r1/drafts")
    assert "getSelection" in page.text
    assert 'name="agent"' in page.text and 'name="section"' in page.text
