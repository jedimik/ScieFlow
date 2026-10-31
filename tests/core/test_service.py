import time

import pytest

from scieflow.core import service


ROLE = "loop.experiment"   # M-A A-5: a dispatch inside a run names its role


@pytest.fixture
def running_turn(project):
    """A turn already in flight for r1: a still-running job dispatched
    conversationally (`kind="turn"`), so the busy check in `service.say` has
    an actual *turn* to refuse against — not just any agent job (see
    `test_a_non_turn_agent_job_does_not_block_say`, which checks the other
    side of that same distinction). Uses `sleepy_turn`'s `sleep 300` command,
    same idea as `test_dispatch_detached_then_cancel`, and cancels it on
    teardown."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "sleepy_turn")
    prompt = ws / "logs" / "running.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy_turn", prompt, ws / "logs" / "running.out.md",
                                 detach=True, conversational=True)
    yield job
    service.cancel_job(project, job["id"])


def test_list_and_detail(project):
    assert [r["slug"] for r in service.list_runs(project)] == ["r1"]
    detail = service.run_detail(project, "r1")
    assert detail["status"]["run"] == "r1"
    assert detail["gates"] == [] and detail["jobs"] == [] and detail["events"] == []


def test_dispatch_blocking_records_a_job(project):
    ws = project.run_dir("r1")
    out = ws / "iterations" / "h.md"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    assert job["state"] == "done" and out.exists()
    assert (ws / "logs" / "t.md").exists()
    assert service.run_detail(project, "r1")["jobs"][0]["id"] == job["id"]


def test_dispatch_detached_then_cancel(project):
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "t.md", detach=True, role=ROLE)
    assert job["state"] == "running"
    cancelled = service.cancel_job(project, job["id"])
    assert cancelled["state"] == "cancelled"


def test_gates_across_runs(project):
    from scieflow.core import gates

    ws = project.run_dir("r1")
    g = gates.open_gate(project, ws, "question", "Which dataset?", options=["A", "B"])
    assert [x["id"] for x in service.open_gates(project)] == [g["id"]]
    assert service.open_gates(project)[0]["slug"] == "r1"
    service.answer_gate(project, "r1", g["id"], "A")
    assert service.open_gates(project) == []


def test_unknown_run_is_a_service_error(project):
    with pytest.raises(service.ServiceError):
        service.run_detail(project, "nope")


def test_dispatch_through_the_service_is_sandboxed(project, monkeypatch):
    """The web app must inherit the boundary without knowing it exists."""
    seen = {}
    real_start = service.jobs.start

    def spy(project_, argv, **kw):
        seen.update(kw)
        return real_start(project_, argv, **kw)

    monkeypatch.setattr(service.jobs, "start", spy)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    assert seen["sandbox_writable"] is not None
    assert ws in seen["sandbox_writable"]


def test_service_dispatch_refuses_without_a_sandbox(project, monkeypatch):
    from scieflow.core import sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError, match="bubblewrap"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)


def test_service_refusal_leaves_a_job_refused_event_on_the_timeline(project, monkeypatch):
    """agent_run.main() emits job.refused on this path; the service must too,
    or a refusal from the web app leaves nothing on the run's history."""
    from scieflow.core import events, sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    refused = [e for e in events.read(ws) if e["type"] == "job.refused"]
    assert len(refused) == 1
    assert refused[0]["data"]["reason"] == "sandbox"


def test_service_escape_hatch_leaves_a_sandbox_disabled_event(project):
    """agent_run.main() emits sandbox.disabled on this path; the service must
    too, so an unsandboxed dispatch from the web app is visible in the run's
    history and not only via the per-job marker on the page."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    (project.root / "config" / "sandbox.yml").write_text(
        "unsandboxed_runs:\n  - slug: r1\n    reason: a human decided\n")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    assert job["state"] == "done"
    disabled = [e for e in events.read(ws) if e["type"] == "sandbox.disabled"]
    assert len(disabled) == 1
    assert disabled[0]["data"]["why"] == "config/sandbox.yml unsandboxed_runs"


def test_service_ignores_a_run_config_that_tries_to_disable_the_sandbox(project):
    """The same escape as on the CLI path, through the web app: an agent that
    appends `sandbox: off` to its own run config must not get an unconfined
    dispatch out of the service layer either."""
    ws = project.run_dir("r1")
    config_path = ws / "config.yml"
    config_path.write_text(config_path.read_text() + "sandbox: off\n")
    out = ws / "out.md"
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {out}\nkind: hypothesis\n")
    with pytest.raises(service.ServiceError, match="config/sandbox.yml"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    assert not out.exists()


def test_normal_sandboxed_dispatch_leaves_neither_event(project):
    """The new emissions must not fire on the happy path."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md", role=ROLE)
    assert job["state"] == "done"
    types = {e["type"] for e in events.read(ws)}
    assert "job.refused" not in types
    assert "sandbox.disabled" not in types


def test_mark_phase_through_the_service(project):
    from scieflow.core.run import status

    service.mark_phase(project, "r1", "hypothesize", "running")
    assert status.read_status(project.run_dir("r1"))["phases"]["hypothesize"] == "running"


def test_service_run_actions_reject_a_bad_slug(project):
    for call in (
        lambda: service.mark_phase(project, "nope", "hypothesize", "running"),
        lambda: service.advance_run(project, "nope"),
        lambda: service.checkpoint_run(project, "nope", "user"),
        lambda: service.resume_run(project, "nope"),
        lambda: service.record_spend(project, "nope", experiment_runs=1),
    ):
        with pytest.raises(service.ServiceError):
            call()


def test_mark_phase_rejects_an_invalid_state(project):
    with pytest.raises(service.ServiceError, match="state"):
        service.mark_phase(project, "r1", "hypothesize", "banana")


def test_checkpoint_then_resume_round_trip(project):
    from scieflow.core.run import status

    service.checkpoint_run(project, "r1", "user", detail="stepping away")
    assert status.read_status(project.run_dir("r1"))["stopped"]["reason"] == "user"
    service.resume_run(project, "r1")
    assert not status.read_status(project.run_dir("r1")).get("stopped")


def test_advance_refused_when_the_iteration_budget_is_spent(project):
    """A refusal must arrive as ServiceError — and must leave the run
    checkpointed exactly as the CLI leaves it, not half-changed."""
    from scieflow.core.run import budget, status

    ws = project.run_dir("r1")
    budget.write_budget(ws, budget.new_budget(1, 10, 60))
    service.record_spend(project, "r1", iterations=1)
    with pytest.raises(service.ServiceError):
        service.advance_run(project, "r1")
    assert status.read_status(ws)["stopped"]["reason"] == "low-budget"


def test_record_spend_rejects_negative_values(project):
    """The budget ledger must never move backwards through the service layer
    — that is the one automatic brake on runaway agent spend."""
    from scieflow.core.run import budget

    budget.write_budget(project.run_dir("r1"), budget.new_budget(3, 10, 60))
    with pytest.raises(service.ServiceError, match="negative"):
        service.record_spend(project, "r1", experiment_runs=-5)
    assert budget.read_budget(project.run_dir("r1"))["spent"]["experiment_runs"] == 0


def test_record_spend_accumulates(project):
    from scieflow.core.run import budget

    # The fixture's run carries no budget.yml, and record_spend returns None
    # without one — which the service turns into a ServiceError.
    budget.write_budget(project.run_dir("r1"), budget.new_budget(3, 10, 60))
    service.record_spend(project, "r1", experiment_runs=2)
    service.record_spend(project, "r1", experiment_runs=3)
    assert budget.read_budget(project.run_dir("r1"))["spent"]["experiment_runs"] == 5


def test_say_dispatches_a_turn_and_records_both_sides(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    result = service.say(project, "r1", "What should we try next?")

    turns = conversation.read(ws)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert turns[0]["text"] == "What should we try next?"
    assert turns[1]["job_id"] == result["job"]["id"]


def test_say_completes_a_conversational_turn_and_resumes_it(project):
    """Finding 3 (2026-09-25 review): before the stub had a conversational
    mode, every `say` test dispatched a prompt with no `output:`/`kind:`
    lines the stub understood, so it exited 1 and every one of these tests
    passed against a *failing* dispatch with an empty agent turn — nothing
    offline ever exercised a successful turn or the resume path, which is
    the one assumption this whole feature rests on."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    first = service.say(project, "r1", "kind: conversation\nWhat should we try next?")

    doc = conversation.read(ws)
    session = doc["session"]
    assert session, "no session id was recorded from the first turn"
    assert doc["turns"][1]["text"] == (
        "stub heard: kind: conversation\nWhat should we try next?")
    assert doc["turns"][1]["job_id"] == first["job"]["id"]

    second = service.say(project, "r1", "kind: conversation\nAnd then?")
    assert session in second["job"]["argv"], (
        "the session id turn one recorded never reached turn two's argv")


def test_a_non_turn_agent_job_does_not_block_say(project):
    """Finding 4 (2026-09-25 review): `_turn_in_flight` used to match any
    running `kind="agent"` job — which is every dispatch a coordinator makes
    (a campaign, a review), not just a chat turn. A plain agent dispatch
    running in the same workspace must not disable the chat."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    prompt = ws / "logs" / "campaign.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "campaign.out.md",
                                 detach=True, role=ROLE)   # kind="agent", not a turn
    try:
        assert service.conversation_state(project, "r1")["busy"] is False
        result = service.say(project, "r1", "kind: conversation\nstill there?")
        assert result["job"]["state"] == "done"
    finally:
        service.cancel_job(project, job["id"])


def test_say_pins_the_charter_into_the_turn(project):
    """The whole point of the charter is that every turn carries it. A turn
    that composed its own prompt would bypass that silently.

    `say` writes the raw message to `logs/turn-*.md` (see
    `test_say_pins_the_charter_exactly_once` for why) — the charter shows up
    only once the prompt is actually composed, which happens inside
    `agent_run.prepare`. So this asserts against what was actually dispatched
    (the stub's argv, which embeds the whole composed prompt as its trailing
    `{prompt}` token) rather than against the file on disk.
    """
    from scieflow.core.run import charter, conversation

    ws = project.run_dir("r1")
    charter.set_text(ws, "Goal: characterise the catalyst.")
    conversation.set_agent(ws, "stub")
    result = service.say(project, "r1", "next step?")

    dispatched = " ".join(result["job"]["argv"])
    assert "characterise the catalyst" in dispatched
    assert dispatched.index("characterise the catalyst") < dispatched.index("next step?")


def test_say_pins_the_charter_exactly_once(project):
    """`say` must not compose the prompt itself and then let `agent_run.prepare`
    compose it again — `prepare` is the one place every dispatch is composed,
    and composing twice would carry two copies of the charter into every turn."""
    from scieflow.core.run import charter, conversation

    ws = project.run_dir("r1")
    charter.set_text(ws, "Goal: characterise the catalyst.")
    conversation.set_agent(ws, "stub")
    result = service.say(project, "r1", "next step?")

    dispatched = " ".join(result["job"]["argv"])
    assert dispatched.count("characterise the catalyst") == 1


def test_say_refuses_when_no_agent_is_chosen(project):
    with pytest.raises(service.ServiceError, match="agent"):
        service.say(project, "r1", "hello")


def test_say_refuses_an_agent_that_cannot_hold_a_session(project):
    """The spec is explicit: an agent that cannot report a session id must be
    refused plainly, not silently restarted on every turn."""
    from scieflow.core import events
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "sleepy")   # no session_cmd
    with pytest.raises(service.ServiceError, match="conversation"):
        service.say(project, "r1", "hello")
    # Finding 7 (2026-09-25 review): a pre-flight refusal — this one is
    # cheap and has no side effect — must leave no human turn and no
    # `turn.sent` event: nothing was ever attempted for this message.
    assert conversation.read(ws)["turns"] == []
    assert not [e for e in events.read(ws) if e["type"] == "turn.sent"]


def test_say_preflight_refuses_an_unknown_agent_before_writing_anything(project):
    from scieflow.core import events
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "no-such-agent")
    with pytest.raises(service.ServiceError, match="unknown agent"):
        service.say(project, "r1", "hello")
    assert conversation.read(ws)["turns"] == []
    assert not [e for e in events.read(ws) if e["type"] == "turn.sent"]


