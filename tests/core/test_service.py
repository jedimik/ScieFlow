import sys
import time
from pathlib import Path

import pytest

from scieflow.core import service
from scieflow.core.project import Project
from scieflow.core.run import status

ROOT = Path(__file__).resolve().parents[2]
STUB = f"{sys.executable} -m scieflow.core.stub_agent {{prompt}}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agents.yml").write_text(
        f'agents:\n  stub: {{cmd: "{STUB}", enabled: true, timeout_min: 1, family: claude, '
        f'session_cmd: "{STUB}", resume_cmd: "{STUB} {{session}}"}}\n'
        '  sleepy: {cmd: "sleep 300", enabled: true, timeout_min: 5}\n')
    (tmp_path / "config" / "defaults.yml").write_text("approval: per-campaign\n")
    (tmp_path / "schemas").mkdir()
    for name in ("status", "gates", "status-research"):
        (tmp_path / "schemas" / f"{name}.yml").write_text(
            (ROOT / "schemas" / f"{name}.yml").read_text())
    ws = tmp_path / "workspace" / "r1"
    (ws / "logs").mkdir(parents=True)
    # A real run always carries config.yml beside status.yml (run/init.py).
    (ws / "config.yml").write_text("slug: r1\napproval: autonomous\n")
    status.write_status(ws, status.new_status("r1", "autonomous"))
    return Project(tmp_path)


@pytest.fixture
def running_turn(project):
    """A turn already in flight for r1: a still-running agent job, so the
    busy check in `service.say` has something to refuse against. Uses the
    `sleepy` agent's `sleep 300` command, same as
    `test_dispatch_detached_then_cancel`, and cancels it on teardown."""
    from scieflow.core.run import conversation

    ws = project.run_dir("r1")
    conversation.set_agent(ws, "stub")
    prompt = ws / "logs" / "running.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "running.out.md",
                                 detach=True)
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
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert job["state"] == "done" and out.exists()
    assert (ws / "logs" / "t.md").exists()
    assert service.run_detail(project, "r1")["jobs"][0]["id"] == job["id"]


def test_dispatch_detached_then_cancel(project):
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("hi")
    job = service.dispatch_agent(project, "sleepy", prompt, ws / "logs" / "t.md", detach=True)
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
    service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert seen["sandbox_writable"] is not None
    assert ws in seen["sandbox_writable"]


def test_service_dispatch_refuses_without_a_sandbox(project, monkeypatch):
    from scieflow.core import sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError, match="bubblewrap"):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")


def test_service_refusal_leaves_a_job_refused_event_on_the_timeline(project, monkeypatch):
    """agent_run.main() emits job.refused on this path; the service must too,
    or a refusal from the web app leaves nothing on the run's history."""
    from scieflow.core import events, sandbox

    monkeypatch.setattr(sandbox, "available", lambda: False)
    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text("go\n")
    with pytest.raises(service.ServiceError):
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
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
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
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
        service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
    assert not out.exists()


def test_normal_sandboxed_dispatch_leaves_neither_event(project):
    """The new emissions must not fire on the happy path."""
    from scieflow.core import events

    ws = project.run_dir("r1")
    prompt = ws / "logs" / "p.md"
    prompt.write_text(f"output: {ws / 'out.md'}\nkind: hypothesis\n")
    job = service.dispatch_agent(project, "stub", prompt, ws / "logs" / "t.md")
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


def test_say_pins_the_charter_into_the_turn(project):
    """The whole point of the charter is that every turn carries it. A turn
    that composed its own prompt would bypass that silently."""
    from scieflow.core.run import charter, conversation

    ws = project.run_dir("r1")
    charter.set_text(ws, "Goal: characterise the catalyst.")
    conversation.set_agent(ws, "stub")
    service.say(project, "r1", "next step?")

    sent = (ws / "logs").glob("turn-*.md")
    composed = "\n".join(p.read_text() for p in sent)
    assert "characterise the catalyst" in composed
    assert composed.index("characterise the catalyst") < composed.index("next step?")


def test_say_refuses_when_no_agent_is_chosen(project):
    with pytest.raises(service.ServiceError, match="agent"):
        service.say(project, "r1", "hello")


def test_say_refuses_an_agent_that_cannot_hold_a_session(project):
    """The spec is explicit: an agent that cannot report a session id must be
    refused plainly, not silently restarted on every turn."""
    from scieflow.core.run import conversation

    conversation.set_agent(project.run_dir("r1"), "sleepy")   # no session_cmd
    with pytest.raises(service.ServiceError, match="conversation"):
        service.say(project, "r1", "hello")


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
