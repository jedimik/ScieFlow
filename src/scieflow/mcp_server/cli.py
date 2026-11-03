"""`scieflow mcp`: the read-only MCP server (M-I, plan 5.6 items 1, 2 and 8).

`serve` runs the stdio server for the discovered project (`--workspace SLUG` limits it to one
run). `tools` lists the tools without starting a server. Both refuse inside a ScieFlow dispatch,
before stdin is read: a sub-agent may not read across runs.
"""

from __future__ import annotations

import json
import sys

import click

from scieflow.core.project import Project, ProjectError
from scieflow.integrations.scite.mcp import in_dispatch
from scieflow.mcp_server import tools as registry
from scieflow.mcp_server.protocol import Server

REFUSAL = "scieflow mcp: refused inside a dispatch (a sub-agent may not read across runs)"


def _refuse_in_dispatch() -> None:
    if in_dispatch():
        click.echo(REFUSAL, err=True)
        sys.exit(1)


def _scope(project: Project, slug: str | None) -> str | None:
    if slug is None:
        return None
    try:
        ws = project.run_dir(slug)
    except ProjectError as exc:
        raise click.ClickException(str(exc)) from exc
    if not ws.is_dir():
        raise click.ClickException(f"no run named {slug!r}")
    return ws.name


@click.group("mcp")
def mcp() -> None:
    """Read-only MCP server for an interactive coordinator (stdio)."""


@mcp.command("serve")
@click.option("--workspace", "slug", default=None, metavar="SLUG",
              help="Serve only this run.")
def serve(slug: str | None) -> None:
    """Serve the read-only tools over stdio until stdin closes."""
    _refuse_in_dispatch()
    project = Project.discover()
    Server(project, scope=_scope(project, slug)).serve(sys.stdin, sys.stdout)


@mcp.command("tools")
@click.option("--json", "as_json", is_flag=True, help="Print the tool list as JSON.")
def list_tools(as_json: bool) -> None:
    """List the MCP tools: names, titles and input schemas."""
    _refuse_in_dispatch()
    described = registry.tools_list()
    if as_json:
        click.echo(json.dumps(described, indent=2, ensure_ascii=False))
        return
    for tool in described:
        click.echo(f"{tool['name']}: {tool['title']}")
