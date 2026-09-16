"""`scieflow` command line.

Module command groups are imported lazily, so a missing optional extra (for
example `experiments` on a literature-only machine) yields an install hint
instead of an ImportError, and `scieflow --help` always works.
"""

import importlib

import click

# name -> (module, attribute, optional extra that provides its dependencies, help)
GROUPS = {
    "agent": ("scieflow.core.cli", "agent", None, "Dispatch headless agents."),
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


@click.group(cls=LazyGroup)
@click.version_option(package_name="scieflow")
def main():
    """ScieFlow — agent-driven experiments and literature research."""


if __name__ == "__main__":
    main()
