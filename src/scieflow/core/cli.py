"""`scieflow agent` commands."""

import click

_PASSTHROUGH = {"ignore_unknown_options": True, "allow_extra_args": True,
                "help_option_names": []}


@click.group()
def agent():
    """Dispatch headless agents."""


@agent.command("run", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def run(ctx):
    """Run one agent headless: AGENT PROMPT_FILE TRANSCRIPT_FILE [--cwd DIR]."""
    from scieflow.core import agent_run

    agent_run.main(ctx.args)
