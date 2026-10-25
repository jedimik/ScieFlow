"""`scieflow run` — the terminal side of a run's conversation, curation, merge rounds,
job cancellation and previews (M-A T5, A-9).

Every command here is thin: it calls exactly one `scieflow.core.service`
function, the same one the web app calls, so admission, budget and sandbox
guards are the existing ones. Writes act as the human and refuse to run inside
a ScieFlow dispatch (the scite dispatch detector is reused for that). Reads take
`--json`.

The group is a new `run` group built from the existing `scieflow.core.run.cli`
group. Existing commands are copied in unchanged, and `core/run/cli.py` itself
is never edited (it is an in-flight file). If a new name already exists there,
the existing command wins and one warning goes to stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from scieflow.core import jobs, service
from scieflow.core.project import Project, ProjectError
from scieflow.core.run import cli as base_cli
from scieflow.integrations.scite.mcp import DispatchRefused, refuse_in_dispatch

JSON = click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")


def _project() -> Project:
    try:
        return Project.discover()
    except ProjectError as exc:
        raise click.ClickException(str(exc)) from exc


def _run_dir(project: Project, slug: str) -> Path:
    try:
        return project.run_dir(slug)
    except ProjectError as exc:
        raise click.ClickException(str(exc)) from exc


def _refuse_in_dispatch() -> None:
    try:
        refuse_in_dispatch()
    except DispatchRefused as exc:
        raise click.ClickException(str(exc)) from exc


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc


def _show(value, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(value, indent=2, default=str))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            plain = item is None or isinstance(item, (str, int, float, bool))
            click.echo(f"{key}: {item if plain else json.dumps(item, default=str)}")
    elif isinstance(value, list):
        for item in value:
            click.echo(json.dumps(item, default=str))
    else:
        click.echo(str(value))


def _text_from(text: str | None, path: Path | None) -> str:
    if (text is None) == (path is None):
        raise click.UsageError("give TEXT or --file PATH, not both or neither")
    if path is not None:
        return path.read_text(encoding="utf-8")
    return "" if text is None else text


FILE_OPTION = click.option("--file", "path", type=click.Path(exists=True, dir_okay=False,
                           path_type=Path), help="Read the text from PATH instead.")


def _owned_job(project: Project, slug: str, job_id: str):
    """A job belongs to a run when its own run directory is that run's."""
    job = jobs.find(project, job_id)
    if job is None or not job.run_dir or Path(job.run_dir).resolve() != _run_dir(project, slug).resolve():
        raise click.ClickException(f"job {job_id} does not belong to run {slug}; nothing cancelled")
    return job


# --- conversation -------------------------------------------------------------------------


@click.command("conversation")
@click.argument("slug")
@JSON
def conversation_cmd(slug, as_json):
    """The run's conversation: agent, turns, and whether a turn is in flight."""
    _show(_call(service.conversation_state, _project(), slug), as_json)


@click.command("converse-agent")
@click.argument("slug")
@click.argument("agent")
@click.option("--reset-session", is_flag=True, help="Start a fresh session instead of resuming.")
@click.option("--expected-revision", type=int, default=None,
              help="Refuse unless the conversation is at this revision.")
def converse_agent_cmd(slug, agent, reset_session, expected_revision):
    """Choose which agent holds this run's conversation."""
    _refuse_in_dispatch()
    _show(_call(service.set_conversation_agent, _project(), slug, agent, "human",
                expected_revision=expected_revision, reset_session=reset_session), True)


@click.command("say")
@click.argument("slug")
@click.argument("message", required=False)
@FILE_OPTION
def say_cmd(slug, message, path):
    """Send one message to the run's conversation agent and print its reply."""
    text = _text_from(message, path)
    _refuse_in_dispatch()
    _show(_call(service.say, _project(), slug, text, "human"), True)


@click.command("merge-round")
@click.argument("slug")
def merge_round_cmd(slug):
    """Send this round's curation to the conversation agent to merge."""
    _refuse_in_dispatch()
    _show(_call(service.merge_round, _project(), slug), True)


# --- start ---------------------------------------------------------------------------------


@click.command("start")
@click.argument("slug")
@click.option("--goal", "goal_file", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="The goal text, from a file.")
@click.option("--agent", default="", help="Agent that takes the first turn.")
@click.option("--workflow", default="", help="Workflow to start from (see `scieflow menu`).")
@click.option("--max-iterations", type=int, default=None)
@click.option("--max-experiment-runs", type=int, default=None)
@click.option("--max-wall-minutes", type=int, default=None)
def start_cmd(slug, goal_file, agent, workflow, max_iterations, max_experiment_runs,
              max_wall_minutes):
    """Create a run and, when an agent is named, have it take the first turn."""
    _refuse_in_dispatch()
    goal = goal_file.read_text(encoding="utf-8")
    _show(_call(service.start_run, _project(), slug, goal, agent, workflow=workflow,
                max_iterations=max_iterations, max_experiment_runs=max_experiment_runs,
                max_wall_minutes=max_wall_minutes), True)


# --- curation ------------------------------------------------------------------------------


@click.group("curation")
def curation_group():
    """The curation document: passages kept from drafts, own text, and versions."""


@curation_group.command("show")
@click.argument("slug")
@JSON
def curation_show(slug, as_json):
    """Every agent's draft, each merged round, and the curation document."""
    _show(_call(service.workbench, _project(), slug), as_json)


