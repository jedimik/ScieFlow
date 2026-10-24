"""`scieflow run` conversation, curation, merge, cancel and preview commands (M-A T5, A-9).

The web app reaches these through `scieflow.core.service`; the terminal must
reach the same functions with the same guards, not a second implementation.
Test 1-4 use the real stub agents from the existing fixtures; the parity
table test lives in `test_cli_parity.py`.
"""

import io
import json
from types import SimpleNamespace

import click
from click.testing import CliRunner

from scieflow.cli import main
from scieflow.core import jobs, service
from scieflow.core.project import Project
from scieflow.core.run import cli as base_cli
from scieflow.core.run import conversation, curation
from scieflow.core.run import converse_cli
from tests.core.test_merge_round import curated, responder  # noqa: F401  (fixtures)


def _invoke(*args, env=None):
    return CliRunner().invoke(main, ["run", *args], env=env, catch_exceptions=False)


def _no_dispatch(monkeypatch):
    monkeypatch.delenv("SCIEFLOW_DISPATCH", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)


def _as_project(monkeypatch, project):
    monkeypatch.setattr(Project, "discover", lambda *a, **k: project)
    monkeypatch.chdir(project.root)  # the service resolves the run from the working directory too


# --- say / conversation ------------------------------------------------------------------


def test_say_writes_the_human_and_agent_turns_and_prints_the_reply(project, responder, monkeypatch):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    ws = project.run_dir("r1")
    service.set_conversation_agent(project, "r1", responder)

    result = _invoke("say", "r1", "hello there")

    assert result.exit_code == 0, result.output
    assert "merged okay" in result.output
    turns = conversation.read(ws)["turns"]
    assert [t["role"] for t in turns] == ["human", "agent"]
    assert turns[0]["text"] == "hello there"
    job = jobs.list_jobs(project, ws)[-1]
    assert job.kind == "turn" and job.state == "done"


def test_say_is_refused_inside_a_dispatch_and_writes_nothing(project, responder, monkeypatch):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    ws = project.run_dir("r1")
    service.set_conversation_agent(project, "r1", responder)
    before = conversation.read(ws)

    result = CliRunner().invoke(main, ["run", "say", "r1", "hello"],
                                env={"SCIEFLOW_DISPATCH": "1"})

    assert result.exit_code == 1, result.output
    assert "dispatch" in result.output.lower()
    assert conversation.read(ws) == before
    assert jobs.list_jobs(project, ws) == []


def test_say_needs_exactly_one_of_message_and_file(project, monkeypatch, tmp_path):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    note = tmp_path / "msg.txt"
    note.write_text("from a file")

    neither = CliRunner().invoke(main, ["run", "say", "r1"])
    both = CliRunner().invoke(main, ["run", "say", "r1", "text", "--file", str(note)])

    assert neither.exit_code == 2
    assert both.exit_code == 2


# --- merge-round ---------------------------------------------------------------------------


def test_merge_round_advances_only_when_the_turn_succeeds(curated, project, responder, monkeypatch):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    ws = curated
    start_round = curation.read(ws)["round"]

    # `stub` cannot merge (it needs a literal `kind: conversation` line): the turn
    # fails, so the round must not move.
    failed = _invoke("merge-round", "r1")
    assert failed.exit_code == 0, failed.output
    assert curation.read(ws)["round"] == start_round

    conversation.set_agent(ws, responder)
    done = _invoke("merge-round", "r1")
    assert done.exit_code == 0, done.output
    assert curation.read(ws)["round"] == start_round + 1


# --- curation -------------------------------------------------------------------------------


def test_curation_round_trip_through_the_terminal_matches_the_service_history(project, monkeypatch):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    ws = project.run_dir("r1")

    assert _invoke("curation", "add", "r1", "my own words").exit_code == 0
    version_after_add = curation.read(ws)["version"]
    block = curation.read(ws)["blocks"][-1]["id"]
    assert _invoke("curation", "edit", "r1", block, "edited words").exit_code == 0
    assert _invoke("curation", "note", "r1", "keep it short").exit_code == 0
    assert _invoke("curation", "move", "r1", block, "0").exit_code == 0
    assert _invoke("curation", "remove", "r1", block).exit_code == 0
    assert _invoke("curation", "revert", "r1", str(version_after_add)).exit_code == 0

    texts = [b["text"] for b in curation.read(ws)["blocks"]]
    assert texts == ["my own words"]

    history = _invoke("curation", "history", "r1", "--json")
    assert history.exit_code == 0
    assert json.loads(history.output) == service.curation_history(project, "r1")


# --- cancel ----------------------------------------------------------------------------------


def test_cancel_refuses_a_job_that_belongs_to_another_run(project, monkeypatch):
    _no_dispatch(monkeypatch)
    _as_project(monkeypatch, project)
    other = project.run_dir("r2")
    monkeypatch.setattr(jobs, "find", lambda proj, job_id: SimpleNamespace(
        id=job_id, run_dir=str(other)))
    called = []
    monkeypatch.setattr(service, "cancel_job", lambda *a, **k: called.append(a) or {})

    result = _invoke("cancel", "r1", "job-9")

    assert result.exit_code == 1, result.output
    assert "does not belong to run r1" in result.output
    assert called == []


# --- registration and collisions ------------------------------------------------------------


def test_run_help_lists_the_existing_and_the_new_commands():
    result = _invoke("--help")
    assert result.exit_code == 0
    for name in ("list", "charter", "events", "say", "curation", "manuscript", "converse-agent"):
        assert name in result.output, name


def test_new_names_do_not_collide_with_the_existing_run_group():
    assert set(converse_cli.new_commands()).isdisjoint(base_cli.run.commands)


def test_the_existing_run_group_is_not_mutated():
    before = dict(base_cli.run.commands)
    converse_cli.build_run_group(base_cli.run, converse_cli.new_commands(), err=io.StringIO())
    assert dict(base_cli.run.commands) == before


def test_a_colliding_name_keeps_the_existing_command_and_warns(monkeypatch):
    base = click.Group("run")

    @click.command("say")
    def existing():
        """the existing one"""

    base.add_command(existing, "say")
    fake = click.Command("say")
    err = io.StringIO()

    group = converse_cli.build_run_group(base, {"say": fake}, err=err)

    assert group.commands["say"] is existing
    assert "say" in err.getvalue()
