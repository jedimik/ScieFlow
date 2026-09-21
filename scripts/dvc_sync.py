#!/usr/bin/env python3
"""DVC sync automation for ScieFlow runs.

Manages data tracking and S3 sync for:
  - ScieFlow: workspace/<slug>

Supports auto-loading credentials and remote settings from .env.

Archive mode (the default, see docs/DVC_STORAGE.md) packs a run into
workspace/_archives/<slug>.zip before push and unpacks it after pull, so one
run is one remote object instead of tens of thousands. Per-file directory
tracking still exists but must be asked for with --no-archive.

Usage:
    uv run scripts/dvc_sync.py track [SLUG ...] [--all]
    uv run scripts/dvc_sync.py push SLUG [SLUG ...] [--archive | --no-archive] [--keep-zip] [--remote NAME]
    uv run scripts/dvc_sync.py pull SLUG [SLUG ...] [--force] [--remote NAME]
    uv run scripts/dvc_sync.py status
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scieflow.core import config
from sflib import archive


def load_env_into_environ(env_path: Path) -> None:
    """Load .env key=values into os.environ if not already present."""
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ and v:
                os.environ[k] = v


def find_workspaces(workspace_root: Path) -> list[str]:
    """List valid workspace run slugs in workspace root."""
    if not workspace_root.exists():
        return []
    slugs = []
    for item in sorted(workspace_root.iterdir()):
        # `_*` are not runs (_archives, _misc); symlinks are aliases of a run
        # that is listed under its real name, so pushing them would duplicate it.
        if item.is_symlink() or item.name.startswith((".", "_")) or not item.is_dir():
            continue
        slugs.append(item.name)
    return slugs


def _clean_slug(raw: str) -> str:
    slug = raw.strip().rstrip("/")
    return slug[len("workspace/"):] if slug.startswith("workspace/") else slug


def resolve_slugs(args_slugs: list[str], all_flag: bool, workspace_root: Path) -> list[str]:
    available = find_workspaces(workspace_root)
    if all_flag:
        return sorted(set(available) | set(archive.archived_slugs(workspace_root)))
    if not args_slugs:
        raise ValueError("Specify at least one workspace slug or use --all")
    slugs = [_clean_slug(s) for s in args_slugs]
    for raw, slug in zip(args_slugs, slugs):
        if slug == archive.ARCHIVE_DIR:
            raise ValueError(f"'{raw}' is the archive directory, not a workspace run")
        if (
            slug not in available
            and not (workspace_root / slug).exists()
            and not (workspace_root / f"{slug}.dvc").exists()
            and not archive.pointer_path(workspace_root, slug).exists()
        ):
            raise FileNotFoundError(f"Workspace run '{raw}' not found in {workspace_root}")
    return slugs


def run_cmd(cmd: list[str], cwd: Path) -> int:
    print(f">> {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd)
    return res.returncode


def cmd_track(slugs: list[str], root: Path, workspace_root: Path) -> int:
    exit_code = 0
    for slug in slugs:
        ws_dir = workspace_root / slug
        if archive.pointer_path(workspace_root, slug).exists():
            print(f"Skipping {slug}: archived; `push` manages its archive pointer.")
            continue
        if not ws_dir.exists():
            print(f"Skipping {slug}: directory {ws_dir} does not exist.")
            continue
        rel_path = ws_dir.relative_to(root)
        print(f"Tracking {rel_path} with DVC...")
        rc = run_cmd(["dvc", "add", str(rel_path)], cwd=root)
        if rc != 0:
            exit_code = rc
    return exit_code


def use_archive(
    slug: str,
    root: Path,
    workspace_root: Path,
    *,
    command: str,
    archive_flag: bool | None = None,
) -> bool:
    """Decide archive vs directory mode for one slug (spec section 1).

    Pull: the archive pointer alone decides. Push: --no-archive wins, then an
    existing pointer, then --archive, then `archive: true` in the run config.
    """
    has_pointer = archive.pointer_path(workspace_root, slug).exists()
    if command == "pull":
        return has_pointer
    if archive_flag is False:
        return False
    if has_pointer or archive_flag is True:
        return True
    ws_dir = workspace_root / slug
    if not ws_dir.is_dir():
        return False
    return config.load_run_config(ws_dir, root).get("archive") is True


def _remote_args(remote: str | None) -> list[str]:
    return ["-r", remote] if remote else []


def push_archive(
    slug: str,
    root: Path,
    workspace_root: Path,
    *,
    keep_zip: bool = False,
    remote: str | None = None,
) -> int:
    ws_dir = workspace_root / slug
    if not ws_dir.is_dir():
        print(f"Skipping {slug}: directory {ws_dir} does not exist.")
        return 0
    zip_path = archive.archive_path(workspace_root, slug)
    rel_zip = str(zip_path.relative_to(root))
    try:
        archive.ensure_space(ws_dir, zip_path.parent)
        print(f"Archiving workspace/{slug} -> {rel_zip} ...")
        archive.build_zip(ws_dir, zip_path)
    except archive.ArchiveError as exc:
        print(f"Error: {slug}: {exc}")
        return 1

    rc = run_cmd(["dvc", "add", "--to-remote", *_remote_args(remote), rel_zip], cwd=root)
    if rc != 0:
        print(f"Error: upload of {slug} failed; archive kept at {rel_zip}")
        return rc
    if keep_zip:
        print(f"Kept local archive {rel_zip}")
    else:
        zip_path.unlink()
        print(f"Removed local archive {rel_zip}")

    # `git add -A <path>` also stages deletions; list only paths git can resolve.
    to_stage = [str(zip_path.parent.relative_to(root))]
    old_pointer = workspace_root / f"{slug}.dvc"
    if old_pointer.exists():
        rel_old = str(old_pointer.relative_to(root))
        gitignore = workspace_root / ".gitignore"
        had_gitignore = gitignore.exists()
        rc = run_cmd(["dvc", "remove", rel_old], cwd=root)
        if rc != 0:
            return rc
        to_stage.append(rel_old)
        if had_gitignore:
            to_stage.append(str(gitignore.relative_to(root)))
    print(f"Next: git add -A {' '.join(to_stage)}")
    print(f'      git commit -m "chore(dvc): archive workspace {slug}"')
    return 0


def pull_archive(
    slug: str,
    root: Path,
    workspace_root: Path,
    *,
    force: bool = False,
    remote: str | None = None,
) -> int:
    zip_path = archive.archive_path(workspace_root, slug)
    pointer = archive.pointer_path(workspace_root, slug)
    rc = run_cmd(["dvc", "pull", *_remote_args(remote), str(pointer.relative_to(root))], cwd=root)
    if rc != 0:
        return rc
    try:
        count = archive.verify_zip(zip_path)
        archive.extract_zip(zip_path, workspace_root / slug, force=force)
    except archive.ArchiveError as exc:
        print(f"Error: {slug}: {exc}")
        return 1
    rel_zip = zip_path.relative_to(root)
    if archive.link_to_cache(zip_path, pointer, root / ".dvc" / "cache"):
        print(f"Kept {rel_zip} (hardlinked to DVC cache)")
    else:
        print(f"Kept {rel_zip} (copy; could not hardlink to DVC cache)")
    print(f"Extracted {count} entries into workspace/{slug}")
    return 0


def _push_directories(
    slugs: list[str], root: Path, workspace_root: Path, remote: str | None = None
) -> int:
    targets = []
    for slug in slugs:
        dvc_file = workspace_root / f"{slug}.dvc"
        if dvc_file.exists():
            targets.append(str(dvc_file.relative_to(root)))
        else:
            ws_dir = workspace_root / slug
            if ws_dir.exists():
                print(f"Warning: {dvc_file.name} not found. Tracking {slug} first...")
                rc = run_cmd(["dvc", "add", str(ws_dir.relative_to(root))], cwd=root)
                if rc == 0 and dvc_file.exists():
                    targets.append(str(dvc_file.relative_to(root)))
                else:
                    return rc or 1

    if not targets:
        print("No DVC targets found to push.")
        return 0

    return run_cmd(["dvc", "push", *_remote_args(remote), *targets], cwd=root)


def cmd_push(
    slugs: list[str],
    root: Path,
    workspace_root: Path,
    *,
    archive_flag: bool | None = None,
    keep_zip: bool = False,
    remote: str | None = None,
) -> int:
    archived = [s for s in slugs
                if use_archive(s, root, workspace_root, command="push", archive_flag=archive_flag)]
    directories = [s for s in slugs if s not in archived]
    if directories and archive_flag is None:
        # Directory mode uploads every file separately: one run can become tens
        # of thousands of remote objects. Never fall into it by accident.
        print(
            "Error: directory mode is not implicit. These runs would upload "
            "file-by-file:\n  " + "\n  ".join(directories)
        )
        print("Push them as a single zip (recommended):")
        print(f"  uv run scripts/dvc_sync.py push --archive {' '.join(directories)}")
        print("Or ask for per-file tracking explicitly:")
        print(f"  uv run scripts/dvc_sync.py push --no-archive {' '.join(directories)}")
        return 1
    exit_code = 0
    for slug in archived:
        rc = push_archive(slug, root, workspace_root, keep_zip=keep_zip, remote=remote)
        exit_code = exit_code or rc
    if directories or not archived:
        rc = _push_directories(directories, root, workspace_root, remote)
        exit_code = exit_code or rc
    return exit_code


def _pull_directories(
    slugs: list[str], root: Path, workspace_root: Path, remote: str | None = None
) -> int:
    targets = []
    for slug in slugs:
        dvc_file = workspace_root / f"{slug}.dvc"
        if dvc_file.exists():
            targets.append(str(dvc_file.relative_to(root)))
        else:
            print(f"Error: {dvc_file} does not exist. Cannot pull without .dvc pointer.")
            return 1

    if not targets:
        print("No DVC targets found to pull.")
        return 0

    return run_cmd(["dvc", "pull", *_remote_args(remote), *targets], cwd=root)


def cmd_pull(
    slugs: list[str],
    root: Path,
    workspace_root: Path,
    *,
    force: bool = False,
    remote: str | None = None,
) -> int:
    archived = [s for s in slugs if use_archive(s, root, workspace_root, command="pull")]
    directories = [s for s in slugs if s not in archived]
    exit_code = 0
    for slug in archived:
        rc = pull_archive(slug, root, workspace_root, force=force, remote=remote)
        exit_code = exit_code or rc
    if directories or not archived:
        rc = _pull_directories(directories, root, workspace_root, remote)
        exit_code = exit_code or rc
    return exit_code


def cmd_status(root: Path, workspace_root: Path) -> int:
    print("=== DVC Status ===")
    rc = run_cmd(["dvc", "status"], cwd=root)
    print("\n=== Workspace Runs Summary ===")
    for line in workspace_summary(workspace_root):
        print(line)
    return rc


def workspace_summary(workspace_root: Path) -> list[str]:
    available = sorted(set(find_workspaces(workspace_root)) | set(archive.archived_slugs(workspace_root)))
    if not available:
        return ["No workspace runs found."]
    lines = []
    for slug in available:
        if archive.pointer_path(workspace_root, slug).exists():
            present = archive.archive_path(workspace_root, slug).exists()
            tracked = f"[ARCHIVE] zip: {'present' if present else 'not downloaded'}"
        elif (workspace_root / f"{slug}.dvc").exists():
            tracked = "[TRACKED in DVC]"
        else:
            tracked = "[LOCAL ONLY - NOT TRACKED]"
        lines.append(f" - workspace/{slug:60} {tracked}")
    return lines


def build_parser(default_env_path: Path) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ScieFlow DVC Workspace Sync Helper")
    ap.add_argument("--env-file", type=Path, default=default_env_path, help="Path to .env file")
    sub = ap.add_subparsers(dest="command", required=True)

    # track
    p_track = sub.add_parser("track", help="Add workspace directory to DVC tracking")
    p_track.add_argument("slugs", nargs="*", help="Workspace slug(s)")
    p_track.add_argument("--all", action="store_true", help="Track all workspaces")

    # push
    p_push = sub.add_parser("push", help="Push named workspace(s) to remote storage")
    p_push.add_argument("slugs", nargs="+", help="Workspace slug(s) — name them explicitly")
    mode = p_push.add_mutually_exclusive_group()
    mode.add_argument("--archive", dest="archive", action="store_true",
                      help="Push as a single zip (archive mode)")
    mode.add_argument("--no-archive", dest="archive", action="store_false",
                      help="Force directory mode, even for archived workspaces")
    p_push.set_defaults(archive=None)
    p_push.add_argument("--keep-zip", action="store_true",
                        help="Keep the local zip after an archive-mode upload")
    p_push.add_argument("--remote", help="DVC remote name (default: core.remote)")

    # pull
    p_pull = sub.add_parser("pull", help="Pull named workspace(s) from remote storage")
    p_pull.add_argument("slugs", nargs="+", help="Workspace slug(s) — name them explicitly")
    p_pull.add_argument("--force", action="store_true",
                        help="Replace a non-empty workspace directory when extracting an archive")
    p_pull.add_argument("--remote", help="DVC remote name (default: core.remote)")

    # status
    sub.add_parser("status", help="Show DVC and workspace tracking status")
    return ap


def main() -> None:
    root = config.repo_root()
    workspace_root = root / "workspace"
    args = build_parser(root / ".env").parse_args()

    # Automatically load environment variables into execution environment
    if args.env_file.exists():
        load_env_into_environ(args.env_file)

    if args.command == "status":
        sys.exit(cmd_status(root, workspace_root))

    # push/pull are selection-only: `--all` exists for `track` alone, because
    # moving hundreds of gigabytes should never be one flag away.
    slugs = resolve_slugs(args.slugs, getattr(args, "all", False), workspace_root)

    if args.command == "track":
        sys.exit(cmd_track(slugs, root, workspace_root))
    elif args.command == "push":
        sys.exit(cmd_push(slugs, root, workspace_root, archive_flag=args.archive,
                          keep_zip=args.keep_zip, remote=args.remote))
    elif args.command == "pull":
        sys.exit(cmd_pull(slugs, root, workspace_root, force=args.force, remote=args.remote))


if __name__ == "__main__":
    main()
