from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from scieflow.core import config as config_mod

from .config import EXAMPLE_CONFIG, ChatsConfig, ConfigError, load_config
from .model import TOOLS


def default_config_path() -> Path:
    return config_mod.repo_root() / "config" / "chats.yml"


CONFIG_OPT = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=default_config_path,
    show_default="<repo>/config/chats.yml",
    help="Path to the chats backup config file.",
)
TOOL_OPT = click.option(
    "--tool",
    "tools",
    multiple=True,
    type=click.Choice(TOOLS),
    help="Limit to one store (repeatable).",
)
SINCE_OPT = click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Only chats updated on or after this date.",
)
PROJECT_OPT = click.option(
    "--project",
    "projects",
    multiple=True,
    help="Only chats whose project path contains this string (repeatable).",
)


def _load(config_path: Path) -> ChatsConfig:
    try:
        return load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


@click.group()
def chats():
    """Back up and restore agent chats, skills, and plugins."""


@chats.command()
@CONFIG_OPT
def init(config_path: Path):
    """Scaffold the chats backup config."""
    if config_path.exists():
        raise click.ClickException(f"{config_path} already exists")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(EXAMPLE_CONFIG)
    click.echo(f"wrote {config_path}")


@chats.command()
@CONFIG_OPT
@TOOL_OPT
@SINCE_OPT
@PROJECT_OPT
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def scan(config_path, tools, since, projects, as_json):
    """List every chat found on this machine. Read-only."""
    import json as json_mod

    from .discovery import discover

    config = _load(config_path)
    refs = discover(config, tools=tools, since=since, projects=projects)
    if as_json:
        click.echo(json_mod.dumps([_row(r) for r in refs], indent=2))
        return
    if not refs:
        click.echo("no chats found")
        return
    _print_table(refs)


def _row(ref) -> dict:
    return {
        "tool": ref.tool,
        "chat_id": ref.chat_id,
        "project": ref.project_path,
        "title": ref.title,
        "started": ref.started.isoformat() if ref.started else None,
        "updated": ref.updated.isoformat() if ref.updated else None,
        "messages": ref.message_count,
        "bytes": ref.size_bytes,
    }


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def _recency(ref):
    """Newest first; chats with no timestamp sort last."""
    return ref.updated or datetime.min.replace(tzinfo=timezone.utc)


def _print_table(refs) -> None:
    by_tool: dict[str, list] = {}
    for ref in refs:
        by_tool.setdefault(ref.tool, []).append(ref)
    total = 0
    for tool in sorted(by_tool):
        rows = sorted(by_tool[tool], key=_recency, reverse=True)
        size = sum(r.size_bytes for r in rows)
        total += size
        click.echo(f"\n{tool}  ({len(rows)} chats, {human(size)})")
        for ref in rows:
            date = ref.updated.date().isoformat() if ref.updated else "    ?     "
            count = str(ref.message_count) if ref.message_count else "-"
            click.echo(
                f"  {date}  {ref.project_name[:22]:<22} {count:>6} rec "
                f"{human(ref.size_bytes):>7}  {ref.title[:52]}"
            )
    click.echo(f"\n{len(refs)} chats, {human(total)} total")


# -- backup -------------------------------------------------------------
OUT_OPT = click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Bundle path (default: <bundle_dir>/scieflow-chats-<host>-<stamp>.zip).",
)


