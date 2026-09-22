#!/usr/bin/env python3
"""One-command push and pull of agent chats.

    uv run scripts/chats-push.sh        # pick agents + projects -> bundle -> DVC
    uv run scripts/chats-pull.sh        # pick a bundle -> fetch -> restore

Both start by asking which agents (claude, codex, agy, gemini) and which
projects you mean, with counts and sizes, so the usual case is three key
presses. Everything underneath is the ordinary `scieflow chats` commands —
this only spares you the flags. Pass the same flags here to skip the
questions (`--tool claude --project SegSnake --yes`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import click

from scieflow.chats import bundle as bundle_mod
from scieflow.chats import transport
from scieflow.chats.config import ConfigError, load_config
from scieflow.chats.discovery import discover
from scieflow.core import config as config_mod
from scieflow.core.menu import BACK, UI


def human(size: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def load(args) -> object:
    path = args.config or config_mod.repo_root() / "config" / "chats.yml"
    try:
        return load_config(Path(path))
    except ConfigError as exc:
        sys.exit(f"error: {exc}")


def run_cli(argv: list[str]) -> int:
    """Run a `scieflow` command in-process; returns its exit code."""
    from scieflow.cli import main

    click.echo(click.style(f"$ scieflow {' '.join(argv)}", dim=True))
    try:
        main.main(args=argv, prog_name="scieflow", standalone_mode=False)
    except click.exceptions.Abort:
        click.echo("cancelled")
        return 1
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


# -- selection ---------------------------------------------------------------
def choose_tools(ui: UI, refs, preset: list[str]) -> list[str] | None:
    if preset:
        return preset
    totals: dict[str, tuple[int, int]] = {}
    for ref in refs:
        count, size = totals.get(ref.tool, (0, 0))
        totals[ref.tool] = (count + 1, size + ref.size_bytes)
    choices = [(f"{tool:<7} {count:>4} chats  {human(size):>7}", tool)
               for tool, (count, size) in sorted(totals.items())]
    return ui.checkbox("Which agents? (space toggles, enter confirms)",
                       choices, checked=list(totals))


def choose_projects(ui: UI, refs) -> list[str] | None:
    """Exact project paths the user ticked; [] means 'all of them'."""
    totals: dict[str, tuple[int, int]] = {}
    for ref in refs:
        key = ref.project_path or "(no project)"
        count, size = totals.get(key, (0, 0))
        totals[key] = (count + 1, size + ref.size_bytes)
    if len(totals) <= 1:
        return []
    rows = sorted(totals.items(), key=lambda kv: -kv[1][1])
    choices = [(f"{Path(path).name[:28]:<28} {count:>4} chats  {human(size):>7}  {path}", path)
               for path, (count, size) in rows]
    picked = ui.checkbox("Which projects? (all = leave everything ticked)",
                         choices, checked=[p for p, _ in rows])
    if picked is None:
        return None
    return [] if len(picked) == len(rows) else picked


# -- push --------------------------------------------------------------------
def cmd_push(args) -> int:
    config = load(args)
    ui = UI()
    refs = discover(config, tools=tuple(args.tool), since=args.since,
                    projects=tuple(args.project))
    if not refs:
        sys.exit("no chats matched")

    # `--tool` and `--project` were already applied by discover(); only the
    # interactive picks still have to be narrowed down here.
    tools = choose_tools(ui, refs, args.tool)
    if not tools:
        sys.exit("nothing selected")
    refs = [r for r in refs if r.tool in tools]
    if args.project:
        projects = list(args.project)
    else:
        projects = choose_projects(ui, refs)
        if projects is None:
            sys.exit("cancelled")
        if projects:
            refs = [r for r in refs if r.project_path in projects]
    if not refs:
        sys.exit("nothing selected")

    total = sum(r.size_bytes for r in refs)
    click.echo(f"\n{len(refs)} chats, {human(total)} from "
               f"{', '.join(sorted({r.tool for r in refs}))}")

    out = Path(args.out) if args.out else config.bundle_dir / bundle_mod.bundle_name()
    argv = ["chats", "backup", "--all", "--out", str(out)]
    for tool in tools:
        argv += ["--tool", tool]
    for project in projects:
        argv += ["--project", project]
    if args.since:
        argv += ["--since", args.since]
    if args.no_encrypt:
        argv.append("--no-encrypt")
    if args.yes:
        argv.append("--yes")
    if args.config:
        argv += ["--config", str(args.config)]
    code = run_cli(argv)
    if code != 0:
        return code

    written = out if out.exists() else next(
        (p for p in (out.with_name(out.name + s) for s in (".age", ".gpg")) if p.exists()), None)
    if written is None:
        sys.exit(f"error: no bundle at {out}")
    click.echo(f"\nbundle: {written}  ({human(written.stat().st_size)})")

    if args.no_push:
        return 0
    if not config.remote.enabled:
        click.echo("remote sync is off (remote.enabled in config/chats.yml) — keeping it local")
        return 0
    code = run_cli(["chats", "push", str(written)])
    if code != 0:
        return code
    pointer = transport.staging_dir(config) / (written.name + transport.POINTER_SUFFIX)
    if args.commit and pointer.exists():
        return commit_pointer(pointer, written.name)
    return 0


def commit_pointer(pointer: Path, bundle_name: str) -> int:
    import subprocess

    root = config_mod.repo_root()
    rel = pointer.relative_to(root)
    for cmd in (["git", "add", str(rel)],
                ["git", "commit", "-m", f"chore(chats): track {bundle_name}"]):
        done = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if done.returncode != 0:
            click.echo(done.stderr.strip() or done.stdout.strip(), err=True)
            return done.returncode
    click.echo(f"committed {rel} — push it so another machine can pull the bundle")
    return 0


# -- pull --------------------------------------------------------------------
def cmd_pull(args) -> int:
    config = load(args)
    ui = UI()
    if not config.remote.enabled:
        sys.exit("remote sync is off: set remote.enabled: true in config/chats.yml")

    pointers = transport.available(config)
    if not pointers:
        sys.exit(f"no bundles tracked in {config.remote.dir} — pull the branch that has them")
    if args.bundle:
        matches = [p for p in pointers if args.bundle in p.name]
        if not matches:
            sys.exit(f"no tracked bundle matches {args.bundle!r}")
        pointer = matches[0]
    elif args.latest or len(pointers) == 1:
        pointer = pointers[0]
    else:
        pointer = ui.select("Which bundle?", [(p.name.removesuffix(".dvc"), p) for p in pointers])
        if pointer is BACK:
            return 1
    name = pointer.name.removesuffix(transport.POINTER_SUFFIX)

    target = (Path(args.to) if args.to else config.bundle_dir) / name
    if target.exists():
        click.echo(f"already here: {target}")
    else:
        code = run_cli(["chats", "pull", name] + (["--to", args.to] if args.to else []))
        if code != 0:
            return code

    code = run_cli(["chats", "inspect", str(target)])
    if code != 0:
        return code

    restore = ["chats", "restore", str(target)]
    for tool in args.tool:
        restore += ["--tool", tool]
    for mapping in args.map:
        restore += ["--map", mapping]
    if args.config:
        restore += ["--config", str(args.config)]

    if args.no_restore:
        click.echo(f"\nfetched only. To restore:\n  uv run scieflow chats restore {target}")
        return 0
    click.echo("\ndry run — nothing is written yet:")
    code = run_cli(restore)
    if code != 0:
        return code
    if not (args.yes or ui.confirm("Apply these changes now?", default=False)):
        click.echo(f"left alone. To apply later:\n  uv run scieflow "
                   f"{' '.join(restore[1:] if restore[0] == 'chats' else restore)} --apply")
        return 0
    return run_cli(restore + ["--apply", "--yes"])


# -- entry point -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="chats_sync.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="chats config file (default <repo>/config/chats.yml)")
    sub = ap.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tool", action="append", default=[],
                        choices=["claude", "codex", "agy", "gemini"],
                        help="limit to an agent (repeatable); skips the question")
    common.add_argument("--yes", action="store_true", help="no confirmation prompts")

    p_push = sub.add_parser("push", parents=[common], help="bundle chats and upload them")
    p_push.add_argument("--project", action="append", default=[],
                        help="limit to projects whose path contains this (repeatable)")
    p_push.add_argument("--since", help="only chats updated on or after YYYY-MM-DD")
    p_push.add_argument("--out", help="bundle path (default: <bundle_dir>/<name>.zip)")
    p_push.add_argument("--no-push", action="store_true", help="build the bundle, do not upload")
    p_push.add_argument("--no-encrypt", action="store_true",
                        help="plaintext bundle; refused by upload, needs --yes")
    p_push.add_argument("--commit", action="store_true",
                        help="git-commit the .dvc pointer after a successful upload")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", parents=[common], help="fetch a bundle and restore it")
    p_pull.add_argument("bundle", nargs="?", help="bundle name or part of it")
    p_pull.add_argument("--latest", action="store_true", help="take the newest tracked bundle")
    p_pull.add_argument("--to", help="directory to fetch into (default: bundle_dir)")
    p_pull.add_argument("--map", action="append", default=[], metavar="OLD=NEW",
                        help="rewrite a path prefix on restore (repeatable)")
    p_pull.add_argument("--no-restore", action="store_true", help="fetch and inspect only")
    p_pull.set_defaults(func=cmd_pull)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