@curation_group.command("history")
@click.argument("slug")
@JSON
def curation_history_cmd(slug, as_json):
    """Every curation version, oldest first."""
    _show(_call(service.curation_history, _project(), slug), as_json)


@curation_group.command("add")
@click.argument("slug")
@click.argument("text", required=False)
@FILE_OPTION
def curation_add(slug, text, path):
    """Add your own words to the curation document."""
    _refuse_in_dispatch()
    body = _text_from(text, path)
    _show(_call(service.add_own_text, _project(), slug, body, "human"), True)


@curation_group.command("keep")
@click.argument("slug")
@click.argument("agent")
@click.argument("section")
@click.argument("text", required=False)
@FILE_OPTION
def curation_keep(slug, agent, section, text, path):
    """Keep a passage from AGENT's draft of SECTION, with its provenance."""
    _refuse_in_dispatch()
    body = _text_from(text, path)
    _show(_call(service.keep_passage, _project(), slug, body, agent, section, "human"), True)


@curation_group.command("edit")
@click.argument("slug")
@click.argument("block_id")
@click.argument("text", required=False)
@FILE_OPTION
def curation_edit(slug, block_id, text, path):
    """Replace the text of one block."""
    _refuse_in_dispatch()
    body = _text_from(text, path)
    _show(_call(service.edit_curation_block, _project(), slug, block_id, body, "human"), True)


@curation_group.command("move")
@click.argument("slug")
@click.argument("block_id")
@click.argument("position", type=int)
def curation_move(slug, block_id, position):
    """Move one block to POSITION (0 is the top)."""
    _refuse_in_dispatch()
    _show(_call(service.move_curation_block, _project(), slug, block_id, position, "human"), True)


@curation_group.command("remove")
@click.argument("slug")
@click.argument("block_id")
def curation_remove(slug, block_id):
    """Remove one block."""
    _refuse_in_dispatch()
    _show(_call(service.remove_curation_block, _project(), slug, block_id, "human"), True)


@curation_group.command("note")
@click.argument("slug")
@click.argument("text", required=False)
@FILE_OPTION
def curation_note(slug, text, path):
    """Set the curation note (an empty TEXT clears it)."""
    _refuse_in_dispatch()
    body = _text_from(text, path)
    _show(_call(service.set_curation_note, _project(), slug, body, "human"), True)


@curation_group.command("revert")
@click.argument("slug")
@click.argument("version", type=int)
def curation_revert(slug, version):
    """Make curation version VERSION current again (appends a copy of it)."""
    _refuse_in_dispatch()
    _show(_call(service.revert_curation, _project(), slug, version, "human"), True)


# --- manuscript ----------------------------------------------------------------------------


@click.group("manuscript")
def manuscript_group():
    """The run's manuscript provenance points."""


@manuscript_group.command("history")
@click.argument("slug")
@JSON
def manuscript_history_cmd(slug, as_json):
    """The provenance points, and why there are none when there are none."""
    _show(_call(service.manuscript_history, _project(), slug), as_json)


@manuscript_group.command("diff")
@click.argument("slug")
@click.argument("a")
@click.argument("b")
@JSON
def manuscript_diff_cmd(slug, a, b, as_json):
    """A textual diff between two provenance points A and B (their `ref` strings)."""
    _show(_call(service.manuscript_diff, _project(), slug, a, b), as_json)


# --- jobs and previews ---------------------------------------------------------------------


@click.command("cancel")
@click.argument("slug")
@click.argument("job_id")
def cancel_cmd(slug, job_id):
    """Cancel a job that belongs to this run."""
    _refuse_in_dispatch()
    project = _project()
    _owned_job(project, slug, job_id)
    _show(_call(service.cancel_job, project, job_id), True)


@click.command("preview")
@click.argument("slug")
@click.argument("source")
@click.option("--compile", "compile_", is_flag=True,
              help="Start a compile job for SOURCE (needs latexmk).")
@JSON
def preview_cmd(slug, source, compile_, as_json):
    """A draft's preview state, or with --compile, start compiling it to a PDF."""
    if compile_:
        _refuse_in_dispatch()
        _show(_call(service.compile_preview, _project(), slug, source), True)
        return
    _show(_call(service.preview_of, _project(), slug, source), as_json)


# --- group assembly ------------------------------------------------------------------------


def new_commands() -> dict[str, click.Command]:
    """The commands M-A T5 adds to `scieflow run`, by name."""
    return {
        "conversation": conversation_cmd,
        "converse-agent": converse_agent_cmd,
        "say": say_cmd,
        "start": start_cmd,
        "merge-round": merge_round_cmd,
        "curation": curation_group,
        "manuscript": manuscript_group,
        "cancel": cancel_cmd,
        "preview": preview_cmd,
    }


def build_run_group(base: click.Group, new: dict[str, click.Command], err=None) -> click.Group:
    """A new `run` group holding every command of `base` plus `new`.

    Commands are shared, not copied, and `base` is never changed. A name the base
    already has keeps the base's command; the new one is skipped with a warning.
    """
    stream = err if err is not None else sys.stderr
    group = click.Group(name=base.name, help=base.help)
    for name, command in base.commands.items():
        group.add_command(command, name)
    for name, command in new.items():
        if name in base.commands:
            stream.write(f"scieflow run: '{name}' already exists; the new command is not "
                         "registered\n")
            continue
        group.add_command(command, name)
    return group


run = build_run_group(base_cli.run, new_commands())