def test_say_keeps_the_human_turn_when_the_dispatch_itself_fails(project, monkeypatch):
    """The other side of finding 7: sandbox wrapping fails at actual launch
    after successful real pre-flight verification. The already-recorded human
    attempt must stay, unlike a pre-flight refusal."""
    from scieflow.core import events, sandbox
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    real_verify = sandbox.verify
    real_launch = service._launch_prepared_turn
    reached = {"verify": 0, "launch": 0}

    def verify(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        reached["verify"] += 1
        return result

    def launch(project, prepared, *, artifacts, turn_audit):
        assert reached["verify"] == 1
        attempted = conversation.read(ws)["turns"]
        assert len(attempted) == 1 and attempted[0]["role"] == "human" and attempted[0]["text"] == "hello"
        reached["launch"] += 1
        monkeypatch.setattr(sandbox, "available", lambda: False)
        return real_launch(project, prepared, artifacts=artifacts, turn_audit=turn_audit)

    monkeypatch.setattr(sandbox, "verify", verify)
    monkeypatch.setattr(service, "_launch_prepared_turn", launch)
    with pytest.raises(service.ServiceError, match="bubblewrap"):
        service.say(project, "r1", "hello")
    turns = conversation.read(ws)["turns"]
    assert len(turns) == 1 and turns[0]["role"] == "human" and turns[0]["text"] == "hello"
    assert len([e for e in events.read(ws) if e["type"] == "turn.sent"]) == 1
    assert reached == {"verify": 1, "launch": 1}
    recorded = events.read(ws)
    assert len([e for e in recorded if e["type"] == "job.queued"]) == 1
    assert len([e for e in recorded if e["type"] == "job.failed"]) == 1
    assert not [e for e in recorded if e["type"] == "job.started"]


def test_say_refuses_while_a_turn_is_still_running(project, running_turn):
    """Two concurrent resumes of one session is not something either CLI
    promises to handle, and two jobs appending one record is a lost update."""
    with pytest.raises(service.ServiceError, match="still"):
        service.say(project, "r1", "and another thing")


def test_say_refuses_an_empty_message(project):
    from scieflow.core.run import conversation

    conversation.set_agent(project.run_dir("r1"), "stub")
    with pytest.raises(service.ServiceError):
        service.say(project, "r1", "   ")


def test_conversation_reports_whether_it_can_converse(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "sleepy")
    assert service.conversation_state(project, "r1")["can_converse"] is False
    conversation.set_agent(ws, "stub")
    assert service.conversation_state(project, "r1")["can_converse"] is True


def test_conversation_state_reports_no_session_lost_before_any_turn(project):
    from scieflow.core.run import conversation

    conversation.set_agent(project.run_dir("r1"), "stub")
    assert service.conversation_state(project, "r1")["session_lost"] is False


def test_conversation_state_reports_a_lost_session(project):
    """Finding 6 (2026-09-25 review): `record_session(ws, None)` leaves a
    prior id alone by design, but if the CLI never reported one at ALL —
    here, the stub's own "missing directives" failure, same shape as any
    crashed turn — the session stays unset forever and nothing says so
    anywhere but a careful read of `conversation.yml`."""
    from scieflow.core import events
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    service.say(project, "r1", "no marker here, so the stub fails and reports no id")

    state = service.conversation_state(project, "r1")
    assert state["session_lost"] is True
    assert [e for e in events.read(ws) if e["type"] == "turn.session_lost"]


def test_a_turn_with_empty_parsed_text_does_not_erase_the_transcript(project, monkeypatch):
    """Minor (2026-09-25 review): `say` used to record any turn — failed,
    cancelled or timed out — as an ordinary reply with empty text, and then
    overwrite the transcript with that same empty text, discarding whatever
    raw output the job actually wrote. Skipping the overwrite when the
    parsed text is empty, and recording the job's state on the turn, are
    this fix's two halves."""
    from scieflow.core import sessions
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    monkeypatch.setattr(sessions, "parse", lambda cfg, out: sessions.Session(None, "  "))

    service.say(project, "r1", "kind: conversation\nhi")

    [transcript] = list((ws / "logs").glob("turn-*.out.md"))
    assert "session_id" in transcript.read_text(), "the raw output was erased"
    turn = conversation.read(ws)["turns"][1]
    assert turn["text"] == "  "
    assert turn["state"] == "done"


def test_switching_the_agent_starts_a_fresh_session(project):
    """The new agent has no session of its own, so its first turn must start
    one rather than resuming an id that belongs to a different CLI."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    service.set_conversation_agent(project, "r1", "stub2")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub2" and doc["session"] is None


def test_switching_keeps_the_turns_already_said(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.add_turn(ws, role="human", text="earlier question")
    service.set_conversation_agent(project, "r1", "stub2")
    assert conversation.read(ws)["turns"][0]["text"] == "earlier question"


def test_switching_to_an_unknown_agent_is_refused(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    with pytest.raises(service.ServiceError, match="unknown agent"):
        service.set_conversation_agent(project, "r1", "nonesuch")
    # Verify nothing changed
    doc = conversation.read(ws)
    assert doc["agent"] == "stub" and doc["session"] == "stub-session"


def test_switching_to_an_agent_that_cannot_converse_is_refused(project):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    with pytest.raises(service.ServiceError, match="conversation"):
        service.set_conversation_agent(project, "r1", "sleepy")
    # Verify nothing changed
    doc = conversation.read(ws)
    assert doc["agent"] == "stub" and doc["session"] == "stub-session"


def test_switching_to_a_disabled_agent_is_refused(project):
    """Minor (2026-09-25 review): `enabled: false` (registry-wide, not tied
    to any run) must stop an agent from being handed a conversation, the
    same as an unknown one or one that cannot converse at all."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    with pytest.raises(service.ServiceError, match="disabled"):
        service.set_conversation_agent(project, "r1", "stub_disabled")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub" and doc["session"] == "stub-session"


def test_conversational_agents_excludes_disabled_and_non_conversational_agents(project):
    """What the run page's hand-over picker offers (`pages.run_page`): only
    agents `set_conversation_agent` would actually accept."""
    agents = service.conversational_agents(project)
    assert "stub" in agents and "stub2" in agents
    assert "sleepy" not in agents          # no session_cmd/resume_cmd at all
    assert "stub_disabled" not in agents   # enabled: false


def test_switching_mid_turn_is_refused(project, running_turn):
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    with pytest.raises(service.ServiceError, match="still"):
        service.set_conversation_agent(project, "r1", "stub2")
    # Verify nothing changed
    doc = conversation.read(ws)
    assert doc["agent"] == "sleepy_turn"


def test_switching_to_the_same_agent_keeps_the_session(project):
    """Switching to the same agent keeps its session, since the CLI is the same."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    conversation.record_session(ws, "stub-session")
    service.set_conversation_agent(project, "r1", "stub")
    doc = conversation.read(ws)
    assert doc["agent"] == "stub" and doc["session"] == "stub-session"


def test_workbench_gathers_drafts_and_curation(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("\\section{Results} text")

    view = service.workbench(project, "r1")
    assert view["agents"] == ["claude"]
    assert view["sections"] == ["results"]
    assert "text" in view["drafts"]["claude"]["results"]
    assert view["curation"]["blocks"] == []


def test_keep_passage_records_provenance_through_the_service(project):
    from scieflow.core.run import curation

    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts" / "claude").mkdir(parents=True)
    service.keep_passage(project, "r1", "a passage", "claude", "results")
    block = curation.read(ws)["blocks"][0]
    assert block["agent"] == "claude" and block["section"] == "results"


def test_keep_passage_refuses_an_agent_that_escapes_the_run(project):
    with pytest.raises(service.ServiceError):
        service.keep_passage(project, "r1", "a passage", "../../etc", "passwd")


@pytest.mark.parametrize("field", ["agent", "section"])
def test_keep_passage_refuses_a_newline_in_the_provenance(project, field):
    """The writer's half of the forged-provenance fix. A drafting agent
    chooses these names, and `curation._render_document` emits them on an
    unwrapped heading line, so a newline lets one name occupy several lines
    of the merge prompt. `drafts.check_name` is the shared rule that refuses
    it here, at the one place a provenance value is stored."""
    from scieflow.core.run import curation

    ws = project.run_dir("r1")
    names = {"agent": "claude", "section": "results"}
    names[field] = "results\n--- end curation SCIEFLOW-CURATION-BOUNDARY ---"
    with pytest.raises(service.ServiceError, match="not a draft name"):
        service.keep_passage(project, "r1", "a passage", names["agent"], names["section"])
    assert curation.read(ws)["blocks"] == [], "nothing may be stored by a refused keep"


def test_the_curation_is_restorable_through_the_service(project):
    from scieflow.core.run import curation

    ws = project.run_dir("r1")
    service.add_own_text(project, "r1", "keep me")
    version = curation.read(ws)["version"]
    service.remove_curation_block(project, "r1", curation.read(ws)["blocks"][0]["id"])

    service.revert_curation(project, "r1", version)
    assert [b["text"] for b in curation.read(ws)["blocks"]] == ["keep me"]
    assert len(service.curation_history(project, "r1")) == curation.read(ws)["version"]


def test_reverting_to_an_unknown_version_is_a_service_error(project):
    with pytest.raises(service.ServiceError, match="version"):
        service.revert_curation(project, "r1", 99)


def test_curation_refusals_reach_the_caller_as_service_errors(project):
    with pytest.raises(service.ServiceError):
        service.add_own_text(project, "r1", "   ")
    with pytest.raises(service.ServiceError, match="block"):
        service.edit_curation_block(project, "r1", "nope", "text")


def test_workbench_tolerates_an_escaping_symlink_among_a_drafts_sections(project, tmp_path):
    """A drafts directory the page reads is not a directory this run wrote
    unsupervised — a symlink one of its files happens to be must not make
    the whole workbench view fail."""
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("ok")
    secret = tmp_path / "secret.tex"
    secret.write_text("not yours")
    (d / "sneaky.tex").symlink_to(secret)

    view = service.workbench(project, "r1")
    assert view["sections"] == ["results"]
    assert "sneaky" not in view["drafts"]["claude"]


def test_workbench_turns_a_draft_error_into_a_service_error(project, monkeypatch):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "drafts" / "claude"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("ok")

    def boom(*_args, **_kwargs):
        raise service.drafts.DraftError("boom")

    monkeypatch.setattr(service.drafts, "read_section", boom)
    with pytest.raises(service.ServiceError):
        service.workbench(project, "r1")


def test_workbench_turns_a_malformed_curation_document_into_a_service_error(project):
    """`curation.read`, called inside the same `workbench` block as every
    `drafts.*` call, raises `CurationError` on a document it cannot parse —
    that must reach the caller as `ServiceError` too, not escape as the
    bare `CurationError`."""
    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts" / "claude").mkdir(parents=True)
    curation_dir = ws / "manuscript" / "curation"
    curation_dir.mkdir(parents=True)
    (curation_dir / "document.yml").write_text("current: not-a-number\nversions: []\n")

    with pytest.raises(service.ServiceError):
        service.workbench(project, "r1")


CURATING = [
    ("add_own_text", ("text",)),
    ("edit_curation_block", ("block-id", "text")),
    ("move_curation_block", ("block-id", 0)),
    ("remove_curation_block", ("block-id",)),
    ("set_curation_note", ("note",)),
    ("revert_curation", (1,)),
    ("curation_history", ()),
]


@pytest.mark.parametrize("name,args", CURATING, ids=[n for n, _ in CURATING])
def test_every_curating_wrapper_translates_a_curation_error(project, name, args):
    """The translate rule lived in seven byte-for-byte identical bodies and
    now lives in `_curating`. This pins it for each public entry point at
    once, so collapsing them cannot quietly drop one — a `CurationError`
    escaping to a route would render as a 500 instead of the page's own
    refusal message.

    Driven by a malformed `document.yml`, the one condition every one of these
    hits on its very first read, rather than by monkeypatching each callee.
    """
    ws = project.run_dir("r1")
    curation_dir = ws / "manuscript" / "curation"
    curation_dir.mkdir(parents=True)
    (curation_dir / "document.yml").write_text("current: not-a-number\nversions: []\n")

    with pytest.raises(service.ServiceError):
        getattr(service, name)(project, "r1", *args)


def test_manuscript_history_reports_no_history_for_a_fresh_run(project):
    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert view["points"] == []
    assert "no history" in view["reason"].lower()


def test_manuscript_history_lists_points_after_a_round(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")

    view = service.manuscript_history(project, "r1")
    assert view["available"] is True
    assert "main:merge_1" in [p["ref"] for p in view["points"]]


def test_manuscript_history_degrades_when_git_is_missing(project, monkeypatch):
    from scieflow.core import provenance

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    view = service.manuscript_history(project, "r1")
    assert view["available"] is False
    assert view["points"] == []
    assert "git" in view["reason"].lower(), "the page must be able to say why"


def test_manuscript_diff_refuses_a_ref_that_is_not_a_point(project):
    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    service.manuscript_history(project, "r1")

    with pytest.raises(service.ServiceError, match="not a point"):
        service.manuscript_diff(project, "r1", "--output=/tmp/x", "main:merge_1")


def test_manuscript_diff_translates_a_provenance_error(project, monkeypatch):
    """Covers a `ProvenanceError` origin other than the whitelist refusal
    `test_manuscript_diff_refuses_a_ref_that_is_not_a_point` already covers.
    Calling `manuscript_diff` with no prior sync would take that *same*
    "not a point" path — via an empty `points()` whitelist rather than a
    populated one refusing an unlisted ref — so this instead drives a
    distinct origin: a git invocation failing partway through
    `provenance.diff` itself, to prove the translation is unconditional on
    where inside `provenance.diff` the error comes from.

    FALSIFICATION: remove the `except provenance.ProvenanceError` clause in
    `manuscript_diff` and this fails with the raised `ProvenanceError`
    propagating instead of `ServiceError`.
    """
    from scieflow.core import provenance

    def boom(ws, a, b):
        raise provenance.ProvenanceError("git diff failed: fatal: bad revision")

    monkeypatch.setattr(service.provenance, "diff", boom)

    with pytest.raises(service.ServiceError, match="git diff failed"):
        service.manuscript_diff(project, "r1", "main:merge_1", "main:merge_2")


def test_manuscript_history_refuses_an_unknown_run(project):
    with pytest.raises(service.ServiceError):
        service.manuscript_history(project, "nope")


def test_manuscript_history_keeps_readable_points_when_sync_fails(project, monkeypatch):
    """A transient sync failure (a lock race, a rebuild in flight) must not
    blank history the repo already holds. Falsify by putting `sync` and
    `points` back under one `try`: the view then reports no points."""
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    d = ws / "manuscript" / "curation" / "rounds" / "1"
    d.mkdir(parents=True)
    (d / "results.tex").write_text("merged\n")
    service.manuscript_history(project, "r1")      # a real repo with a real point

    def boom(ws):
        raise provenance.ProvenanceError("update-ref: cannot lock ref")

    monkeypatch.setattr(service.provenance, "sync", boom)
    view = service.manuscript_history(project, "r1")
    assert "main:merge_1" in [p["ref"] for p in view["points"]]


# --- run_overview (panel 1: what exists on disk) ---------------------------

def _touch(ws, *parts, text="x\n"):
    path = ws.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_run_overview_fresh_run_is_absent_everywhere_with_reasons(project):
    inv = service.run_overview(project, "r1")["inventory"]
    assert inv["drafts"]["state"] == "absent"
    assert "Phase 3" in inv["drafts"]["reason"]
    assert inv["merge"]["state"] == "absent"
    assert inv["review"]["state"] == "absent"
    for key in ("findings", "gaps", "drafts", "merge", "review"):
        assert inv[key]["reason"], key


def test_run_overview_an_empty_directory_is_not_an_absent_one(project):
    ws = project.run_dir("r1")
    (ws / "manuscript" / "drafts").mkdir(parents=True)
    (ws / "findings").mkdir()
    inv = service.run_overview(project, "r1")["inventory"]
    assert inv["drafts"]["state"] == "empty"
    assert inv["findings"]["state"] == "empty"
    assert inv["gaps"]["state"] == "absent"
    assert inv["drafts"]["reason"] != service.run_overview(
        project, "r1")["inventory"]["gaps"]["reason"]


def test_run_overview_mid_phase_uses_the_agents_the_run_wrote(project):
    ws = project.run_dir("r1")
    _touch(ws, "findings", "zed.json")
    _touch(ws, "findings", "amy.json")
    _touch(ws, "gaps", "amy.json")
    inv = service.run_overview(project, "r1")["inventory"]
    assert inv["findings"] == {**inv["findings"], "state": "present",
                               "names": ["amy", "zed"]}
    assert inv["gaps"]["names"] == ["amy"]
    assert inv["drafts"]["state"] == "absent"


def test_run_overview_drafts_without_a_merge(project):
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "drafts", "kim", "results.tex")
    _touch(ws, "manuscript", "drafts", "lee", "intro.tex")
    inv = service.run_overview(project, "r1")["inventory"]
    assert inv["drafts"]["state"] == "present"
    assert inv["drafts"]["agents"] == [
        {"agent": "kim", "sections": ["intro", "results"]},
        {"agent": "lee", "sections": ["intro"]}]
    assert inv["drafts"]["sections"] == ["intro", "results"]
    assert inv["merge"]["state"] == "absent"


def test_run_overview_a_complete_run(project):
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "curation", "rounds", "2", "intro.tex")
    _touch(ws, "manuscript", "curation", "rounds", "10", "intro.tex")
    _touch(ws, "review", "round-1", "review.md")
    _touch(ws, "review", "draft-round-1", "kim-on-lee.json")
    inv = service.run_overview(project, "r1")["inventory"]
    assert [r["n"] for r in inv["merge"]["rounds"]] == [2, 10]
    assert inv["merge"]["rounds"][0]["sections"] == ["intro"]
    assert {(r["kind"], r["n"]) for r in inv["review"]["rounds"]} == {
        ("review", 1), ("cross-review", 1)}


def test_run_overview_never_reaches_provenance_sync(project, monkeypatch):
    """The seam ticket 19 wires provenance into. `sync` costs ~0.8s of git
    subprocesses per call; only `points()` may ever be used here."""
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("run_overview must not call provenance.sync")

    ensure_calls = []
    real_ensure = service.provenance.ensure_repo

    def counted_ensure(*a, **k):
        # `ensure_repo` WRITES (creates the repo) - on a read-only page.
        ensure_calls.append(a)
        return real_ensure(*a, **k)

    monkeypatch.setattr(service.provenance, "sync", boom)
    monkeypatch.setattr(service.provenance, "ensure_repo", counted_ensure)
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "curation", "rounds", "1", "intro.tex")
    service.run_overview(project, "r1")
    assert ensure_calls == [], "run_overview must probe with repo_path, never ensure_repo"
    # The counter carries the guard: a panel that swallows the raise with a
    # broad `except Exception` (as _panel itself does) cannot hide the call.
    assert calls == []


def test_run_overview_sync_guard_survives_a_swallowed_raise(project, monkeypatch):
    """A sync that silently returns must still be caught: count calls."""
    calls = []
    monkeypatch.setattr(service.provenance, "sync", lambda *a, **k: calls.append(a))
    monkeypatch.setitem(service._PANELS, "sneaky",
                        lambda ws: service.provenance.sync(ws) or {"reason": ""})
    service.run_overview(project, "r1")
    assert len(calls) == 1, "the counter must see a call that raised nothing"


def test_run_overview_one_failing_panel_does_not_take_the_others_down(project, monkeypatch):
    def boom(ws):
        raise RuntimeError("panel two exploded")

    monkeypatch.setitem(service._PANELS, "boom", boom)
    _touch(project.run_dir("r1"), "manuscript", "drafts", "kim", "intro.tex")
    view = service.run_overview(project, "r1")
    assert "panel two exploded" in view["boom"]["reason"]
    assert view["inventory"]["drafts"]["agents"][0]["agent"] == "kim"


def test_run_overview_a_panel_failure_names_its_exception_class(project, monkeypatch, caplog):
    def boom(ws):
        return {}["x"]

    monkeypatch.setitem(service._PANELS, "boom", boom)
    with caplog.at_level("ERROR", logger=service.__name__):
        view = service.run_overview(project, "r1")
    assert "KeyError" in view["boom"]["reason"]
    assert any("overview panel boom failed" in r.getMessage() and r.exc_info
               for r in caplog.records), "the traceback must be logged"


def test_run_overview_calls_nothing_but_ws_and_panel():
    """A builder called directly from run_overview loses isolation, and a
    direct call that fails only on some run states would pass every other
    test and 500 the page. Check the call graph, not the output shape."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(service.run_overview))
    called = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert sorted(set(called)) == ["_panel", "_ws"], called


import os as _os

_ROOT = hasattr(_os, "geteuid") and _os.geteuid() == 0


@pytest.mark.skipif(_ROOT, reason="root ignores mode bits")
@pytest.mark.parametrize("sub,key", [("findings", "findings"),
                                     ("manuscript/drafts", "drafts")])
def test_run_overview_an_unreadable_directory_is_its_own_state(project, sub, key):
    ws = project.run_dir("r1")
    _touch(ws, "findings", "amy.json")
    target = ws / sub
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0)
    try:
        inv = service.run_overview(project, "r1")["inventory"]
    finally:
        target.chmod(0o755)
    assert inv[key]["state"] == "unreadable"
    assert "could not be read" in inv[key]["reason"]
    assert inv["gaps"]["state"] == "absent"


