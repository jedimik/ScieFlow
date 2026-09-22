"""`scieflow workspace` — what runs exist, what state they are in, what is off.

Read-only. A run is `workspace/<slug>/` with a `status.yml`; its kind comes
from that file (`run:` = research loop, `workflow:` = research module).
Names starting with `_` are not runs (`_archives`, `_misc`), `news/` and
`chats/` belong to their modules, and a symlinked slug is an alias of the run
it points to (kept so old chats' paths still resolve).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from scieflow.core import config as config_mod

MODULE_DIRS = {"news", "chats"}
LOOP_FILES = ("goal.md", "config.yml", "status.yml", "budget.yml", "notebook.md")
RESEARCH_FILES = ("brief.md", "config.yml", "status.yml", "log.md")
LOOP_PHASES = ("hypothesize", "experiment", "literature", "synthesize")


@dataclass
class Run:
    slug: str
    kind: str                     # loop | lit-review | gap-discovery | … | none
    state: str
    updated: str | None
    aliases: list[str] = field(default_factory=list)
    lineage: str | None = None


def workspace_root(root: Path | None = None) -> Path:
    return (root or config_mod.repo_root()) / "workspace"


def _load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text()) if path.exists() else None
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _state(kind: str, status: dict) -> str:
    if kind == "none":
        return "no status.yml"
    if status.get("stopped"):
        stopped = status["stopped"]
        reason = stopped.get("reason") if isinstance(stopped, dict) else stopped
        return f"stopped ({reason})"
    if kind == "loop":
        phases = status.get("phases") or {}
        current = next(
            (p for p in LOOP_PHASES if (phases.get(p) or "pending") != "done"), None
        )
        it = status.get("iteration", 0)
        return f"iteration {it}, {current or 'all phases done'}" + (
            f" ({phases.get(current)})" if current and phases.get(current) not in (None, "pending") else ""
        )
    phase = status.get("phase")
    return f"phase {phase}" if phase else "started"


def _updated(path: Path) -> str | None:
    stamps = [
        (path / name).stat().st_mtime
        for name in ("status.yml", "log.md", "notebook.md")
        if (path / name).exists()
    ]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).date().isoformat()


def describe(path: Path) -> Run:
    status = _load(path / "status.yml")
    if "run" in status:
        kind = "loop"
    elif status.get("workflow"):
        kind = str(status["workflow"])
    else:
        kind = "none"
    config = _load(path / "config.yml")
    return Run(
        slug=path.name,
        kind=kind,
        state=_state(kind, status),
        updated=_updated(path),
        lineage=config.get("lineage") if isinstance(config.get("lineage"), str) else None,
    )


def list_runs(root: Path | None = None) -> list[Run]:
    """Real runs, each carrying the alias names that point at it."""
    ws = workspace_root(root)
    if not ws.is_dir():
        return []
    runs: dict[str, Run] = {}
    aliases: dict[str, list[str]] = {}
    for entry in sorted(ws.iterdir()):
        name = entry.name
        if name.startswith((".", "_")) or name in MODULE_DIRS or not entry.is_dir():
            continue
        if entry.is_symlink():
            target = entry.resolve()
            if target.parent == ws.resolve():
                aliases.setdefault(target.name, []).append(name)
            continue
        runs[name] = describe(entry)
    for target, names in aliases.items():
        if target in runs:
            runs[target].aliases = sorted(names)
    return sorted(runs.values(), key=lambda r: r.slug)


def moved_entries(root: Path | None = None) -> list[tuple[str, str]]:
    """Top-level symlinks into `_misc/`: (old name, new location)."""
    ws = workspace_root(root)
    out = []
    for entry in sorted(ws.iterdir()) if ws.is_dir() else []:
        if entry.is_symlink():
            target = os.readlink(entry)
            if target.startswith("_misc/"):
                out.append((entry.name, target))
    return out


# -- sync status ----------------------------------------------------------
GIB = 1024 ** 3


def _pointer_for(slug: str, root: Path) -> Path | None:
    """The DVC pointer of a run's archive, or its legacy per-file pointer."""
    ws = workspace_root(root)
    for candidate in (ws / "_archives" / f"{slug}.zip.dvc", ws / f"{slug}.dvc"):
        if candidate.exists():
            return candidate
    return None


