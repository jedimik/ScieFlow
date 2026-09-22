"""`scieflow serve` — run the local web app."""

from __future__ import annotations

import click

from scieflow.core.browser import open_url
from scieflow.core.project import Project
from scieflow.web import auth
from scieflow.web.app import create_app

DEFAULT_PORT = 8765


@click.command("serve")
@click.option("--port", default=DEFAULT_PORT, show_default=True,
              help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Loopback address to bind; anything else is refused.")
@click.option("--no-browser", is_flag=True, help="Print the URL, open nothing.")
def serve(port: int, host: str, no_browser: bool) -> None:
    """Open the local web app: runs, timelines, jobs, gates (loopback only)."""
    import uvicorn

    try:
        host = auth.loopback_only(host)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    project = Project.discover()
    token = auth.new_token()
    app = create_app(project, token)
    url = f"http://{host}:{port}/?token={token}"
    click.echo(f"ScieFlow — {project.root}")
    click.echo(f"Open: {url}")
    click.echo("The token in that URL is this session's key; it becomes a cookie "
               "on first open. Ctrl-C to stop.")
    if not no_browser:
        open_url(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
