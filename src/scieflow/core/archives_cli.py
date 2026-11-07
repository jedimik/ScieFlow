"""`scieflow workspace archives` and `scieflow workspace archive …`: archived runs from the terminal (B2:5.11, T9-D7).

Each command calls the service function of the same name and prints its result. The service is looked
up inside each command, so loading the CLI does not load it. A refusal (ServiceError, ArchiveRefused)
prints its message and exits 1; a job that failed or was interrupted exits 1.
"""

from __future__ import annotations

import json
import sys

import click


def _project():
    from scieflow.core.project import Project, ProjectError
    try:
        return Project.discover()
    except (ProjectError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc


def _call(name: str, *args, **kwargs) -> dict:
    """Run service.<name>; a service refusal or error becomes a clean one-line exit 1."""
    from scieflow.core import service
    try:
        return getattr(service, name)(*args, **kwargs)
    except service.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc


def _dump(doc: dict) -> None:
    click.echo(json.dumps(doc, indent=2, default=str))


def _human(n) -> str:
    if n is None:
        return "-"
    for unit, name in ((1 << 30, "GiB"), (1 << 20, "MiB"), (1 << 10, "KiB")):
        if n >= unit:
            return f"{n / unit:.1f} {name}"
    return f"{n} bytes"


def _job_result(doc: dict) -> None:
    """Print the outcome sentence of a download, restore or verify; a failed one exits 1."""
    if doc.get("message"):
        click.echo(doc["message"])
    outcome = doc.get("outcome") or ""
    if outcome == "interrupted" or outcome.startswith("failed"):
        sys.exit(1)


@click.command("archives")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def archives_cmd(as_json):
    """List archived runs (their pointers in git). Never contacts the remote."""
    doc = _call("archived_runs", _project())
    if as_json:
        return _dump(doc)
    for row in doc.get("rows", []):
        line = (f"{row.get('slug')}  {row.get('kind') or '-'}  {_human(row.get('size'))}  "
                f"{row.get('state') or '-'}  {row.get('relation') or '-'}")
        if row.get("outcome"):
            line += f"  last: {row['outcome']}"
        elif row.get("reason"):
            line += f"  {row['reason']}"
        if row.get("command"):
            line += f"  ({row['command']})"
        click.echo(line)
    if doc.get("cap_note"):
        click.echo(doc["cap_note"])


@click.group("archive")
def archive():
    """One archived run: show it, browse it, download it, restore it, or cancel its job."""


@archive.command("show")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def show_cmd(slug, as_json):
    """Facts about one archive, read without downloading."""
    doc = _call("archive_detail", _project(), slug)
    if as_json:
        return _dump(doc)
    click.echo(f"{doc.get('slug', slug)}: {doc.get('kind') or '-'}, {_human(doc.get('size'))}, "
               f"md5 {doc.get('md5_12') or '-'}")
    click.echo(f"state: {doc.get('state') or '-'} ({doc.get('relation') or '-'})")
    if doc.get("outcome"):
        click.echo(f"last job: {doc['outcome']}")
    env = doc.get("environment") or {}
    if env:
        click.echo(f"dvc: {env.get('dvc', '-')}; DVC remote configuration in this checkout: "
                   f"{env.get('remote_config', '-')}")
    for group in (doc.get("disk") or {}).get("groups", []):
        click.echo(f"disk {group['path']}: {_human(group['received'])} received, "
                   f"{_human(group['free'])} free, {_human(group['needed'])} needed")
    level = doc.get("data_level")
    if level and level.get("banner"):
        click.echo(level["banner"])
    elif level:
        click.echo(f"data level: {level.get('level')} ({level.get('reason')})")
    if doc.get("browse_refusal"):
        click.echo(f"browse: {doc['browse_refusal']}")
    if doc.get("manuscript"):
        click.echo(doc["manuscript"])
    if doc.get("actions"):
        click.echo("actions: " + ", ".join(doc["actions"]))
    for leftover in doc.get("leftovers") or []:
        click.echo(f"leftover: {leftover}")


@archive.command("ls")
@click.argument("slug")
@click.argument("directory", required=False, default="")
@click.option("--page", default=1, show_default=True, type=int, help="Page of the listing.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def ls_cmd(slug, directory, page, as_json):
    """List one directory inside a downloaded archive, read-only."""
    doc = _call("archive_entries", _project(), slug, directory, page)
    if as_json:
        return _dump(doc)
    if doc.get("reason"):
        raise click.ClickException(f"cannot browse {slug}: {doc['reason']}")
    for entry in doc.get("entries", []):
        suffix = "/" if entry.get("kind") == "dir" else ""
        extra = f" -> {entry['target']}" if entry.get("target") else ""
        click.echo(f"{entry.get('name')}{suffix}  {entry.get('kind')}  {_human(entry.get('size'))}{extra}")
    if doc.get("note"):
        click.echo(doc["note"])


@archive.command("cat")
@click.argument("slug")
@click.argument("member")
@click.option("--out", "out", type=click.Path(dir_okay=False), default=None,
              help="Write the member to this file instead of stdout.")
def cat_cmd(slug, member, out):
    """Print one member of a downloaded archive (raw bytes)."""
    from scieflow.core.archives import ZipRefused
    doc = _call("archive_member", _project(), slug, member, mode="raw")
    if doc.get("reason"):
        raise click.ClickException(f"{member}: {doc['reason']}")
    stream = doc.get("stream")
    if stream is None:
        return
    is_text = (doc.get("media_type") or "").startswith("text/")
    try:
        if out:
            with open(out, "wb") as fh:
                for chunk in stream:
                    fh.write(chunk)
            click.echo(f"wrote {_human(doc.get('size'))} to {out}")
            return
        if sys.stdout.isatty() and not is_text:
            raise click.ClickException("this member is binary: use --out FILE")
        for chunk in stream:
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    except ZipRefused as exc:
        raise click.ClickException(f"{member}: {exc.reason}") from exc


@archive.command("fetch")
@click.argument("slug")
@click.option("--yes", "yes", is_flag=True,
              help="Confirm a download of 1 GiB or more, one of unknown size, or a replace.")
@click.option("--replace", "replace", is_flag=True,
              help="Download again over a local copy that is not the verified one.")
def fetch_cmd(slug, yes, replace):
    """Download and verify one archive zip (never unpacks it)."""
    doc = _call("fetch_archive", _project(), slug, replace=replace, yes=yes, wait=True)
    _job_result(doc)


@archive.command("restore")
@click.argument("slug")
@click.option("--yes", "yes", is_flag=True,
              help="Confirm a restore of 1 GiB or more of unpacked files.")
def restore_cmd(slug, yes):
    """Unpack a verified archive into an absent or empty workspace/<slug>."""
    doc = _call("restore_archive", _project(), slug, yes=yes, wait=True)
    _job_result(doc)


@archive.command("cancel")
@click.argument("slug")
def cancel_cmd(slug):
    """Stop the running download, verification or restore of this archive."""
    doc = _call("cancel_archive_job", _project(), slug)
    click.echo(doc.get("message") or "")