def test_run_overview_degrades_the_panel_on_an_unexpected_failure(project, monkeypatch):
    def boom(ws):
        raise OSError("disk went away")

    monkeypatch.setattr(service.drafts, "agents", boom)
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    inv = service.run_overview(project, "r1")["inventory"]
    assert "disk went away" in inv["reason"]


def test_run_overview_refuses_an_unknown_run(project):
    with pytest.raises(service.ServiceError):
        service.run_overview(project, "nope")


# --- run_overview (panel 2: where the manuscript stands) -------------------

def _two_merge_rounds(ws):
    """Drafts by two agents in three sections; round 1 has one section, round 2
    has two; round 2 changes results.tex and adds intro.tex."""
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _touch(ws, "manuscript", "drafts", "kim", "results.tex")
    _touch(ws, "manuscript", "drafts", "lee", "methods.tex")
    _touch(ws, "manuscript", "curation", "rounds", "1", "results.tex", text="v1\n")
    _touch(ws, "manuscript", "curation", "rounds", "2", "results.tex", text="v2\n")
    _touch(ws, "manuscript", "curation", "rounds", "2", "intro.tex", text="new\n")


def _synced(project):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _two_merge_rounds(ws)
    provenance.sync(ws)                       # set-up only; the panel must not do this
    return ws


def test_manuscript_panel_reports_round_sections_and_changed_files(project):
    _synced(project)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["state"] == "ok" and ms["reason"] == ""
    assert ms["round"] == 2
    assert ms["present"] == ["intro", "results"]
    assert ms["drafted"] == ["intro", "methods", "results"]
    assert ms["missing"] == ["methods"]
    assert ms["changed"] == ["intro.tex", "results.tex"]
    assert ms["compared"] == ["main:merge_2", "main:merge_1"]


def test_manuscript_panel_one_round_has_nothing_to_compare(project):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "curation", "rounds", "1", "results.tex")
    provenance.sync(ws)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["round"] == 1 and ms["changed"] is None
    assert "first merge round" in ms["changed_note"]


def test_manuscript_panel_never_syncs_even_with_history_present(project, monkeypatch):
    """The panel this guard was built for. A counter, not a raise: `_panel`
    swallows exceptions, so only a call count cannot be hidden."""
    _synced(project)
    calls = []
    monkeypatch.setattr(service.provenance, "sync", lambda *a, **k: calls.append(a))
    assert service.run_overview(project, "r1")["manuscript"]["state"] == "ok"
    assert calls == []


def test_manuscript_panel_never_creates_the_repo(project):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _two_merge_rounds(ws)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["state"] == "no_repo"
    assert not provenance.repo_path(ws).exists(), "reading must not create history"


def test_manuscript_panel_three_no_history_states_are_distinct(project, monkeypatch):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _two_merge_rounds(ws)
    no_repo = service.run_overview(project, "r1")["manuscript"]

    provenance.ensure_repo(ws)                 # a repo with no branches: artifacts, no points
    no_points = service.run_overview(project, "r1")["manuscript"]

    monkeypatch.setattr(provenance.shutil, "which", lambda name: None)
    no_git = service.run_overview(project, "r1")["manuscript"]

    assert (no_git["state"], no_repo["state"], no_points["state"]) == (
        "no_git", "no_repo", "no_points")
    assert len({no_git["reason"], no_repo["reason"], no_points["reason"]}) == 3
    assert "git" in no_git["reason"] and "not installed" in no_git["reason"]
    for ms in (no_repo, no_points):
        assert "after the first merge round or workbench visit" in ms["reason"]
    for ms in (no_git, no_repo, no_points):
        low = ms["reason"].lower()
        assert "create" not in low and "sync" not in low, "must not offer the 0.8s stall"
        assert ms["round"] is None


def test_manuscript_panel_draft_history_without_a_merge_says_so(project):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    provenance.sync(ws)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["state"] == "no_merge" and "no merge round" in ms["reason"]


def test_manuscript_panel_says_when_history_is_behind_the_disk(project):
    ws = _synced(project)
    _touch(ws, "manuscript", "curation", "rounds", "3", "results.tex")   # merged, not yet synced
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["round"] == 2 and ms["behind"] == 3


def test_manuscript_panel_a_failed_compare_degrades_that_line_only(project, monkeypatch):
    _synced(project)

    def boom(ws, a, b):
        raise service.provenance.ProvenanceError("git diff failed")

    monkeypatch.setattr(service.provenance, "diff", boom)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["round"] == 2 and ms["changed"] is None
    assert "could not be compared" in ms["changed_note"]


# --- run_overview (panel 3: who contributed what) --------------------------

def _curate(ws, kept=(), mine=0):
    from scieflow.core.run import curation

    for agent, section in kept:
        curation.keep(ws, "a kept passage", agent=agent, section=section)
    for i in range(mine):
        curation.add_own(ws, f"my own words {i}")


def test_attribution_counts_kept_passages_per_agent_and_section(project):
    ws = project.run_dir("r1")
    _curate(ws, kept=[("kim", "intro"), ("kim", "intro"), ("kim", "results"), ("lee", "methods")])
    at = service.run_overview(project, "r1")["attribution"]
    assert at["reason"] == "" and at["total"] == 4 and at["kept"] == 4
    by = {a["agent"]: a for a in at["agents"]}
    assert by["kim"]["count"] == 3 and by["lee"]["count"] == 1
    assert by["kim"]["sections"] == [{"section": "intro", "count": 2},
                                     {"section": "results", "count": 1}]