def sync_status(slug: str, root: Path | None = None, big_bytes: int = GIB) -> dict:
    """What a run would upload: new files since its last sync, and the big ones.

    Rebuildable directories are excluded, exactly as archives exclude them, so
    the numbers match what a push would actually carry.
    """
    root = root or config_mod.repo_root()
    path = workspace_root(root) / slug
    if not path.is_dir():
        raise click.ClickException(f"no run workspace/{slug}")
    archive = _archive_mod()
    _, files, links = archive._collect(path)
    pointer = _pointer_for(slug, root)
    since = pointer.stat().st_mtime if pointer else None

    total = new_bytes = 0
    new: list[str] = []
    big: list[tuple[int, str]] = []
    for item in files:
        try:
            stat = item.stat()
        except OSError:
            continue
        rel = str(item.relative_to(path))
        total += stat.st_size
        if since is None or stat.st_mtime > since:
            new.append(rel)
            new_bytes += stat.st_size
        if stat.st_size >= big_bytes:
            big.append((stat.st_size, rel))
    return {
        "slug": slug,
        "tracked": pointer is not None,
        "pointer": str(pointer.relative_to(root)) if pointer else None,
        "last_sync": (datetime.fromtimestamp(since, tz=timezone.utc).isoformat(timespec="seconds")
                      if since else None),
        "files": len(files),
        "bytes": total,
        "new_files": len(new),
        "new_bytes": new_bytes,
        "new_sample": sorted(new)[:10],
        "big_files": [{"bytes": size, "path": rel}
                      for size, rel in sorted(big, reverse=True)[:20]],
        "symlinks": len(links),
    }


# -- doctor ---------------------------------------------------------------
def _archive_mod():
    try:
        from sflib import archive
    except ImportError:
        sys.path.insert(0, str(config_mod.repo_root() / "scripts"))
        from sflib import archive
    return archive


def _size(path: Path, cap: int = 200_000) -> int:
    total, seen = 0, 0
    for dirpath, dirnames, filenames in os.walk(path):
        for name in filenames:
            seen += 1
            if seen > cap:
                return total
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def doctor(slug: str, root: Path | None = None) -> dict:
    """Read-only findings for one run."""
    from scieflow.core import agent_config

    root = root or config_mod.repo_root()
    ws = workspace_root(root)
    path = ws / slug
    if not path.is_dir():
        raise click.ClickException(f"no run workspace/{slug}")
    if path.is_symlink():
        return {"slug": slug, "alias_of": os.readlink(path)}
    run = describe(path)
    expected = LOOP_FILES if run.kind == "loop" else RESEARCH_FILES if run.kind != "none" else ()
    archive = _archive_mod()

    junk: dict[str, int] = {}
    self_links = 0
    for dirpath, dirnames, filenames in os.walk(path):
        base = Path(dirpath)
        depth = len(base.relative_to(path).parts)
        kept = []
        for name in dirnames:
            child = base / name
            if not child.is_symlink() and archive._skip_dir(child, path):
                junk[str(child.relative_to(path))] = _size(child)
            else:
                kept.append(name)
        dirnames[:] = kept if depth < 4 else []
        for name in filenames + [d for d in kept if (base / d).is_symlink()]:
            link = base / name
            if link.is_symlink() and os.readlink(link).startswith(str(path) + "/"):
                self_links += 1

    config = _load(path / "config.yml")
    legacy = [k for k in (*agent_config.LEGACY_ROLE_KEYS, "agent", "journal_profiler",
                          *agent_config.LEGACY_UNMAPPED_KEYS) if k in config]
    logs_scripts = len(list((path / "logs").glob("*.py"))) if (path / "logs").is_dir() else 0
    return {
        "slug": slug,
        "kind": run.kind,
        "state": run.state,
        "missing": [f for f in expected if not (path / f).exists()],
        "legacy_config_keys": legacy,
        "junk_dirs": dict(sorted(junk.items(), key=lambda kv: -kv[1])),
        "absolute_self_links": self_links,
        "scripts_in_logs": logs_scripts,
        "per_file_dvc_pointer": (ws / f"{slug}.dvc").exists(),
    }


