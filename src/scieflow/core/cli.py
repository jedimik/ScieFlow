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


def _root():
    from scieflow.core import config

    return config.repo_root()


@agent.command("show")
@click.option("--workspace", "slug", help="Show the effective config of workspace/<SLUG>.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def show(slug, as_json):
    """Effective role assignments and agent settings, with where each comes from."""
    import json

    from scieflow.core import agent_config

    root = _root()
    if slug and not agent_config.workspace_dir(root, slug).is_dir():
        raise click.ClickException(f"no workspace {slug!r} under {root / 'workspace'}")
    eff = agent_config.resolve(root, slug)
    if as_json:
        click.echo(json.dumps(eff.to_json(), indent=2))
    else:
        title = f"Workspace {slug} (unset values inherit the defaults)" if slug else "Defaults"
        click.echo(agent_config.format_table(eff, title))
    if eff.problems:
        raise SystemExit(1)
