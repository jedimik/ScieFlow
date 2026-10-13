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


@research.group()
def scite():
    """scite: readiness and the review evidence bundle (coordinator only)."""


@scite.command("status")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def scite_status_cmd(as_json):
    """Is scite configured, is the SDK installed, is the token exposed to dispatches,
    which harness configs register a scite MCP server (names only)."""
    import json

    from scieflow.core.project import Project
    from scieflow.core.workspace import echo_scite
    from scieflow.integrations.scite import doctor

    report = doctor.report(Project.discover().root)
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    echo_scite(report)
    for r in report["harness_registrations"]:
        click.echo(f"  registered: {r['harness']} {r['server']} ({r['file']})")


def _evidence(slug: str, round_: int, plan_only: bool, as_json: bool) -> None:
    from scieflow.core.review.cli import evidence_cmd

    args = [slug, "--round", str(round_)] + (["--plan"] if plan_only else []) \
        + (["--json"] if as_json else [])
    evidence_cmd.main(args, standalone_mode=False)


@scite.command("plan")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--json", "as_json", is_flag=True, help="Print the plan as JSON.")
def scite_plan_cmd(slug, round_, as_json):
    """Same as `scieflow review evidence SLUG --round N --plan`: sends nothing."""
    _evidence(slug, round_, True, as_json)


@scite.command("run")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--json", "as_json", is_flag=True, help="Print the outcome as JSON.")
def scite_run_cmd(slug, round_, as_json):
    """Same as `scieflow review evidence SLUG --round N`."""
    _evidence(slug, round_, False, as_json)