def test_attribution_shows_the_researchers_own_blocks_as_unattributed(project):
    """The criterion this panel exists for. Per-agent counts cannot sum to the
    manuscript: `mine` blocks carry no agent and no section. Dropping them
    would understate how much of the paper the researcher wrote."""
    ws = project.run_dir("r1")
    _curate(ws, kept=[("kim", "intro"), ("lee", "methods")], mine=3)
    at = service.run_overview(project, "r1")["attribution"]
    assert at["mine"] == 3, "mine blocks must be counted, not silently dropped"
    assert at["total"] == 5
    assert sum(a["count"] for a in at["agents"]) == 2
    assert sum(a["count"] for a in at["agents"]) + at["mine"] == at["total"]


def test_attribution_a_run_of_only_own_words_is_not_empty(project):
    _curate(project.run_dir("r1"), mine=2)
    at = service.run_overview(project, "r1")["attribution"]
    assert at["reason"] == "" and at["agents"] == [] and at["mine"] == 2 and at["total"] == 2


def test_attribution_no_curation_document_reads_as_nothing_curated(project):
    at = service.run_overview(project, "r1")["attribution"]
    assert "nothing curated yet" in at["reason"]
    assert at["total"] == 0 and at["agents"] == [] and at["mine"] == 0


def test_attribution_keeps_an_agent_that_is_no_longer_in_drafts(project):
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "drafts", "kim", "intro.tex")
    _curate(ws, kept=[("kim", "intro"), ("gone", "intro")])
    at = service.run_overview(project, "r1")["attribution"]
    by = {a["agent"]: a for a in at["agents"]}
    assert by["gone"]["count"] == 1 and by["gone"]["drafted"] is False
    assert by["kim"]["drafted"] is True


def test_attribution_unreadable_curation_is_a_stated_reason(project):
    ws = project.run_dir("r1")
    _touch(ws, "manuscript", "curation", "document.yml", text="{ not: [valid")
    view = service.run_overview(project, "r1")
    assert view["attribution"]["reason"]
    assert view["inventory"]["reason"] == "", "one corrupt source degrades one panel"


# --- run_overview (panel 4: spend and progress over time) ------------------

_T0 = "2026-10-03T10:00:00.000+00:00"


def _at(seconds, base="2026-10-03T10:00:00"):
    """A UTC stamp `seconds` after 10:00:00, millisecond precision like the log."""
    from datetime import datetime, timedelta, timezone

    t = datetime.fromisoformat(base).replace(tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return t.isoformat(timespec="milliseconds")


_seq = iter(range(10**6))


def _ev(ws, type_, ts, event_id=None, **data):
    """Append one event with an explicit `ts` (emit() stamps now())."""
    import json

    event = {"id": event_id or f"e{next(_seq)}", "ts": ts, "type": type_,
             "actor": "agent", "data": data}
    with (ws / "events.jsonl").open("a") as f:
        f.write(json.dumps(event) + "\n")


def _progress(project):
    return service.run_overview(project, "r1")["progress"]


def _fresh(project):
    ws = project.run_dir("r1")
    (ws / "events.jsonl").unlink(missing_ok=True)       # the fixture seeds a few events
    return ws


def test_progress_adds_no_event_type():
    from scieflow.core import events

    assert events.TYPES == frozenset({
        "run.created", "run.resumed",
        "phase.pending", "phase.started", "phase.done", "phase.failed",
        "iteration.advanced", "checkpoint", "budget.recorded",
        "job.queued", "job.started", "job.finished", "job.failed", "job.timeout",
        "job.cancelled", "job.lost", "job.refused", "sandbox.disabled",
        "gate.opened", "gate.answered", "gate.withdrawn",
        "charter.set", "charter.reverted",
        "turn.sent", "turn.received", "turn.session_lost",
        "curation.changed", "curation.round",
        "provenance.synced", "provenance.skipped",
        "integration.call", "sync.pushed", "sync.pulled",
        # Added by the draft-review contract (scite M0), not by this panel.
        "review.assigned", "review.pass_done", "review.evidence_built",
        "review.matrix_ready", "review.decided", "review.blocker",
        "scite.quota_low", "scite.unavailable",
        # Added by M-B (data protection), additive.
        "data.declared", "data.level_resolved", "data.warning",
        "data.released", "data.imported", "data.ingested",
        # Added by M-C1 (literature kernel), additive.
        "network.set", "literature.snapshot",
        # Added by M-C2 t7 (monitor), additive.
        "literature.monitor",
        # Added by M-C2 t5 (identity blockers), additive.
        "review.identity_checked", "review.identity_overruled",
    }), "the vocabulary is closed by design; this panel needs no new type"


def test_progress_attributes_spend_to_the_phase_it_was_recorded_in(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(0), phase="search", iteration=1)
    _ev(ws, "budget.recorded", _at(10), experiment_runs=2)
    _ev(ws, "budget.recorded", _at(20), experiment_runs=3, wall_minutes=5)
    _ev(ws, "phase.done", _at(90), phase="search", iteration=1)
    _ev(ws, "phase.started", _at(100), phase="draft", iteration=1)
    _ev(ws, "budget.recorded", _at(110), experiment_runs=1)
    _ev(ws, "phase.done", _at(400), phase="draft", iteration=1)
    _ev(ws, "budget.recorded", _at(500), iterations=1)         # between phases
    pr = _progress(project)
    rows = {r["phase"]: r for r in pr["phases"]}
    assert rows["search"]["spend"] == {"experiment_runs": 5, "wall_minutes": 5}
    assert rows["search"]["seconds"] == 90 and rows["draft"]["seconds"] == 300
    assert rows["draft"]["spend"] == {"experiment_runs": 1}
    assert pr["outside_phase"] == {"iterations": 1}


def test_progress_a_running_phase_has_no_duration(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(0), phase="search", iteration=1)
    row = _progress(project)["phases"][0]
    assert row["running"] is True and row["seconds"] is None


def test_progress_reports_the_longest_answered_gate(project):
    ws = _fresh(project)
    for gid, opened, answered in (("g1", 0, 30), ("g2", 100, 700), ("g3", 800, 860)):
        _ev(ws, "gate.opened", _at(opened), gate=gid, kind="question")
        _ev(ws, "gate.answered", _at(answered), gate=gid, kind="question")
    longest = _progress(project)["gates"]["longest"]
    assert longest["gate"] == "g2" and longest["seconds"] == 600


def test_progress_a_gate_still_open_is_still_waiting_not_timed_against_now(project):
    ws = _fresh(project)
    _ev(ws, "gate.opened", _at(0), gate="g1", kind="question")
    _ev(ws, "gate.opened", _at(5), gate="g2", kind="question")
    _ev(ws, "gate.answered", _at(65), gate="g2", kind="question")
    gates = _progress(project)["gates"]
    assert gates["longest"]["gate"] == "g2", "an unanswered gate has no duration to rank"
    assert [w["gate"] for w in gates["waiting"]] == ["g1"]
    assert gates["waiting"][0]["since"] == "2026-10-03 10:00:00 UTC"
    assert "seconds" not in gates["waiting"][0]


def test_progress_a_withdrawn_gate_is_not_waiting(project):
    ws = _fresh(project)
    _ev(ws, "gate.opened", _at(0), gate="g1", kind="question")
    _ev(ws, "gate.withdrawn", _at(9), gate="g1")
    assert _progress(project)["gates"]["waiting"] == []


def test_progress_never_yields_a_negative_duration(project):
    ws = _fresh(project)
    _ev(ws, "gate.opened", _at(100), gate="g1", kind="question")
    _ev(ws, "gate.answered", _at(40), gate="g1", kind="question")      # clock stepped back
    _ev(ws, "phase.started", _at(100), phase="p", iteration=1)
    _ev(ws, "phase.done", _at(10), phase="p", iteration=1)
    pr = _progress(project)
    assert pr["gates"]["longest"]["seconds"] == 0
    assert pr["phases"][0]["seconds"] == 0
    assert pr["complete"] is True and any("out of order" in w for w in pr["warnings"])


def test_progress_a_duplicated_event_is_counted_once(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(0), phase="p", iteration=1)
    _ev(ws, "budget.recorded", _at(1), event_id="dup", experiment_runs=4)
    _ev(ws, "budget.recorded", _at(1), event_id="dup", experiment_runs=4)
    _ev(ws, "phase.done", _at(2), phase="p", iteration=1)
    assert _progress(project)["phases"][0]["spend"] == {"experiment_runs": 4}


def test_progress_a_truncated_log_shows_what_it_can_and_says_so(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(0), phase="p", iteration=1)
    _ev(ws, "phase.done", _at(30), phase="p", iteration=1)
    with (ws / "events.jsonl").open("a") as f:
        f.write('{"id": "x", "ts": "2026-10-03T10:00:31.000+00:00", "type": "bud')   # cut off
    pr = _progress(project)
    assert pr["reason"] == "" and pr["phases"][0]["seconds"] == 30
    assert pr["complete"] is False and any("unreadable" in n for n in pr["notes"])


def test_progress_a_gate_answer_with_no_opening_says_the_log_is_incomplete(project):
    ws = _fresh(project)
    _ev(ws, "gate.answered", _at(30), gate="g9", kind="question")
    pr = _progress(project)
    assert pr["gates"]["longest"] is None
    assert pr["complete"] is False and any("gate" in n for n in pr["notes"])


def test_progress_a_phase_marked_done_from_pending_is_not_an_incomplete_log(project):
    """Marking a pending phase done directly is supported (web UI and
    actions.mark_phase), so a done with no started is a healthy log."""
    ws = _fresh(project)
    _ev(ws, "phase.done", _at(30), phase="p", iteration=1)
    pr = _progress(project)
    row = pr["phases"][0]
    assert row["phase"] == "p" and row["seconds"] == 0 and row["running"] is False
    assert pr["complete"] is True and pr["notes"] == [] and pr["warnings"] == []


def test_progress_an_unreadable_line_still_says_incomplete_beside_a_direct_done(project):
    ws = _fresh(project)
    _ev(ws, "phase.done", _at(30), phase="p", iteration=1)
    with (ws / "events.jsonl").open("a") as f:
        f.write("{not json\n")
    pr = _progress(project)
    assert pr["complete"] is False and any("unreadable" in n for n in pr["notes"])


def test_progress_clock_skew_is_a_warning_not_incompleteness(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(100), phase="p", iteration=1)
    _ev(ws, "phase.done", _at(10), phase="p", iteration=1)
    pr = _progress(project)
    assert pr["complete"] is True and pr["notes"] == []
    assert any("out of order" in w for w in pr["warnings"])


def test_manuscript_panel_decodes_git_quoted_file_names(project):
    from scieflow.core import provenance

    ws = project.run_dir("r1")
    odd = 'sec"x'
    for n, text in ((1, "a\n"), (2, "b\n")):
        _touch(ws, "manuscript", "curation", "rounds", str(n), f"{odd}.tex", text=text)
        _touch(ws, "manuscript", "curation", "rounds", str(n), "caf\u00e9.tex", text=text)
    provenance.sync(ws)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["changed"] == ["café.tex", 'sec"x.tex']


def test_header_path_returns_none_for_a_non_octal_escape():
    """`899` is three decimal digits but not octal: int(..., 8) raises."""
    assert service._header_path('"a/x\\899.tex" "b/x\\899.tex"') is None
    assert service._header_path('"a/x\\\u0663\u0663\u0663.tex"') is None


def test_manuscript_says_so_when_a_diff_header_had_to_be_dropped(project, monkeypatch):
    ws = project.run_dir("r1")
    for n in (1, 2):
        _touch(ws, "manuscript", "curation", "rounds", str(n), "intro.tex", text=f"{n}\n")
    service.provenance.sync(ws)
    text = ("diff --git a/sections/intro.tex b/sections/intro.tex\n"
            "diff --git garbage\n")
    monkeypatch.setattr(service.provenance, "diff",
                        lambda *a, **k: {"text": text, "truncated": False})
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["changed"] == ["intro.tex"]
    assert "could not be read" in ms["changed_note"]


def test_header_path_returns_none_for_an_unparseable_header():
    assert service._header_path("garbage") is None
    assert service._header_path('"a/unterminated') is None
def test_progress_an_absent_log_is_a_reason_not_zeros(project):
    ws = _fresh(project)
    pr = _progress(project)
    assert "no event log" in pr["reason"] or "no events" in pr["reason"]
    assert pr["phases"] == []


def test_progress_ignores_a_non_numeric_spend_and_says_so(project):
    ws = _fresh(project)
    _ev(ws, "phase.started", _at(0), phase="p", iteration=1)
    _ev(ws, "budget.recorded", _at(1), experiment_runs="lots", wall_minutes=True, iterations=2)
    pr = _progress(project)
    assert pr["phases"][0]["spend"] == {"iterations": 2}
    assert pr["complete"] is False


def test_progress_counts_checkpoints_and_iterations(project):
    ws = _fresh(project)
    _ev(ws, "checkpoint", _at(1), reason="low-budget")
    _ev(ws, "iteration.advanced", _at(2), iteration=2)
    _ev(ws, "iteration.advanced", _at(3), iteration=3)
    pr = _progress(project)
    assert pr["checkpoints"] == 1 and pr["iterations"] == 2   # advances, not the number


def test_progress_renders_utc_and_never_local_time(project):
    import os
    import time

    ws = _fresh(project)
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Auckland"            # UTC+13 in October: a local rendering would shift
    time.tzset()
    try:
        _ev(ws, "gate.opened", "2026-10-03T23:30:00.000+00:00", gate="g1", kind="question")
        _ev(ws, "gate.opened", "2026-10-04T01:00:00.000+02:00", gate="g2", kind="question")
        _ev(ws, "gate.opened", "2026-10-04T08:00:00", gate="g3", kind="question")   # no zone
        waiting = {w["gate"]: w["since"] for w in _progress(project)["gates"]["waiting"]}
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
    assert waiting["g1"] == "2026-10-03 23:30:00 UTC"
    assert waiting["g2"] == "2026-10-03 23:00:00 UTC"          # converted, labelled UTC
    assert "UTC" not in waiting["g3"] and "not recorded" in waiting["g3"]


@pytest.mark.parametrize("rounds", [2, 20])
def test_manuscript_panel_task2_reuses_history_with_real_comparison(project, monkeypatch, rounds):
    provenance = service.provenance
    ws = project.run_dir("r1")
    for n in range(1, rounds + 1):
        _touch(ws, "manuscript", "curation", "rounds", str(n), "results.tex", text=f"merged {n}\n")
        _touch(ws, "review", f"round-{n}", "review.md", text=f"review {n}\n")
        for agent in ("agy", "claude", "codex", "gemini"):
            _touch(ws, "manuscript", "drafts", agent, "results.tex", text=f"{agent}\n")
            _touch(ws, "review", f"draft-round-{n}", f"response-{agent}.md", text=f"response {n}\n")
    provenance.sync(ws)
    repo = provenance.repo_path(ws)
    real_run = provenance.subprocess.run
    calls = []

    def measured(argv, **kwargs):
        if argv[:3] == ["git", "--git-dir", str(repo)]:
            calls.append(argv[3:])
            assert argv[3] in {"for-each-ref", "ls-tree", "diff"}, "panel reads never write"
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance.subprocess, "run", measured)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["state"] == "ok" and ms["round"] == rounds
    assert ms["present"] == ["results"] and ms["missing"] == []
    assert ms["changed"] == ["results.tex"] and ms["changed_note"] == ""
    assert ms["compared"] == [f"main:merge_{rounds}", f"main:merge_{rounds - 1}"]
    trees = sum(c[0] == "ls-tree" for c in calls)
    print(f"MANUSCRIPT_TASK2 rounds={rounds} branches=5 ls-tree={trees} total-git={len(calls)}")
    assert trees == 5, "comparison must not repeat the history traversal"
    assert len(calls) == 8, "cold observation/listings + fingerprint + actual diff"
    assert sum(c[0] == "diff" for c in calls) == 1


