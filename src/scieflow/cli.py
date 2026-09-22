"""`scieflow` command line.

Module command groups are imported lazily, so a missing optional extra (for
example `experiments` on a literature-only machine) yields an install hint
instead of an ImportError, and `scieflow --help` always works.
"""

import importlib
import sys

import click

# name -> (module, attribute, optional extra that provides its dependencies, help)
GROUPS = {
    "menu": ("scieflow.core.menu", "menu", None,
             "Interactive menu (also what bare `scieflow` opens)."),
    "agent": ("scieflow.core.cli", "agent", None, "Dispatch headless agents."),
    "gate": ("scieflow.core.gates", "gate", None,
             "Approvals the protocols require, answered from terminal or browser."),
    "run": ("scieflow.core.run.cli", "run", None,
            "A run's state, history and lifecycle."),
    "experiment": (
        "scieflow.experiments.cli",
        "experiment",
        "experiments",
        "Computational experiments: campaigns, sweeps, metrics, reports.",
    ),
    "research": (
        "scieflow.research.cli",
        "research",
        "research",
        "Literature research: search, validation, citations, Zotero.",
    ),
    "news": (
        "scieflow.news.cli",
        "news",
        "news",
        "Track what changed in the tools and topics you follow.",
    ),
    "workspace": (
        "scieflow.core.workspace",
        "workspace",
        None,
        "Research runs under workspace/: list, index, doctor.",
    ),
    "chats": (
        "scieflow.chats.cli",
        "chats",
        "chats",
        "Back up and restore agent chats, skills, and plugins.",
    ),
}


class LazyGroup(click.Group):
    def list_commands(self, ctx):
        return sorted(GROUPS)

    def get_command(self, ctx, name):
        if name not in GROUPS:
            return None
        module, attr, extra, _ = GROUPS[name]
        try:
            loaded = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if extra is None or (exc.name or "").startswith("scieflow"):
                raise
            raise click.ClickException(
                f"'scieflow {name}' needs the '{extra}' extra "
                f"(missing module: {exc.name}). Install it with: uv sync --extra {extra}"
            ) from exc
        return getattr(loaded, attr)

    def format_commands(self, ctx, formatter):
        rows = [(name, GROUPS[name][3]) for name in self.list_commands(ctx)]
        with formatter.section("Commands"):
            formatter.write_dl(rows)


@click.group(cls=LazyGroup, invoke_without_command=True)
@click.version_option(package_name="scieflow")
@click.pass_context
def main(ctx):
    """ScieFlow — agent-driven experiments and literature research.

    Run with no command in a terminal to open the interactive menu.
    """
    if ctx.invoked_subcommand is not None:
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        from scieflow.core.menu import run_menu

        run_menu()
    else:
        click.echo(ctx.get_help())


if __name__ == "__main__":
    main()
