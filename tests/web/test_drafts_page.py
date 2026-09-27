"""The workbench page: three panels over one run's drafts."""

import re
import sys
from pathlib import Path

import pytest

from scieflow.core.run import conversation, curation
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


def _responder_cmd() -> str:
    """A one-line `python -c` command that always answers successfully for
    any prompt, whatever it contains — mirrors `tests/core/test_merge_round.
    py`'s own `responder` fixture exactly, because `stub_agent`'s
    conversation mode only ever answers a prompt carrying a literal `kind:
    conversation` line, which the real merge prompt never carries (that
    module's own `test_a_turn_that_runs_but_does_not_succeed_does_not_
    advance_the_round` pins this: `stub` genuinely runs the merge turn and
    genuinely fails it, every time). So `stub` can prove this route reaches
    `service.merge_round` and dispatches a turn, but only an agent shaped
    like this one can prove the round actually advances end to end."""
    code = ('import json;print(json.dumps({"session_id": "resp-1"}));'
           'print(json.dumps({"type": "result", "result": "merged okay"}))')
    return f"{sys.executable} -c '{code}' {{prompt}}"


@pytest.fixture
def responder(project):
    """A fake conversable agent that always answers successfully, installed
    the way `tests/core/test_merge_round.py` and `tests/web/test_sse.py`'s
    `slow` agent both do: appended to the project's own `agents.yml`.
    Returns the agent's name."""
    cmd = _responder_cmd()
    yaml_cmd = cmd.replace('"', '\\"')
    agents_yml = Path(project.root) / "config" / "agents.yml"
    agents_yml.write_text(agents_yml.read_text() + (
        f'  responder:\n    cmd: "{yaml_cmd}"\n    session_cmd: "{yaml_cmd}"\n'
        f'    resume_cmd: "{yaml_cmd}"\n    family: claude\n    enabled: true\n'
        "    timeout_min: 1\n"
    ))
    return "responder"


def test_the_page_shows_every_agents_draft(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert "claude" in page.text and "codex" in page.text
    assert "per claude" in page.text and "per codex" in page.text


def test_one_escaping_symlink_does_not_404_the_whole_workbench(client, drafted, tmp_path):
    """The page's stake in the `agents()`/`rounds()` filtering fix. Before it,
    `ln -s /anywhere manuscript/drafts/x` inside a run — which any drafting
    agent can do — made `service.workbench` raise, which `drafts_page` turned
    into a 404 for the entire workbench, for good: the only escape was
    deleting the symlink in a terminal, which is what AGENTS.md rule 4 exists
    to avoid. One stray symlink must cost one hidden column, not the page."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "results.tex").write_text("not this run's\n")
    (drafted / "manuscript" / "drafts" / "escapee").symlink_to(outside)

    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200, "one symlink must not 404 the workbench"
    assert "claude" in page.text and "codex" in page.text, (
        "the other agents' columns must still be there")
    assert "escapee" not in page.text, "the escaping directory is hidden, not served"
    assert "not this run" not in page.text


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
    """Not just "nothing was created" — an implementation that silently
    ignored an unrecognized action would also leave `blocks` empty and pass
    that check alone. The message must actually say so."""
    response = post(client, "/runs/r1/drafts", action="destroy")
    assert response.status_code in (200, 400)
    assert "unknown action" in response.text.lower()
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
    """The draft source itself reaches HTML, and an agent wrote it.

    Asserts the escaped form is present too, not just the raw form's
    absence — an absence-only check passes just as well for a page that
    never echoes the draft at all, which is the exact defect this app has
    shipped before (`tests/web/test_pages.py` makes the same both-sides
    check for its own payload)."""
    (drafted / "manuscript" / "drafts" / "claude" / "results.tex").write_text(
        '<script>alert("from the tex")</script>')
    page = client.get("/runs/r1/drafts")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;alert(&#34;from the tex&#34;)&lt;/script&gt;" in page.text


def test_the_run_page_links_to_the_workbench(client, drafted):
    page = client.get("/runs/r1")
    assert "/runs/r1/drafts" in page.text


def test_the_page_offers_the_merging_agent_and_the_send_button(client, drafted):
    page = client.get("/runs/r1/drafts")
    assert 'value="merge"' in page.text, "the button that sends the round"
    assert "/runs/r1/say" in page.text, "the merging agent is switchable from here"


def test_the_merging_agent_selector_lists_conversational_agents_not_draft_authors(
        client, drafted):
    """REVIEW FINDING 1: `service.workbench`'s own `agents` key (the draft
    *authors* — "claude"/"codex" here) must not shadow `service.
    conversational_agents` (this fixture's project declares "stub"/"stub2"
    as conversable, and "sleepy"/"stub_disabled" as not) in the template
    context. The two sets are disjoint by construction here, so this fails
    outright if the selector ever lists draft authors instead — which is
    exactly what a `**view` spread landing after a same-named `agents` key
    does, since `service.workbench`'s `agents` then wins unconditionally."""
    page = client.get("/runs/r1/drafts").text
    select_html = re.search(r'<select name="agent">(.*?)</select>', page, re.S).group(1)
    options = set(re.findall(r'<option[^>]*>([^<]*)</option>', select_html))
    assert options == {"stub", "stub2"}
    assert "claude" not in options and "codex" not in options