def _human(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


# -- CLI ------------------------------------------------------------------
@click.group()
def workspace():
    """Research runs under workspace/: list, index, doctor."""


@workspace.command("list")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def list_cmd(as_json):
    """List runs with kind, state and last activity."""
    runs = list_runs()
    if as_json:
        click.echo(json.dumps([asdict(r) for r in runs], indent=2))
        return
    for r in runs:
        alias = f"  (also: {', '.join(r.aliases)})" if r.aliases else ""
        click.echo(f"{r.updated or '    ?     '}  {r.kind:<14} {r.slug:<50} {r.state}{alias}")
    moved = moved_entries()
    if moved:
        click.echo("\nnot runs (moved, old path kept as a link):")
        for old, new in moved:
            click.echo(f"  {old} -> workspace/{new}")


@workspace.command()
def index():
    """Write workspace/INDEX.md (generated; not committed)."""
    runs = list_runs()
    ws = workspace_root()
    lines = [
        "# Workspace index",
        "",
        f"_Generated by `scieflow workspace index` on "
        f"{datetime.now(timezone.utc).date().isoformat()}. Do not edit._",
        "",
        "| Run | Kind | State | Updated | Aliases |",
        "|---|---|---|---|---|",
    ]
    for r in runs:
        lines.append(f"| `{r.slug}` | {r.kind} | {r.state} | {r.updated or ''} | "
                     f"{', '.join(f'`{a}`' for a in r.aliases)} |")
    families: dict[str, list[str]] = {}
    for r in runs:
        if r.lineage:
            families.setdefault(r.lineage, []).append(r.slug)
    if families:
        lines += ["", "## Lineage", ""]
        for name, slugs in sorted(families.items()):
            lines.append(f"- **{name}**: " + " → ".join(f"`{s}`" for s in slugs))
    moved = moved_entries()
    if moved:
        lines += ["", "## Not runs", ""]
        lines += [f"- `{old}` → `workspace/{new}`" for old, new in moved]
    (ws / "INDEX.md").write_text("\n".join(lines) + "\n")
    click.echo(f"wrote {ws / 'INDEX.md'} ({len(runs)} runs)")


@workspace.command("sync-status")
@click.argument("slug", required=False)
@click.option("--big-gb", default=1.0, show_default=True,
              help="Call a file big at this size, in gigabytes.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def sync_status_cmd(slug, big_gb, as_json):
    """What a run would upload: new files since its last sync, and big ones."""
    big_bytes = int(big_gb * GIB)
    slugs = [slug.removeprefix("workspace/").rstrip("/")] if slug else [
        r.slug for r in list_runs()]
    reports = []
    for name in slugs:
        try:
            reports.append(sync_status(name, big_bytes=big_bytes))
        except click.ClickException:
            if slug:
                raise
    if as_json:
        click.echo(json.dumps(reports if not slug else reports[0], indent=2))
        return
    if not reports:
        click.echo("no runs in workspace/ — nothing to sync "
                   "(pull one with: uv run scripts/dvc_sync.py pull <slug>)")
        return
    for report in reports:
        state = "never synced" if not report["tracked"] else f"synced {report['last_sync'][:10]}"
        click.echo(f"{report['slug']:<50} {state:<20} "
                   f"{report['new_files']:>6} new / {report['files']:>6} files  "
                   f"{_human(report['new_bytes']):>8} new / {_human(report['bytes']):>8}")
        for entry in report["big_files"][:5]:
            click.echo(f"    big: {_human(entry['bytes']):>8}  {entry['path']}")


@workspace.command("doctor")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def doctor_cmd(slug, as_json):
    """Read-only health report for one run."""
    report = doctor(slug.removeprefix("workspace/").rstrip("/"))
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    if "alias_of" in report:
        click.echo(f"{slug} is an alias of {report['alias_of']}")
        return
    click.echo(f"{report['slug']}  [{report['kind']}]  {report['state']}")
    if report["missing"]:
        click.echo(f"  missing: {', '.join(report['missing'])}")
    if report["legacy_config_keys"]:
        click.echo(f"  legacy config keys (honoured): {', '.join(report['legacy_config_keys'])}")
    if report["junk_dirs"]:
        total = sum(report["junk_dirs"].values())
        click.echo(f"  rebuildable dirs, left out of archives ({_human(total)}):")
        for rel, size in list(report["junk_dirs"].items())[:8]:
            click.echo(f"    {_human(size):>7}  {rel}")
    if report["absolute_self_links"]:
        click.echo(f"  {report['absolute_self_links']} absolute links into this run "
                   "(break if the run is renamed or moved — keep the slug)")
    if report["scripts_in_logs"]:
        click.echo(f"  {report['scripts_in_logs']} scripts in logs/ (new runs: tools/)")
    if report["per_file_dvc_pointer"]:
        click.echo(f"  per-file DVC pointer workspace/{slug}.dvc — untrack it, "
                   "then push as a zip (docs/DVC_STORAGE.md)")
