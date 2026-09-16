"""`scieflow research` commands.

Each command forwards its arguments unchanged to the module's own argparse
entry point, so `scieflow research <cmd> --help` shows that command's options.
"""

import importlib

import click

_PASSTHROUGH = {"ignore_unknown_options": True, "allow_extra_args": True,
                "help_option_names": []}


def _forward(name: str, module: str, summary: str, group: click.Group) -> None:
    @group.command(name, context_settings=_PASSTHROUGH, add_help_option=False, help=summary)
    @click.pass_context
    def command(ctx):
        importlib.import_module(module).main(ctx.args)


@click.group()
def research():
    """Literature research: search, validation, citations, Zotero."""


@research.group()
def search():
    """Search a literature index; prints normalized paper JSON."""


for _name, _summary in [
    ("openalex", "Search OpenAlex works."),
    ("arxiv", "Search arXiv preprints."),
    ("europepmc", "Search Europe PMC."),
    ("crossref", "Search Crossref works."),
]:
    _forward(_name, f"scieflow.research.search.{_name}", _summary, search)

_forward("validate", "scieflow.research.validate",
         "Validate a JSON artifact against a research schema.", research)
_forward("check-citations", "scieflow.research.citations",
         "Check paper-draft citations against references.bib and searched DOIs.", research)
_forward("zotero-export", "scieflow.research.zotero",
         "Export a run's papers to a Zotero collection.", research)