@chats.command()
@CONFIG_OPT
@TOOL_OPT
@SINCE_OPT
@PROJECT_OPT
@OUT_OPT
@click.option("--all", "take_all", is_flag=True, help="Take every matching chat, no prompts.")
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Replay a selection written by 'scieflow chats plan'.",
)
@click.option("--with-brain", is_flag=True, help="Include Antigravity brain/ scratch dirs.")
@click.option(
    "--scan-secrets/--no-scan-secrets",
    default=True,
    show_default=True,
    help="Report secret-shaped strings per chat before packing.",
)
@click.option("--no-encrypt", is_flag=True, help="Write a plaintext bundle (needs --yes).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def backup(
    config_path, tools, since, projects, out_path, take_all, plan_path,
    with_brain, scan_secrets, no_encrypt, yes,
):
    """Pick chats, associate their skills and plugins, write one bundle."""
    from . import bundle as bundle_mod
    from . import crypto
    from .detect import detect
    from .discovery import adapters, discover
    from .model import Selection
    from .select import choose_artifacts, choose_chats, human, read_plan

    config = _load(config_path)
    options = {"with_brain": with_brain}
    refs = discover(config, tools=tools, since=since, projects=projects, options=options)
    if not refs:
        raise click.ClickException("no chats matched")

    wanted_artifacts: set[tuple[str, str]] | None = None
    if plan_path is not None and take_all:
        raise click.UsageError("--plan and --all are mutually exclusive")
    if plan_path is not None:
        keys, wanted_artifacts = read_plan(plan_path)
        chosen = [r for r in refs if r.key in keys]
        if not chosen:
            raise click.ClickException(f"{plan_path}: none of its chats are on this machine")
    elif take_all:
        chosen = refs
    else:
        chosen = choose_chats(refs)
    if not chosen:
        raise click.ClickException("nothing selected")

    by_tool = {a.tool: a for a in adapters(config, tools, options)}
    click.echo(f"scanning {len(chosen)} transcript(s) for skills, plugins and secrets…", err=True)
    found, secrets = detect(config, chosen, by_tool, with_secrets=scan_secrets)
    if wanted_artifacts is not None:
        artifacts = [a for a in found if (a.kind, a.name) in wanted_artifacts]
    elif take_all:
        artifacts = found
    else:
        artifacts = choose_artifacts(found)

    if secrets:
        click.echo("\nsecret-shaped strings (counts only — review before sharing):")
        for key, counts in secrets.items():
            detail = ", ".join(f"{k}×{v}" for k, v in counts.items())
            click.echo(f"  {key}  {detail}")

    selection = Selection(chats=list(chosen), artifacts=list(artifacts))
    limit = config.max_bundle_gb * 1024**3
    if selection.size_bytes > limit:
        raise click.ClickException(
            f"selection is {human(selection.size_bytes)}, over max_bundle_gb "
            f"({config.max_bundle_gb}G). Narrow it, or raise the limit in {config_path}."
        )

    method, warning = (None, None)
    if not no_encrypt:
        try:
            method, warning = crypto.pick(config.encryption.method)
        except crypto.CryptoError as e:
            raise click.ClickException(str(e)) from e
        if warning:
            click.echo(f"note: {warning}", err=True)
    elif not yes:
        raise click.UsageError("--no-encrypt also needs --yes: the bundle will be plaintext")

    target = out_path or config.bundle_dir / bundle_mod.bundle_name()
    click.echo(
        f"\n{len(selection.chats)} chats, {len(selection.artifacts)} skills/plugins, "
        f"{human(selection.size_bytes)} -> {target}"
        + ("" if method else "  (UNENCRYPTED)")
    )
    if not yes:
        if not sys.stdin.isatty():
            raise click.ClickException("not written: pass --yes to confirm non-interactively")
        click.confirm("Write this bundle?", abort=True)

    final = _write_bundle(config, selection, secrets, by_tool, target, method)
    click.echo(f"wrote {final}  ({human(final.stat().st_size)})")