def test_manuscript_panel_task2_long_canonical_round_is_explicitly_delimited(project):
    provenance = service.provenance
    ws = project.run_dir("r1")
    repo = provenance.ensure_repo(ws)
    empty = provenance._tree(repo, [])
    huge = "9" * 5000
    tree = provenance._tree(repo, [("040000", "tree", empty, f"merge_{n}")
                                    for n in ("2", "10", "9" * 4999, huge)])
    commit = provenance._commit(repo, tree, [], "long canonical round")
    provenance._update_ref(repo, "refs/heads/main", commit)
    assert provenance.points(ws)[0]["ref"] == f"main:merge_{huge}", "reader accepts the canonical round"
    ms = service._manuscript(ws)
    assert ms["state"] == "no_merge" and ms["round"] is None
    assert "cannot be represented" in ms["reason"] and "round identifier" in ms["reason"]
    assert ms["present"] == [] and ms["changed"] is None and ms["compared"] == []


@pytest.mark.parametrize("failure", ["refs", "listing", "comparison"])
def test_manuscript_panel_task2_comparison_failures_preserve_round_summary(project, monkeypatch, failure):
    ws = _synced(project)
    provenance = service.provenance
    repo = provenance.repo_path(ws)
    real_tips = provenance._read_branch_tips
    real_bytes = provenance._git_bytes
    observations = 0

    def tips(path):
        nonlocal observations
        observations += 1
        if observations == 2:
            if failure == "refs":
                (repo / "HEAD").write_text("broken HEAD\n")
            if failure == "listing":
                main = provenance._ref_sha(repo, "refs/heads/main")
                assert main is not None
                provenance._update_ref(repo, "refs/heads/draft/new", main)
        return real_tips(path)

    def binary(path, *args):
        if observations == 2 and failure == "listing" and args[0] == "ls-tree":
            raise provenance.ProvenanceError("listing failed after panel points")
        return real_bytes(path, *args)

    real_run = provenance.subprocess.run
    comparisons = []

    def compare(argv, **kwargs):
        if argv[:4] == ["git", "--git-dir", str(repo), "diff"]:
            comparisons.append(argv[4:])
            if failure == "comparison":
                (repo / "HEAD").write_text("broken HEAD\n")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(provenance, "_read_branch_tips", tips)
    monkeypatch.setattr(provenance, "_git_bytes", binary)
    monkeypatch.setattr(provenance.subprocess, "run", compare)
    ms = service.run_overview(project, "r1")["manuscript"]
    assert ms["state"] == "ok" and ms["round"] == 2 and ms["reason"] == ""
    assert ms["present"] == ["intro", "results"] and ms["missing"] == ["methods"]
    assert ms["drafted"] == ["intro", "methods", "results"]
    assert ms["changed"] is None and "could not be compared" in ms["changed_note"]
    assert ms["compared"] == ["main:merge_2", "main:merge_1"]
    assert observations == 2 and len(comparisons) == (1 if failure == "comparison" else 0)
    assert provenance._read_memo.get() is None


# --- K target-only producer; selection/launch integration belongs later ---
@pytest.mark.parametrize('change,reason', [({}, ''), ({'enabled': False}, 'disabled'),
    ({'session_cmd': ''}, 'no session_cmd'), ({'resume_cmd': ''}, 'no resume_cmd'),
    ({'family': 'unregistered'}, 'no family')])
def test_k_target_validation_is_snapshot_only(monkeypatch, change, reason):
    from scieflow.core import agent_run, sandbox
    from scieflow.core.run import charter

    def forbidden(*args, **kwargs):
        raise AssertionError('target validation read external state')

    monkeypatch.setattr(service.config, 'load_agents', forbidden)
    monkeypatch.setattr(agent_run, 'prepare', forbidden)
    monkeypatch.setattr(charter, 'snapshot', forbidden)
    monkeypatch.setattr(sandbox, 'environment', forbidden)
    target = {'enabled': True, 'family': 'claude', 'session_cmd': 'missing first {prompt}',
              'resume_cmd': 'missing resume {session} {prompt}', **change}
    registry = {'broken-old': None, 'target': target}
    if reason:
        with pytest.raises(service.ServiceError, match=reason):
            service._validate_conversation_target('target', registry)
    else:
        assert service._validate_conversation_target('target', registry) is None
    assert registry['target'] == target


@pytest.mark.parametrize('registry', [None, [], {'target': None}, {'target': []}])
def test_k_target_bad_snapshot_controlled(registry):
    with pytest.raises(service.ServiceError):
        service._validate_conversation_target('target', registry)


@pytest.mark.parametrize('bad', [{'family': []}, {'family': {}}, {'session_cmd': ['mutable']},
    {'resume_cmd': {'mutable': True}}, {'enabled': 'false'}])
def test_k_target_bad_scalar_shape_controlled(bad):
    registry = {'target': {'enabled': True, 'family': 'claude',
                           'session_cmd': 'first {prompt}',
                           'resume_cmd': 'resume {session} {prompt}', **bad}}
    with pytest.raises(service.ServiceError):
        service._validate_conversation_target('target', registry)


def test_k_target_wrapper_loads_once(project, monkeypatch):
    calls = []
    def load(root):
        calls.append(root)
        return {'target': {'enabled': True, 'family': 'claude',
                           'session_cmd': 'missing first', 'resume_cmd': 'missing resume'}}
    monkeypatch.setattr(service.config, 'load_agents', load)
    service._check_can_converse(project, 'target')
    assert calls == [project.root]
    with pytest.raises(service.ServiceError, match='unknown agent: absent'):
        service._validate_conversation_target('absent', load(project.root))


@pytest.mark.parametrize('family', [['mutable'], {'mutable': True}])
def test_k_target_nontext_family_refuses_controlled(family):
    registry = {'target': {'enabled': True, 'family': family,
                           'session_cmd': 'first {prompt}',
                           'resume_cmd': 'resume {session} {prompt}'}}
    with pytest.raises(service.ServiceError):
        service._validate_conversation_target('target', registry)


# Task3 behavioral race: same nodes run before and after basic admission.
@pytest.mark.parametrize('boundary', ['before-job', 'before-final-record'])
@pytest.mark.parametrize('contender', ['send', 'handover'])
def test_k_t3_basic_entry_refuses_through_final_record(k_conversational_project, monkeypatch,
                                                      boundary, contender):
    import threading
    from scieflow.core.run import conversation

    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    reached, release = threading.Event(), threading.Event()
    results, errors = [], []
    real_dispatch, real_record = service._launch_prepared_turn, conversation.record_session

    def barrier():
        if threading.current_thread() is owner:
            reached.set()
            assert release.wait(15), 'bounded final-record barrier expired'

    def dispatch(*args, **kwargs):
        if boundary == 'before-job':
            barrier()
        job = real_dispatch(*args, **kwargs)
        result = service.asdict(job)
        assert result['state'] == 'done' and result['exit_code'] == 0
        return job

    def record(*args, **kwargs):
        if boundary == 'before-final-record':
            assert not service._turn_in_flight(project, ws), 'inert process has really exited'
            barrier()
        return real_record(*args, **kwargs)

    def run():
        try:
            results.append(service.say(project, 'r1', 'owner message'))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(service, '_launch_prepared_turn', dispatch)
    monkeypatch.setattr(conversation, 'record_session', record)
    owner = threading.Thread(target=run)
    owner.start()
    try:
        assert reached.wait(15), errors
        before = (ws / 'conversation.yml').read_bytes()
        with pytest.raises(service.ServiceError, match='busy|still running'):
            if contender == 'send':
                service.say(project, 'r1', 'contender message')
            else:
                service.set_conversation_agent(project, 'r1', 'stub2')
        assert (ws / 'conversation.yml').read_bytes() == before
    finally:
        release.set()
        owner.join(20)
    assert not owner.is_alive() and not errors
    assert results[0]['job']['exit_code'] == 0
    doc = conversation.read(ws)
    assert doc['session'] == 'k-session-1'
    assert doc['turns'][-1]['text'] == 'K protocol reply'


@pytest.mark.parametrize('operation', ['send', 'handover'])
def test_k_t3_busy_precedes_state_and_semantic_reads(project, monkeypatch, operation):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.run import conversation
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'stub')
    before = (ws / 'conversation.yml').read_bytes()
    with turn_admission.reserve(project, ws):
        def forbidden(*args, **kwargs):
            raise AssertionError('busy entry read state or semantic configuration')
        monkeypatch.setattr(conversation, 'read', forbidden)
        monkeypatch.setattr(service.config, 'load_agents', forbidden)
        monkeypatch.setattr(sandbox, 'resolve_turn_protection', forbidden)
        with pytest.raises(service.ServiceError, match='busy'):
            if operation == 'send':
                service.say(project, 'r1', 'refused')
            else:
                service.set_conversation_agent(project, 'r1', 'stub2')
    assert (ws / 'conversation.yml').read_bytes() == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


@pytest.mark.parametrize('operation', ['send', 'handover'])
@pytest.mark.parametrize('policy', ['unsandboxed_runs:\n  - slug: r1\n    reason: deliberate\n',
                                  'writable:\n  - path: .scieflow\n'])
def test_k_t3_supported_unsafe_never_legacy_fallback(project, monkeypatch, operation, policy, grant_credentials):
    from scieflow.core.run import conversation
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'stub')
    before = (ws / 'conversation.yml').read_bytes()
    (project.root / 'config/sandbox.yml').write_text(policy)
    grant_credentials(project.root, 'stub')     # S4 (A9): the refusal under test is the grant, not the login
    def forbidden(*args, **kwargs):
        raise AssertionError('unsafe entry reached old dispatch or selection write')
    monkeypatch.setattr(service, '_launch_prepared_turn', forbidden)
    monkeypatch.setattr(conversation, 'set_agent', forbidden)
    with pytest.raises(service.ServiceError, match='unconfined|writable grant'):
        if operation == 'send':
            service.say(project, 'r1', 'refused')
        else:
            service.set_conversation_agent(project, 'r1', 'stub2')
    assert (ws / 'conversation.yml').read_bytes() == before
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


