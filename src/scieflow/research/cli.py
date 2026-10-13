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


@scite.command("backfill")
@click.argument("slug")
@click.option("--round", "round_", type=int, required=True, help="Review round number.")
@click.option("--force", is_flag=True,
              help="Backfill a round that already has a matrix (it must then be rebuilt and "
                   "its decisions made again).")
@click.option("--json", "as_json", is_flag=True, help="Print the outcome as JSON.")
def scite_backfill_cmd(slug, round_, force, as_json):
    """Fill what round N's evidence bundle is missing after a quota stop or an outage.

    Earlier answers are reused (no call); only the skipped requests are sent, within
    the budgets of config/scite.yml. Pass-B reviews written against the old bundle
    should be re-checked afterwards. Exit 3 on a policy / consent / dispatch refusal.
    """
    import json
    import sys

    from scieflow.core.project import Project
    from scieflow.core.review import evidence
    from scieflow.integrations.scite import policy as _policy

    project = Project.discover()
    try:
        outcome = evidence.backfill(project, slug, round_, force=force)
    except _policy.PolicyError as exc:
        click.echo(f"refused: {exc}", err=True)
        sys.exit(_policy.exit_code(exc))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    if outcome.status == "off":
        click.echo(f"scite is off: {outcome.reason} — nothing sent")
        return
    s = outcome.calls["summary"]
    if as_json:
        click.echo(json.dumps({"status": "built", "scite_status": outcome.scite_status,
                               "calls": s}, indent=2, ensure_ascii=False))
        return
    click.echo(f"backfilled: workspace/{slug}/review/round-{round_}/evidence/bundle.json "
               f"(scite {outcome.scite_status}; {s['mcp']} MCP, {s['rest']} REST, "
               f"{s['cached']} cached, {len(s['skipped'])} still skipped)")
    for item in s["skipped"]:
        click.echo(f"  skipped ({item['reason']}): {item['query']}")
    click.echo("next: re-run the matrix check; pass B written against the old bundle may "
               "need a re-run")