def _write_bundle(config, selection, secrets, by_tool, target, method):
    import shutil as shutil_mod
    import tempfile

    import yaml as yaml_mod

    from . import bundle as bundle_mod
    from . import crypto

    stage_dir = Path(tempfile.mkdtemp(prefix="scieflow-chats-stage-"))
    try:
        specs = []
        for tool, adapter in by_tool.items():
            refs = [c for c in selection.chats if c.tool == tool]
            if not refs:
                continue
            specs.extend(adapter.collect(refs))
            specs.extend(adapter.sidecars(refs))
        digests = bundle_mod.stage(specs, stage_dir)
        bundle_mod.stage_artifacts(selection.artifacts, stage_dir)
        manifest = bundle_mod.build_manifest(
            config, selection, digests, secrets, sorted(config.exclude_extra)
        )
        try:
            bundle_mod.validate_manifest(manifest)
        except bundle_mod.BundleError as e:
            raise click.ClickException(str(e)) from e
        (stage_dir / bundle_mod.MANIFEST).write_text(
            yaml_mod.safe_dump(manifest, sort_keys=False, allow_unicode=True)
        )
        try:
            written = bundle_mod.write(stage_dir, target)
        except bundle_mod.BundleError as e:
            raise click.ClickException(str(e)) from e
        if method is None:
            return written
        try:
            return crypto.encrypt(written, method, config.encryption.recipient)
        except crypto.CryptoError as e:
            written.unlink(missing_ok=True)
            raise click.ClickException(str(e)) from e
    finally:
        shutil_mod.rmtree(stage_dir, ignore_errors=True)


@chats.command("plan")
@CONFIG_OPT
@TOOL_OPT
@SINCE_OPT
@PROJECT_OPT
@click.argument("plan_file", type=click.Path(path_type=Path))
def plan_cmd(config_path, tools, since, projects, plan_file):
    """Write an editable selection file for a repeatable backup."""
    from .select import write_plan
    from .discovery import discover

    config = _load(config_path)
    refs = discover(config, tools=tools, since=since, projects=projects)
    if not refs:
        raise click.ClickException("no chats matched")
    write_plan(plan_file, refs, [], selected_keys=set())
    click.echo(
        f"wrote {plan_file} with {len(refs)} chats, all 'selected: false'.\n"
        f"Edit it, then: uv run scieflow chats backup --plan {plan_file}"
    )