def test_k_t3_unsupported_ordinary_explicit_legacy_compatibility(k_conversational_project, monkeypatch):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError('unsupported ordinary used admission/protection guarantee')
    monkeypatch.setattr(turn_admission, 'reserve', forbidden)
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', forbidden)
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    result = service.say(project, 'r1', 'ordinary compatibility')
    assert result['job']['state'] == 'done' and result['job']['exit_code'] == 0
    assert conversation.read(ws)['session'] == 'k-session-1'
    assert not (project.root / '.scieflow').exists()


@pytest.mark.parametrize('old', ['', 'removed-manager', 'stub_disabled', 'sleepy'])
def test_k_t3_nonlaunch_target_only_one_capture_and_no_charter(k_conversational_project, monkeypatch, old):
    from scieflow.core import agent_run, harness_commands, sandbox, turn_admission
    from scieflow.core.run import charter, conversation
    project, ws, _ = k_conversational_project
    if old:
        conversation.set_agent(ws, old)
    (ws / 'charter.yml').write_text('malformed: [\n')
    calls, captured, validations = [], [], []
    load = service.config.load_agents
    capture = sandbox.resolve_turn_protection
    validate = turn_admission.Reservation.validate_protection
    def registry(root):
        calls.append(root)
        return load(root)
    def protection(*args, **kwargs):
        result = capture(*args, **kwargs)
        captured.append(result)
        return result
    def check(self, value):
        validations.append(value)
        return validate(self, value)
    def forbidden(*args, **kwargs):
        raise AssertionError('nonlaunch used full send/charter/capability/spend')
    monkeypatch.setattr(service.config, 'load_agents', registry)
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', protection)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', check)
    for obj, name in ((agent_run, 'resolve_turn'), (agent_run, 'prepare_turn'),
                      (agent_run, 'prepare'), (charter, 'snapshot'),
                      (harness_commands, 'support_for'), (sandbox, 'verify'),
                      (service.actions, 'record_spend')):
        monkeypatch.setattr(obj, name, forbidden)
    selected = service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    assert selected['agent'] == 'k-protocol-fixture'
    assert len(calls) == len(captured) == 1
    assert len(validations) == 2 and all(p is captured[0] for p in validations)
    assert not list((ws / 'logs').glob('turn-*')) and not (ws / 'jobs').exists()


@pytest.mark.parametrize('change', ['registry', 'policy', 'lock', 'ancestor'])
def test_k_t3_selection_mutation_barrier_zero_write(project, monkeypatch, change):
    import threading
    from scieflow.core import sandbox
    from scieflow.core.run import conversation
    ws = project.run_dir('r1')
    conversation.set_agent(ws, 'stub')
    before = (ws / 'conversation.yml').read_bytes()
    reached, release = threading.Event(), threading.Event()
    errors, reads, policies = [], [], []
    validate = service._validate_conversation_target
    load = service.config.load_agents
    capture = sandbox.resolve_turn_protection
    def target(*args):
        validate(*args)
        reached.set()
        assert release.wait(15)
    def registry(root):
        reads.append(root)
        return load(root)
    def protection(*args, **kwargs):
        policies.append(True)
        return capture(*args, **kwargs)
    def run():
        try:
            service.set_conversation_agent(project, 'r1', 'stub2')
        except service.ServiceError as exc:
            errors.append(exc)
    monkeypatch.setattr(service, '_validate_conversation_target', target)
    monkeypatch.setattr(service.config, 'load_agents', registry)
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', protection)
    thread = threading.Thread(target=run)
    thread.start()
    backup = None
    try:
        assert reached.wait(15)
        if change == 'registry':
            path = project.root / 'config/agents.yml'
            path.write_text(path.read_text() + '# mutation\n')
        elif change == 'policy':
            (project.root / 'config/sandbox.yml').write_text('writable: []\n')
        elif change == 'lock':
            [path] = list((project.root / '.scieflow/turn-admission').glob('*.lock'))
            path.unlink()
            path.touch(mode=0o600)
        else:
            path = project.root / '.scieflow'
            backup = path.with_name('.scieflow-moved')
            path.rename(backup)
            path.mkdir()
    finally:
        release.set()
        thread.join(20)
        if backup is not None:
            path.rmdir()
            backup.rename(path)
    assert not thread.is_alive() and len(errors) == 1
    assert len(reads) == len(policies) == 1
    assert (ws / 'conversation.yml').read_bytes() == before


def test_k_t3_basic_send_independent_protection_and_legacy_prepare_once(k_conversational_project, monkeypatch):
    from scieflow.core import agent_run, sandbox, turn_admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    captured, validations, preparations = [], [], []
    capture, validate, prepare = sandbox.resolve_turn_protection, turn_admission.Reservation.validate_protection, agent_run.prepare_turn
    resolutions = []
    real_resolve = agent_run.resolve_turn
    def protection(*args, **kwargs):
        result = capture(*args, **kwargs)
        captured.append(result)
        return result
    def check(self, value):
        validations.append(value)
        return validate(self, value)
    def legacy(*args, **kwargs):
        result = prepare(*args, **kwargs)
        preparations.append(result)
        return result
    def forbidden(*args, **kwargs):
        raise AssertionError('supported send must not legacy-prepare or dispatch')
    def resolve(*args, **kwargs):
        value = real_resolve(*args, **kwargs)
        resolutions.append(value)
        return value
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', protection)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', check)
    monkeypatch.setattr(agent_run, 'prepare_turn', legacy)
    monkeypatch.setattr(agent_run, 'resolve_turn', resolve)
    monkeypatch.setattr(agent_run, 'prepare', forbidden)
    monkeypatch.setattr(service, 'dispatch_agent', forbidden)
    result = service.say(project, 'r1', 'ordinary')
    assert result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
    assert len(captured) == len(preparations) == 1
    assert len(validations) == 4 and all(p is captured[0] for p in validations)


def test_k_t3_exception_releases_basic_entry(k_conversational_project, monkeypatch):
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    real = service._launch_prepared_turn
    def fail(*args, **kwargs):
        raise service.ServiceError('injected dispatch failure')
    monkeypatch.setattr(service, '_launch_prepared_turn', fail)
    with pytest.raises(service.ServiceError, match='injected'):
        service.say(project, 'r1', 'failed attempt')
    monkeypatch.setattr(service, '_launch_prepared_turn', real)
    assert service.say(project, 'r1', 'next explicit attempt')['job']['exit_code'] == 0


# C1-C3: admission owns the canonical destination, not the request spelling.
def _k_t3_run_identity_pair(k_conversational_project):
    from scieflow.core.run import conversation, status

    project, r1, _ = k_conversational_project
    conversation.set_agent(r1, 'k-protocol-fixture')
    r2 = project.run_dir('r2')
    (r2 / 'logs').mkdir(parents=True)
    (r2 / 'config.yml').write_text('slug: r2\napproval: autonomous\n')
    status.write_status(r2, status.new_status('r2', 'autonomous'))
    conversation.set_agent(r2, 'stub')
    (r2 / 'logs/kept-transcript.out.md').write_text('destination transcript sentinel')
    (r2 / 'artifacts').mkdir()
    (r2 / 'artifacts/kept.bin').write_bytes(b'\x00destination artifact\xff')
    alias = project.workspace_root / 'view'
    alias.symlink_to('r1', target_is_directory=True)
    return project, r1, r2, alias


def _k_t3_run_identity_inventory(ws):
    # Include conversation, logs, transcripts, artifacts, and newly created
    # entries. Reads may advance atime; that is not an artifact mutation.
    import stat

    inventory = {}
    for path in sorted(ws.rglob('*')):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            value = ('link', path.readlink().as_posix())
        elif stat.S_ISDIR(info.st_mode):
            value = ('directory',)
        else:
            assert stat.S_ISREG(info.st_mode)
            value = ('file', path.read_bytes())
        inventory[path.relative_to(ws).as_posix()] = (info.st_mode, value)
    return inventory


def _k_t3_run_identity_both_busy(project, r1, r2):
    from scieflow.core import turn_admission

    for ws in (r1, r2):
        with pytest.raises(turn_admission.AdmissionBusy):
            with turn_admission.reserve(project, ws):
                raise AssertionError('independently reserved run was admitted twice')


def _k_t3_run_identity_retarget(project, alias, protection):
    import shlex
    import subprocess
    from scieflow.core import sandbox

    assert sandbox.available(), 'this regression requires the real supported sandbox'
    sentinel = project.workspace_root / 'run-identity-child-positive'
    script = (f'printf coordinator > {shlex.quote(str(sentinel))} && '
              f'rm {shlex.quote(str(alias))} && '
              f'ln -s r2 {shlex.quote(str(alias))}')
    child = subprocess.run(
        sandbox.wrap(['/bin/sh', '-c', script],
                     writable=list(protection.coordinator_writable),
                     cwd=project.workspace_root, mask=list(protection.masks)),
        capture_output=True, text=True, timeout=20, check=False)
    assert child.returncode == 0, child.stderr
    assert sentinel.read_bytes() == b'coordinator'
    assert alias.resolve() == project.run_dir('r2')
    return child.returncode


@pytest.mark.parametrize('retarget', [False, True], ids=['stable-alias', 'sandbox-retarget'])
def test_k_t3_run_identity_final_record_keeps_reserved_destination(
        k_conversational_project, monkeypatch, retarget):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.run import conversation

    project, r1, r2, alias = _k_t3_run_identity_pair(k_conversational_project)
    before_r2 = _k_t3_run_identity_inventory(r2)
    captured, capture_paths, completed, dispatch_paths, record_paths, busy_paths = [], [], [], [], [], []
    real_capture = sandbox.resolve_turn_protection
    real_dispatch = service._launch_prepared_turn
    real_record = conversation.record_session
    real_busy = service._turn_in_flight
    child_exits = []

    def capture(*args, **kwargs):
        capture_paths.append(kwargs['run_dir'])
        value = real_capture(*args, **kwargs)
        captured.append(value)
        return value

    def busy(project, ws):
        busy_paths.append(ws)
        return real_busy(project, ws)

    def dispatch(project, prepared, *, artifacts, turn_audit):
        dispatch_paths.append((artifacts.raw, artifacts.transcript))
        job = real_dispatch(project, prepared, artifacts=artifacts, turn_audit=turn_audit)
        value = service.asdict(job)
        assert value['state'] == 'done' and value['exit_code'] == 0
        completed.append(value)
        return job

    def record(ws, session_id):
        assert len(completed) == len(captured) == 1 and session_id == 'k-session-1'
        assert not real_busy(project, r1), 'actual inert process must have exited'
        _k_t3_run_identity_both_busy(project, r1, r2)
        if retarget:
            child_exits.append(_k_t3_run_identity_retarget(project, alias, captured[0]))
        _k_t3_run_identity_both_busy(project, r1, r2)
        record_paths.append(ws)  # Never require the vulnerable lexical spelling.
        return real_record(ws, session_id)

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(service, '_turn_in_flight', busy)
    monkeypatch.setattr(service, '_launch_prepared_turn', dispatch)
    monkeypatch.setattr(conversation, 'record_session', record)
    with turn_admission.reserve(project, r2):
        try:
            result = service.say(project, 'view', 'canonical destination inert turn')
            unchanged = _k_t3_run_identity_inventory(r2) == before_r2
            print('T3_RUN_IDENTITY_FINAL', {'retarget': retarget, 'r2_inventory_unchanged': unchanged,
                  'child_exits': child_exits, 'real_job_exit': completed[0]['exit_code'],
                  'r1_session': conversation.read(r1)['session'],
                  'r2_session': conversation.read(r2)['session'], 'both_busy_at_boundary': True})
            assert unchanged, 'final recording changed independently reserved r2 through the request alias'
            assert result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
            doc = conversation.read(r1)
            assert doc['session'] == 'k-session-1' and doc['turns'][-1]['text'] == 'K protocol reply'
            assert [p.resolve() for p in capture_paths] == [p.resolve() for p in record_paths] == [r1]
            assert [p.resolve() for p in busy_paths] == [r1]
            [(prompt, transcript)] = dispatch_paths
            assert prompt.parent.resolve() == transcript.parent.resolve() == r1 / service.TURN_PROMPT_DIR
            assert transcript.read_text() == 'K protocol reply'
            assert child_exits == ([0] if retarget else [])
        finally:
            alias.unlink()
            alias.symlink_to('r1', target_is_directory=True)
    # Both ownership domains cleanly release after real success.
    with turn_admission.reserve(project, r1), turn_admission.reserve(project, r2):
        pass