def test_the_send_region_explains_itself_when_no_agent_is_chosen(client, drafted):
    """REVIEW FINDING 2: the merge button is disabled whenever no
    conversation agent is chosen (`can_converse` is false with no agent at
    all), and disabled with no explanation was a dead end — the region must
    say why, the same way `run.html`'s own chat panel already does."""
    page = client.get("/runs/r1/drafts").text
    assert "choose an agent" in page.lower() or "choose one below" in page.lower()
    assert "disabled" in page  # the button itself is actually disabled


def test_selection_capture_posts_the_agent_and_section(client, drafted):
    """The whole capture mechanism: `window.getSelection()` plus the two
    provenance fields. No editor framework, no build step."""
    page = client.get("/runs/r1/drafts")
    assert "getSelection" in page.text
    assert 'name="agent"' in page.text and 'name="section"' in page.text


@pytest.fixture
def merged(drafted):
    """`drafted` plus one completed merge round, the state the convergence
    loop's second round starts from."""
    round1 = drafted / "manuscript" / "curation" / "rounds" / "1"
    round1.mkdir(parents=True)
    (round1 / "results.tex").write_text("Merged: yield was 95\\% in both drafts.\n")
    return drafted


def test_a_merged_rounds_output_is_selectable_like_a_draft(client, merged):
    """The spec has a round's output "become the next round's left-hand
    pane". The round panes carried neither `data-agent` nor `data-section`,
    so the capture handler walked up, found no `dataset.agent` and returned —
    round 2's curation could only ever be built from round 1's *inputs*, and
    the only workaround was pasting round text into "Add as your own words",
    which records agent prose as the author's own: the exact provenance lie
    this feature exists to prevent."""
    page = client.get("/runs/r1/drafts")
    assert page.status_code == 200
    assert 'data-agent="round:1"' in page.text, (
        "a merged round's pane must carry its provenance, or it cannot be kept from")
    assert 'data-section="results"' in page.text


def test_keeping_from_a_round_records_the_round_as_the_source(client, merged):
    """And the page then says so honestly, as a round rather than as an agent
    named `round:1`."""
    response = post(client, "/runs/r1/drafts", **{
        "action": "keep", "text": "Merged: yield was 95\\% in both drafts.",
        "agent": "round:1", "section": "results"})
    assert response.status_code in (200, 303)
    block = curation.read(merged)["blocks"][0]
    assert block["agent"] == "round:1" and block["section"] == "results"

    page = client.get("/runs/r1/drafts")
    assert "merged round 1" in page.text, (
        "the curation list must name a round as a round, not as an agent")


def test_the_selection_handler_never_writes_the_own_words_textarea(client, drafted):
    """The textarea clobber: one shared textarea meant a qualifying selection
    ran `form.text.value = chosen` over whatever had been typed for "Add as
    your own words". Two forms now, and the script only ever touches the keep
    form's fields."""
    page = client.get("/runs/r1/drafts")
    assert 'id="keep-form"' in page.text and 'id="own-form"' in page.text
    script = page.text.split("<script>")[-1]
    assert "own-form" not in script, (
        "nothing in the capture handler may reach the own-words form")
    assert script.count("getElementById('keep-form')") >= 1


def test_a_selection_outside_any_pane_clears_the_pending_provenance(client, drafted):
    """The handler used to `return` when it found no `dataset.agent`, leaving
    the keep form holding the PREVIOUS selection's provenance — so a keep
    from an unlabelled region silently stored a duplicate of an earlier
    passage under an earlier agent's name, with nothing to tell the
    researcher which had happened. The three fields now move together, and
    the page states the pending provenance."""
    page = client.get("/runs/r1/drafts")
    script = page.text.split("<script>")[-1]
    assert "form.agent.value = pane ?" in script, (
        "provenance must be set from the found pane or cleared, never left stale")
    assert 'id="keep-provenance"' in page.text, (
        "the page must say what provenance a keep would record")