# -- inspect / restore --------------------------------------------------
@chats.command()
@click.argument("bundle_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def inspect(bundle_path):
    """Print a bundle's manifest without restoring anything."""
    import shutil as shutil_mod

    from .bundle import BundleError, open_bundle

    try:
        work, manifest = open_bundle(bundle_path)
    except BundleError as e:
        raise click.ClickException(str(e)) from e
    try:
        source = manifest.get("source", {})
        click.echo(f"created  {manifest.get('created')}")
        click.echo(f"source   {source.get('user')}@{source.get('host')}  home={source.get('home')}")
        click.echo(f"tools    {', '.join(manifest.get('tools') or [])}")
        click.echo(f"chats    {len(manifest.get('chats') or [])}")
        for row in manifest.get("chats") or []:
            click.echo(f"  {row['tool']:<7} {str(row.get('updated'))[:10]}  "
                       f"{str(row.get('project'))[-34:]:<34} {str(row.get('title'))[:40]}")
        artifacts = manifest.get("artifacts") or []
        if artifacts:
            click.echo(f"skills/plugins  {len(artifacts)}")
            for row in artifacts:
                mark = "bundled" if row.get("bundled") else "reference"
                click.echo(f"  {row['kind']:<7} {row['name']:<36} {mark}")
        secrets = manifest.get("secret_scan") or {}
        if secrets:
            click.echo(f"secret-shaped strings in {len(secrets)} chat(s) — counts only")
    finally:
        shutil_mod.rmtree(work.parent, ignore_errors=True)


@chats.command("restore")
@CONFIG_OPT
@TOOL_OPT
@click.argument("bundle_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--map", "maps", multiple=True, metavar="OLD=NEW",
              help="Rewrite this path prefix (repeatable).")
@click.option("--apply", "do_apply", is_flag=True, help="Write the changes (default: dry run).")
@click.option("--overwrite", is_flag=True, help="Replace files and rows that already exist.")
@click.option("--diff", "show_diff", is_flag=True, help="Show a unified diff for merged files.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def restore_cmd(config_path, tools, bundle_path, maps, do_apply, overwrite, show_diff, yes):
    """Restore a bundle onto this machine. Dry run unless --apply."""
    import shutil as shutil_mod

    from . import restore as restore_mod
    from .bundle import BundleError, open_bundle

    config = _load(config_path)
    try:
        pairs = [restore_mod.parse_map(m) for m in maps]
    except ValueError as e:
        raise click.BadParameter(str(e)) from e

    try:
        work, manifest = open_bundle(bundle_path)
    except BundleError as e:
        raise click.ClickException(str(e)) from e
    try:
        mapping = restore_mod.default_mapping(manifest, pairs)
        changes = restore_mod.plan(work, manifest, config, mapping, tools)
        if not changes:
            click.echo("nothing to restore")
            return
        if mapping:
            click.echo("path rewrites:")
            for old, new in mapping.items():
                click.echo(f"  {old}  ->  {new}")
        click.echo(f"\n{len(changes)} change(s):")
        for change in changes:
            click.echo(change.render())
            if show_diff and change.action == "merge":
                text = change.diff()
                if text:
                    click.echo("".join(f"      {line}" for line in text.splitlines(True)))
        counts = restore_mod.summarise(changes)
        click.echo("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if not do_apply:
            click.echo("\ndry run — nothing written. Re-run with --apply to write.")
            return
        _refuse_if_running(tuple(tools) or tuple(manifest.get("tools") or ()))
        if not yes:
            if not sys.stdin.isatty():
                raise click.ClickException("not written: pass --yes to confirm non-interactively")
            click.confirm("Apply these changes?", abort=True)
        notes = restore_mod.apply(changes, overwrite=overwrite)
        for note in notes:
            click.echo(f"note: {note}", err=True)
        click.echo("restore complete")
    finally:
        shutil_mod.rmtree(work.parent, ignore_errors=True)


def _refuse_if_running(tools: tuple[str, ...]) -> None:
    """A live CLI holds its SQLite files open; a partial write loses threads."""
    from .stores.base import running_tools

    watched = tuple(t for t in ("claude", "codex", "agy") if not tools or t in tools)
    live = running_tools(watched) if watched else []
    if not live:
        return
    subject = "is" if len(live) == 1 else "are"
    raise click.ClickException(
        f"{', '.join(live)} {subject} still running — close "
        f"{'it' if len(live) == 1 else 'them'} first. Their databases are open, "
        "and a half-written restore loses threads."
    )


# -- optional DVC transport ---------------------------------------------
@chats.command()
@CONFIG_OPT
@click.argument("bundle_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def push(config_path, bundle_path):
    """Upload one encrypted bundle to DVC storage. Bundles only."""
    from .transport import TransportError, push as push_bundle

    config = _load(config_path)
    try:
        pointer, hint = push_bundle(config, bundle_path)
    except TransportError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"uploaded {bundle_path.name}")
    click.echo(f"pointer  {pointer}")
    click.echo(f"next     {hint}")


@chats.command()
@CONFIG_OPT
@click.argument("name", required=False)
@click.option(
    "--to",
    "dest_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to fetch into (default: bundle_dir).",
)
def pull(config_path, name, dest_dir):
    """Fetch a bundle from DVC storage. With no NAME, list what is tracked."""
    from .transport import TransportError, available, pull as pull_bundle

    config = _load(config_path)
    if name is None:
        pointers = available(config)
        if not pointers:
            click.echo(
                "no tracked bundles in this checkout "
                f"({config.remote.dir}). Pull the branch that tracks them."
            )
            return
        for pointer in pointers:
            click.echo(f"  {pointer.name.removesuffix('.dvc')}")
        click.echo("\nFetch one with: uv run scieflow chats pull <name>")
        return
    try:
        fetched = pull_bundle(config, name, dest_dir)
    except TransportError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"fetched {fetched}")
    click.echo(f"next    uv run scieflow chats restore {fetched}")