def test_k_t3_run_identity_postdispatch_drift_refuses_before_parse(
        k_conversational_project, monkeypatch):
    from scieflow.core import sandbox, turn_admission
    from scieflow.core.run import conversation

    project, r1, r2, alias = _k_t3_run_identity_pair(k_conversational_project)
    before_r2 = _k_t3_run_identity_inventory(r2)
    captured, validations, completed, parses, records, errors = [], [], [], [], [], []
    real_capture = sandbox.resolve_turn_protection
    real_validate = turn_admission.Reservation.validate_protection
    real_dispatch = service._launch_prepared_turn
    real_parse = service.sessions.parse
    real_record = conversation.record_session

    def capture(*args, **kwargs):
        value = real_capture(*args, **kwargs)
        captured.append(value)
        return value

    def validate(self, protection):
        validations.append(protection)
        return real_validate(self, protection)

    def dispatch(*args, **kwargs):
        job = real_dispatch(*args, **kwargs)
        value = service.asdict(job)
        assert value['state'] == 'done' and value['exit_code'] == 0
        completed.append(value)
        assert len(captured) == 1
        _k_t3_run_identity_both_busy(project, r1, r2)
        assert _k_t3_run_identity_retarget(project, alias, captured[0]) == 0
        _k_t3_run_identity_both_busy(project, r1, r2)
        return job

    def parse(*args, **kwargs):
        parses.append(True)
        return real_parse(*args, **kwargs)

    def record(*args, **kwargs):
        records.append(True)
        return real_record(*args, **kwargs)

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', validate)
    monkeypatch.setattr(service, '_launch_prepared_turn', dispatch)
    monkeypatch.setattr(service.sessions, 'parse', parse)
    monkeypatch.setattr(conversation, 'record_session', record)
    with turn_admission.reserve(project, r2):
        try:
            try:
                service.say(project, 'view', 'attempt remains recorded on identity drift')
            except (service.ServiceError, OSError) as exc:
                errors.append(exc)
            print('T3_RUN_IDENTITY_POSTDISPATCH', {'error_types': [type(e).__name__ for e in errors],
                  'dispatches': len(completed), 'captures': len(captured), 'validations': len(validations),
                  'parses': len(parses), 'records': len(records),
                  'r2_inventory_unchanged': _k_t3_run_identity_inventory(r2) == before_r2})
            assert _k_t3_run_identity_inventory(r2) == before_r2
            assert len(completed) == len(captured) == 1
            assert len(errors) == 1 and isinstance(errors[0], service.ServiceError), \
                'completion identity drift must visibly refuse, not follow the alias or fail on a missing transcript'
            assert 'identity changed' in str(errors[0])
            assert len(validations) == 4 and all(p is captured[0] for p in validations)
            assert not parses and not records
            doc = conversation.read(r1)
            assert doc['session'] is None and len(doc['turns']) == 1
            assert doc['turns'][0]['role'] == 'human'
            assert completed[0]['exit_code'] == 0  # real attempted job retained, never retried
        finally:
            alias.unlink()
            alias.symlink_to('r1', target_is_directory=True)
    with turn_admission.reserve(project, r1), turn_admission.reserve(project, r2):
        pass


@pytest.mark.parametrize('retarget', [False, True], ids=['stable-alias', 'sandbox-retarget'])
def test_k_t3_run_identity_handover_keeps_reserved_destination(
        k_conversational_project, monkeypatch, retarget):
    from scieflow.core import agent_run, harness_commands, sandbox, turn_admission
    from scieflow.core.run import charter, conversation

    project, r1, r2, alias = _k_t3_run_identity_pair(k_conversational_project)
    before_r2 = _k_t3_run_identity_inventory(r2)
    captured, validations, registries, capture_paths, write_paths, busy_paths = [], [], [], [], [], []
    real_capture = sandbox.resolve_turn_protection
    real_validate = turn_admission.Reservation.validate_protection
    real_load = service.config.load_agents
    real_set = conversation.set_agent
    real_busy = service._turn_in_flight
    child_exits = []

    def capture(*args, **kwargs):
        capture_paths.append(kwargs['run_dir'])
        value = real_capture(*args, **kwargs)
        captured.append(value)
        return value

    def validate(self, protection):
        validations.append(protection)
        return real_validate(self, protection)

    def registry(root):
        registries.append(root)
        return real_load(root)

    def busy(project, ws):
        busy_paths.append(ws)
        return real_busy(project, ws)

    def set_agent(ws, agent, actor='human'):
        assert len(captured) == len(registries) == 1
        _k_t3_run_identity_both_busy(project, r1, r2)
        if retarget:
            child_exits.append(_k_t3_run_identity_retarget(project, alias, captured[0]))
        _k_t3_run_identity_both_busy(project, r1, r2)
        write_paths.append(ws)
        return real_set(ws, agent, actor)

    def forbidden(*args, **kwargs):
        raise AssertionError('nonlaunch destination binding used a full send/probe/spend')

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', validate)
    monkeypatch.setattr(service.config, 'load_agents', registry)
    monkeypatch.setattr(service, '_turn_in_flight', busy)
    monkeypatch.setattr(conversation, 'set_agent', set_agent)
    for obj, name in ((agent_run, 'resolve_turn'), (agent_run, 'prepare_turn'),
                      (agent_run, 'prepare'), (charter, 'snapshot'),
                      (harness_commands, 'support_for'), (sandbox, 'verify'),
                      (service, 'dispatch_agent'), (service.actions, 'record_spend')):
        monkeypatch.setattr(obj, name, forbidden)
    with turn_admission.reserve(project, r2):
        try:
            selected = service.set_conversation_agent(project, 'view', 'stub2')
            unchanged = _k_t3_run_identity_inventory(r2) == before_r2
            print('T3_RUN_IDENTITY_HANDOVER', {'retarget': retarget, 'r2_inventory_unchanged': unchanged,
                  'r1_agent': conversation.read(r1)['agent'], 'child_exits': child_exits,
                  'captures': len(captured), 'registry_loads': len(registries),
                  'validations': len(validations), 'both_busy_at_boundary': True})
            assert unchanged, 'hand-over changed independently reserved r2 through the request alias'
            assert selected['agent'] == conversation.read(r1)['agent'] == 'stub2'
            assert [p.resolve() for p in capture_paths] == [p.resolve() for p in write_paths] == [r1]
            assert [p.resolve() for p in busy_paths] == [r1]
            assert len(validations) == 2 and all(p is captured[0] for p in validations)
            assert child_exits == ([0] if retarget else [])
            assert not list((r1 / service.TURN_PROMPT_DIR).glob('turn-*')) and not (r1 / 'jobs').exists()
        finally:
            alias.unlink()
            alias.symlink_to('r1', target_is_directory=True)
    with turn_admission.reserve(project, r1), turn_admission.reserve(project, r2):
        pass


# M1-M4: send-only host preparation, immutable capture, actual masked launch.
@pytest.mark.parametrize('present', [False, True], ids=['first-absent', 'existing-provenance'])
def test_k_t3_mask_prepared_before_real_first_and_resumed_dispatch(
        k_conversational_project, monkeypatch, present):
    import os
    import shlex
    import subprocess
    from scieflow.core import agent_run, provenance, sandbox, turn_admission
    from scieflow.core.run import conversation

    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    leaf = ws / provenance.REPO_DIR
    if present:
        leaf.mkdir(mode=0o750)
        (leaf / 'host-content').write_bytes(b'provenance sentinel')
    initial_inventory = _k_t3_run_identity_inventory(leaf) if present else {}
    captured, validations, preparations, masks, jobs_seen = [], [], [], [], []
    real_capture = sandbox.resolve_turn_protection
    real_validate = turn_admission.Reservation.validate_protection
    real_prepare = turn_admission.Reservation.prepare_launch_masks
    real_legacy = agent_run.prepare_turn
    real_dispatch = service._launch_prepared_turn

    def capture(*args, **kwargs):
        value = real_capture(*args, **kwargs)
        captured.append(value)
        return value

    def validate(self, value):
        validations.append(value)
        return real_validate(self, value)

    def prepare(self, value):
        assert value is captured[-1]
        absence = next(f for f in value.path_facts if f.path == leaf)
        original = value.path_facts
        assert absence.exists is (present or len(captured) == 2)
        real_prepare(self, value)
        assert value.path_facts is original
        assert absence.exists is (present or len(captured) == 2)
        assert leaf.is_dir()
        masks.append((leaf.lstat().st_ino, self._launch_mask.fd if self._launch_mask else None))

    def legacy(*args, **kwargs):
        preparations.append(True)
        return real_legacy(*args, **kwargs)

    def dispatch(*args, **kwargs):
        assert len(masks) == len(captured) and leaf.stat().st_ino == masks[-1][0]
        value = captured[-1]
        positive = ws / 'mask-child-positive'
        script = (f'printf permitted > {shlex.quote(str(positive))} && '
                  f'printf discarded > {shlex.quote(str(leaf / "discarded-child-write"))}')
        child = subprocess.run(sandbox.wrap(['/bin/sh', '-c', script],
                               writable=list(value.writable), cwd=ws, mask=list(value.masks)),
                               capture_output=True, text=True, timeout=20, check=False)
        assert child.returncode == 0, child.stderr
        assert positive.read_bytes() == b'permitted' and not (leaf / 'discarded-child-write').exists()
        assert leaf.stat().st_ino == masks[-1][0]
        job = real_dispatch(*args, **kwargs)
        result = service.asdict(job)
        assert result['exit_code'] == 0 and leaf.stat().st_ino == masks[-1][0]
        assert _k_t3_run_identity_inventory(leaf) == initial_inventory
        jobs_seen.append(result)
        return job

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(turn_admission.Reservation, 'validate_protection', validate)
    monkeypatch.setattr(turn_admission.Reservation, 'prepare_launch_masks', prepare)
    monkeypatch.setattr(agent_run, 'prepare_turn', legacy)
    monkeypatch.setattr(service, '_launch_prepared_turn', dispatch)
    for text in ('first explicit turn', 'resumed explicit turn'):
        result = service.say(project, 'r1', text)
        assert result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
        assert conversation.read(ws)['session'] == 'k-session-1'
    assert len(captured) == len(preparations) == len(masks) == len(jobs_seen) == 2
    assert len(validations) == 8
    assert all(p is captured[0] for p in validations[:4])
    assert all(p is captured[1] for p in validations[4:])
    assert masks[0][0] == masks[1][0]
    for _, fd in masks:
        if fd is not None:
            with pytest.raises(OSError):
                os.fstat(fd)
    print('T3_MASK_REAL', {'present': present, 'job_exits': [j['exit_code'] for j in jobs_seen],
          'captures': len(captured), 'legacy_prepares': len(preparations), 'validations': len(validations),
          'same_nofollow_inode': True, 'masked_write_discarded': True, 'provenance_unchanged': True})


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
def test_k_t3_mask_creation_race_prevents_dispatch(k_conversational_project, monkeypatch, kind):
    import os
    from scieflow.core import provenance, turn_admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    leaf = ws / provenance.REPO_DIR
    outside = project.root / 'outside-mask'
    outside.mkdir()
    (outside / 'sentinel').write_bytes(b'outside')
    real_mkdir = os.mkdir
    real_publish = turn_admission._publish_mask_noreplace
    real_prepare = turn_admission.Reservation.prepare_launch_masks
    raced, dispatches, entered = [], [], []
    def publish(src_fd, src_name, dst_fd, dst_name):
        if dst_name == leaf.name:
            if kind == 'directory':
                real_mkdir(dst_name, 0o700, dir_fd=dst_fd)
            else:
                os.symlink(str(outside), dst_name, dir_fd=dst_fd)
            raced.append(leaf.lstat().st_ino)
        return real_publish(src_fd, src_name, dst_fd, dst_name)
    def forbidden(*args, **kwargs):
        dispatches.append(True)
        raise AssertionError('mask preparation failure dispatched a model')
    def preparation(self, protection):
        # Inject AFTER supported admission at the real exclusive publication.
        entered.append(protection)
        with monkeypatch.context() as patch:
            patch.setattr(turn_admission, '_publish_mask_noreplace', publish)
            return real_prepare(self, protection)
    monkeypatch.setattr(turn_admission.Reservation, 'prepare_launch_masks', preparation)
    monkeypatch.setattr(service, '_launch_prepared_turn', forbidden)
    with pytest.raises(service.ServiceError):
        service.say(project, 'r1', 'record the human attempt, never dispatch the conflict')
    assert len(entered) == 1 and raced == [leaf.lstat().st_ino] and not dispatches
    doc = conversation.read(ws)
    assert doc['session'] is None and [t['role'] for t in doc['turns']] == ['human']
    assert not (ws / 'jobs').exists()
    assert (outside / 'sentinel').read_bytes() == b'outside'
    with turn_admission.reserve(project, ws):
        pass