def test_a_reflected_error_is_escaped(client, drafted):
    """`?error=` is attacker-supplied query text landing on a brand new
    page; nothing else in this test module covers it, and it is reached by
    a plain GET (a malicious redirect link, not just this page's own
    refusals)."""
    payload = '<script>alert("reflected")</script>'
    page = client.get("/runs/r1/drafts", params={"error": payload})
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;alert(&#34;reflected&#34;)&lt;/script&gt;" in page.text


def test_a_non_numeric_position_does_not_crash_the_page(client, drafted):
    """Pins `_as_int`: `position` and `version` arrive as text from a hidden
    form field and can be anything. Without `_as_int`, `int("nope")` raises
    a bare `ValueError` that reaches the user as a 500."""
    post(client, "/runs/r1/drafts", action="mine", text="only block")
    block = curation.read(drafted)["blocks"][0]["id"]
    response = post(client, "/runs/r1/drafts", action="move", block=block, position="nope")
    assert response.status_code == 200
    assert "not a number" in response.text.lower()
    assert [b["text"] for b in curation.read(drafted)["blocks"]] == ["only block"]


def test_a_non_numeric_version_does_not_crash_the_page(client, drafted):
    response = post(client, "/runs/r1/drafts", action="revert", version="nope")
    assert response.status_code == 200
    assert "not a number" in response.text.lower()


def test_merging_the_round_dispatches_to_the_merging_agent_and_can_advance(
        client, project, drafted, responder):
    """REVIEW FINDING 3: nothing else in this module ever posts
    `action=merge` — a typo mis-wiring that branch would fall through to
    the `else` case (`unknown action: merge`) and this suite would still be
    fully green, on the one action with real side effects (an agent turn,
    budget spent, the round advanced). `responder` (not `stub`: see its own
    docstring) always answers successfully, so a genuine end-to-end
    advance — not just "no 500" — is what this pins."""
    conversation.set_agent(project.run_dir("r1"), responder)
    curation.keep(drafted, "The catalyst degrades above 400 K.",
                  agent="claude", section="results")
    assert curation.read(drafted)["round"] == 1

    response = post(client, "/runs/r1/drafts", action="merge")
    assert response.status_code == 200
    assert curation.read(drafted)["round"] == 2
    assert conversation.read(drafted)["turns"][-1]["text"] == "merged okay"


def test_merging_an_empty_curation_is_refused_as_a_message_not_a_500(client, drafted):
    """The other half of REVIEW FINDING 3: `service.merge_round` refuses
    before ever dispatching a turn when there is nothing to merge — no
    conversation agent needs to be configured at all for this path, so it
    is a distinct case from the happy path above, not a weaker copy of it.

    Asserts `service.merge_round`'s own, exact refusal text
    ("nothing to merge: keep a passage, write your own text, or leave a
    note"), not just the bare phrase "nothing to merge" — the page's own
    static copy in the "Send this round" region ("there is nothing to merge
    to otherwise", shown whenever no conversation agent is chosen, which
    this fixture also never sets) contains that exact bare phrase and made
    an earlier, looser version of this assertion pass even with the `merge`
    branch stubbed out to do nothing at all — caught only by falsifying it."""
    assert curation.read(drafted)["blocks"] == []
    response = post(client, "/runs/r1/drafts", action="merge")
    assert response.status_code == 200
    assert ("nothing to merge: keep a passage, write your own text, or leave a note"
            in response.text.lower())
    assert curation.read(drafted)["round"] == 1


def test_a_merge_turn_that_runs_but_does_not_succeed_says_so_on_the_page(
        client, project, drafted):
    """`service.merge_round` deliberately does not raise for a turn that ran
    and did not succeed — it genuinely happened, it is on the run's own
    conversation and budget, and the round is left where it was. The route
    discarded that return value and 303'd back with no error, so a merge that
    timed out, failed or was cancelled looked exactly like one that never
    happened: no message, no new column, no number. The researcher had to open
    the run's job list to discover they had spent budget.

    `stub` fails a merge turn deterministically — it answers only a prompt
    carrying a literal `kind: conversation` line, which the merge prompt never
    does — which is what `tests/core/test_merge_round.py::test_a_turn_that_
    runs_but_does_not_succeed_does_not_advance_the_round` already relies on.
    """
    conversation.set_agent(project.run_dir("r1"), "stub")
    curation.keep(drafted, "The catalyst degrades above 400 K.",
                  agent="claude", section="results")

    response = post(client, "/runs/r1/drafts", action="merge")
    assert response.status_code == 200
    assert curation.read(drafted)["round"] == 1, "the round must not advance"
    assert conversation.read(drafted)["turns"], "the turn genuinely happened"
    assert "did not succeed" in response.text, (
        "a merge that ran and failed must not look like one that never ran")
    assert "job list" in response.text, "and must say where to find out why"


def test_the_send_region_shows_the_current_round_number(client, project, drafted, responder):
    """With the round on the page, a failed turn is visible as a number that
    did not move — which is the other half of the fix above."""
    conversation.set_agent(project.run_dir("r1"), responder)
    curation.keep(drafted, "kept", agent="claude", section="results")
    assert "Send round 1" in client.get("/runs/r1/drafts").text

    post(client, "/runs/r1/drafts", action="merge")
    assert curation.read(drafted)["round"] == 2
    assert "Send round 2" in client.get("/runs/r1/drafts").text