@pytest.mark.parametrize('reason', ['nonlaunch', 'no-agent', 'preflight', 'running', 'busy', 'unsafe', 'unsupported'])
def test_k_t3_mask_not_prepared_on_nonlaunch_or_refused_or_unsupported_paths(
        k_conversational_project, monkeypatch, reason):
    from scieflow.core import provenance, sandbox, turn_admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    leaf = ws / provenance.REPO_DIR
    def forbidden(*args, **kwargs):
        raise AssertionError('this path must not perform send-only mask preparation')
    monkeypatch.setattr(turn_admission.Reservation, 'prepare_launch_masks', forbidden)
    if reason == 'nonlaunch':
        service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
        with pytest.raises(service.ServiceError, match='no job'):
            service.cancel_job(project, 'no-such-job')
    elif reason == 'no-agent':
        with pytest.raises(service.ServiceError, match='choose an agent'):
            service.say(project, 'r1', 'no selected manager')
    else:
        conversation.set_agent(ws, 'k-protocol-fixture')
        if reason == 'preflight':
            (ws / 'config.yml').write_text('agent_overrides:\n  k-protocol-fixture:\n    family: unregistered\n')
        elif reason == 'running':
            monkeypatch.setattr(service, '_turn_in_flight', lambda *args: True)
        elif reason == 'unsafe':
            (project.root / sandbox.ALLOWLIST_FILE).write_text('writable:\n  - path: .scieflow\n')
        elif reason == 'unsupported':
            monkeypatch.setattr(turn_admission, 'backend_supported', lambda: False)
            assert service.say(project, 'r1', 'legacy compatibility')['job']['exit_code'] == 0
            assert leaf.exists()  # ordinary sandbox side effect, NOT new preparation
            return
        if reason == 'busy':
            with turn_admission.reserve(project, ws):
                with pytest.raises(service.ServiceError, match='busy'):
                    service.say(project, 'r1', 'held elsewhere')
        else:
            with pytest.raises(service.ServiceError):
                service.say(project, 'r1', 'refused before send-only preparation')
    assert not leaf.exists() and not leaf.is_symlink()
    assert not list((ws / service.TURN_PROMPT_DIR).glob('turn-*'))


# P1-P4: preserve behavior across old exposed creation/new protected publication.
@pytest.mark.parametrize('boundary', ['stable', 'protected-stage', 'destination-conflict', 'after-publication'])
def test_k_t3_mask_publication_confined_boundaries(k_conversational_project, monkeypatch, boundary):
    import os
    import shlex
    import subprocess
    from scieflow.core import agent_run, sandbox, turn_admission as admission
    from scieflow.core.run import conversation

    assert admission.backend_supported() and sandbox.available()
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    leaf = ws / 'provenance.git'
    backup = ws / 'original-published-mask'
    positive = project.workspace_root / 'publication-child-positive'
    protected = project.root / 'config/publication-readonly'
    protected.write_bytes(b'protected')
    captured, validations, dispatches, legacy_calls, opened = [], [], [], [], []
    observed = {}
    real_capture = sandbox.resolve_turn_protection
    real_validate = admission.Reservation.validate_protection
    real_prepare = admission.Reservation.prepare_launch_masks
    real_dispatch = service._launch_prepared_turn
    real_legacy = agent_run.prepare_turn
    real_mkdir, real_open, real_stat = os.mkdir, os.open, os.stat
    real_publish = getattr(admission, '_publish_mask_noreplace', None)
    before = set(os.listdir('/proc/self/fd'))

    def capture(*args, **kwargs):
        value = real_capture(*args, **kwargs)
        captured.append(value)
        return value

    def validate(self, value):
        validations.append(value)
        return real_validate(self, value)

    def legacy(*args, **kwargs):
        legacy_calls.append(True)
        return real_legacy(*args, **kwargs)

    def child(protection, action):
        grants = list(protection.coordinator_writable)
        masks = sandbox.masks_for(grants, run_dir=ws)
        assert grants == [project.workspace_root] and masks == []
        script = (f'printf granted > {shlex.quote(str(positive))} && '
                  f'if (printf forbidden > {shlex.quote(str(protected))}) 2>/dev/null; '
                  f'then exit 41; fi; ' + action)
        result = subprocess.run(sandbox.wrap(['/bin/sh', '-c', script], writable=grants,
                                cwd=project.workspace_root, mask=masks),
                                capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        assert positive.read_bytes() == b'granted' and protected.read_bytes() == b'protected'
        observed.update(child_exit=0, protected_write_denied=True)

    def substitute(protection):
        assert 'replacement_inode' not in observed
        child(protection, f'mv -- {shlex.quote(str(leaf))} {shlex.quote(str(backup))} && '
              f'mkdir -m 700 -- {shlex.quote(str(leaf))} && '
              f'printf stranger > {shlex.quote(str(leaf / "stranger"))}')
        observed['replacement_inode'] = leaf.lstat().st_ino
        assert backup.lstat().st_ino == observed['created_inode']
        assert observed['replacement_inode'] != observed['created_inode']

    def preparation(self, protection):
        parent = self._fds[ws]
        neutral_path = self._lock_path.parent
        neutral = self._fds[neutral_path]
        neutral_identity = neutral_path.lstat().st_ino
        sentinel = neutral_path / 'protected-parent-sentinel'
        if not sentinel.exists():
            sentinel.write_bytes(b'neutral protected')

        def mkdir(name, mode=0o777, *, dir_fd=None):
            result = real_mkdir(name, mode, dir_fd=dir_fd)
            if dir_fd == neutral and str(name).startswith('mask-v1-'):
                stage = neutral_path / name
                observed.update(stage_name=name, created_inode=stage.lstat().st_ino)
                if boundary in ('protected-stage', 'destination-conflict'):
                    # Reached immediately after mkdir, before production stat/open.
                    action = (f'if mv -- {shlex.quote(str(stage))} {shlex.quote(str(stage)+".moved")} 2>/dev/null; '
                              f'then exit 42; fi; '
                              f'if mkdir -- {shlex.quote(str(stage / "forbidden-child"))} 2>/dev/null; then exit 43; fi; '
                              f'if mv -- {shlex.quote(str(neutral_path))} {shlex.quote(str(neutral_path)+".moved")} 2>/dev/null; '
                              f'then exit 44; fi; '
                              f'if (printf forbidden > {shlex.quote(str(sentinel))}) 2>/dev/null; then exit 45; fi; ')
                    if boundary == 'destination-conflict':
                        action += f'mkdir -m 700 -- {shlex.quote(str(leaf))} && printf stranger > {shlex.quote(str(leaf / "stranger"))}'
                    else:
                        action += 'true'
                    child(protection, action)
                    assert stage.lstat().st_ino == observed['created_inode']
                    assert neutral_path.lstat().st_ino == neutral_identity
                    assert sentinel.read_bytes() == b'neutral protected' and not (stage / 'forbidden-child').exists()
                    observed['stage_attempts_reached_and_denied'] = True
            elif dir_fd == parent and name == leaf.name and real_publish is None:
                observed['created_inode'] = real_stat(name, dir_fd=parent, follow_symlinks=False).st_ino
                if boundary == 'after-publication':
                    substitute(protection)
            return result

        def opening(name, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(name, flags, mode, dir_fd=dir_fd)
            if dir_fd in (neutral, parent) and (name == leaf.name or str(name).startswith('mask-v1-')):
                opened.append(fd)
            return fd

        def publish(src_fd, src_name, dst_fd, dst_name):
            assert src_fd == neutral and dst_fd == parent and dst_name == leaf.name
            pinned = [fd for fd in opened if os.fstat(fd).st_ino == observed['created_inode']]
            assert len(pinned) == 1, 'source must be pinned before publication'
            observed['prepublication_fd_inode'] = os.fstat(pinned[0]).st_ino
            real_publish(src_fd, src_name, dst_fd, dst_name)
            if boundary == 'after-publication':
                substitute(protection)
                assert os.fstat(pinned[0]).st_ino == observed['created_inode']

        with monkeypatch.context() as patch:
            patch.setattr(os, 'mkdir', mkdir)
            patch.setattr(os, 'open', opening)
            if real_publish is not None:
                patch.setattr(admission, '_publish_mask_noreplace', publish)
            real_prepare(self, protection)
        if self._launch_mask is not None:
            assert self._launch_mask.protection is protection and not self._launch_mask.absence.exists
            observed['owned_inode'] = self._launch_mask.identity[1]

    def dispatch(*args, **kwargs):
        dispatches.append(True)
        return real_dispatch(*args, **kwargs)

    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(admission.Reservation, 'validate_protection', validate)
    monkeypatch.setattr(admission.Reservation, 'prepare_launch_masks', preparation)
    monkeypatch.setattr(agent_run, 'prepare_turn', legacy)
    monkeypatch.setattr(service, '_launch_prepared_turn', dispatch)
    result, errors = None, []
    try:
        result = service.say(project, 'r1', 'publication boundary inert first send')
    except service.ServiceError as exc:
        errors.append(str(exc))
    print('T3_PUBLICATION_BOUNDARY', dict(observed, boundary=boundary, dispatches=len(dispatches), refusal=errors))
    assert len(captured) == 1 and all(value is captured[0] for value in validations)
    assert set(os.listdir('/proc/self/fd')) == before
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert protected.read_bytes() == b'protected'
    if boundary in ('destination-conflict', 'after-publication'):
        assert observed['child_exit'] == 0
        assert errors and not dispatches, 'persistent stranger was adopted and dispatched as owned'
        assert result is None and conversation.read(ws)['session'] is None
        assert [turn['role'] for turn in conversation.read(ws)['turns']] == ['human']
        assert not (ws / 'jobs').exists() and (leaf / 'stranger').read_bytes() == b'stranger'
        if boundary == 'after-publication':
            assert backup.lstat().st_ino == observed['created_inode']
        else:
            assert observed['stage_attempts_reached_and_denied']
    else:
        assert not errors and result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
        assert len(validations) == 4 and len(legacy_calls) == 1
        assert observed['owned_inode'] == observed['created_inode']
        if real_publish is not None:
            assert observed['owned_inode'] == observed['prepublication_fd_inode']
        if boundary == 'protected-stage':
            assert observed['stage_attempts_reached_and_denied']
        resumed = service.say(project, 'r1', 'publication boundary inert resume')
        assert resumed['job']['exit_code'] == 0 and resumed['turn']['text'] == 'K protocol reply'
        assert len(captured) == len(legacy_calls) == len(dispatches) == 2 and len(validations) == 8
        assert all(value is captured[1] for value in validations[4:])
        assert leaf.lstat().st_ino == observed['created_inode']
        assert not list(leaf.iterdir()), 'tmpfs/protocol must leave host provenance unchanged'
    with admission.reserve(project, ws):
        pass


@pytest.mark.parametrize('failure', ['missing-symbol', 'abi', 'ENOSYS', 'EINVAL', 'EOPNOTSUPP', 'EXDEV', 'EACCES'])
def test_k_t3_mask_publication_errors_never_dispatch_or_fall_back(k_conversational_project, monkeypatch, failure):
    import ctypes
    import errno
    import os
    from scieflow.core import sandbox, turn_admission as admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    conversation.set_agent(ws, 'k-protocol-fixture')
    assert admission.backend_supported()
    calls, captures = [], []
    before = set(os.listdir('/proc/self/fd'))
    real_capture = sandbox.resolve_turn_protection

    class Function:
        def __call__(self, *args):
            calls.append(args)
            if failure == 'abi':
                raise ctypes.ArgumentError('injected ABI error')
            ctypes.set_errno(getattr(errno, failure))
            return -1

    class Library:
        def __init__(self, *args, **kwargs):
            if failure != 'missing-symbol':
                self.renameat2 = Function()

    def capture(*args, **kwargs):
        value = real_capture(*args, **kwargs)
        captures.append(value)
        return value

    def forbidden(*args, **kwargs):
        pytest.fail('supported publication failure fell back or dispatched')

    monkeypatch.setattr(ctypes, 'CDLL', Library)
    monkeypatch.setattr(sandbox, 'resolve_turn_protection', capture)
    monkeypatch.setattr(service, '_launch_prepared_turn', forbidden)
    with pytest.raises(service.ServiceError, match='prepare owned provenance mask'):
        service.say(project, 'r1', 'keep the attempted human turn, refuse publication')
    assert len(captures) == 1 and len(calls) == (0 if failure == 'missing-symbol' else 1)
    assert not (ws / 'provenance.git').exists() and not (ws / 'jobs').exists()
    assert not list((project.root / '.scieflow/turn-admission').glob('mask-v1-*.stage'))
    assert conversation.read(ws)['session'] is None
    assert [turn['role'] for turn in conversation.read(ws)['turns']] == ['human']
    assert set(os.listdir('/proc/self/fd')) == before
    with admission.reserve(project, ws):
        pass


def test_k_t3_mask_publication_not_required_for_existing_or_nonlaunch(k_conversational_project, monkeypatch):
    from scieflow.core import turn_admission as admission
    from scieflow.core.run import conversation
    project, ws, _ = k_conversational_project
    leaf = ws / 'provenance.git'
    def forbidden(*args, **kwargs):
        pytest.fail('existing/nonlaunch path required no-replace libc support')
    monkeypatch.setattr(admission.ctypes, 'CDLL', forbidden)
    service.set_conversation_agent(project, 'r1', 'k-protocol-fixture')
    assert not leaf.exists()
    leaf.mkdir(mode=0o750)
    (leaf / 'sentinel').write_bytes(b'existing provenance')
    inode = leaf.lstat().st_ino
    result = service.say(project, 'r1', 'ordinary existing mask needs no publication')
    assert result['job']['exit_code'] == 0 and result['turn']['text'] == 'K protocol reply'
    assert conversation.read(ws)['session'] == 'k-session-1'
    assert leaf.lstat().st_ino == inode and (leaf / 'sentinel').read_bytes() == b'existing provenance'
