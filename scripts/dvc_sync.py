#!/usr/bin/env python3
"""DVC sync automation for ScieFlow runs.

Manages data tracking and S3 sync for:
  - ScieFlow: workspace/<slug>

Supports auto-loading credentials and remote settings from .env.

Usage:
    uv run scripts/dvc_sync.py track [SLUG ...] [--all]
    uv run scripts/dvc_sync.py push [SLUG ...] [--all]
    uv run scripts/dvc_sync.py pull [SLUG ...] [--all]
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

from sflib import archive, config


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
        if item.is_dir() and not item.name.startswith(".") and item.name != archive.ARCHIVE_DIR:
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


def cmd_push(slugs: list[str], root: Path, workspace_root: Path) -> int:
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

    return run_cmd(["dvc", "push", *targets], cwd=root)


def cmd_pull(slugs: list[str], root: Path, workspace_root: Path) -> int:
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

    return run_cmd(["dvc", "pull", *targets], cwd=root)


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


def main() -> None:
    root = config.repo_root()
    workspace_root = root / "workspace"
    default_env_path = root / ".env"

    ap = argparse.ArgumentParser(description="ScieFlow DVC Workspace Sync Helper")
    ap.add_argument("--env-file", type=Path, default=default_env_path, help="Path to .env file")
    sub = ap.add_subparsers(dest="command", required=True)

    # track
    p_track = sub.add_parser("track", help="Add workspace directory to DVC tracking")
    p_track.add_argument("slugs", nargs="*", help="Workspace slug(s)")
    p_track.add_argument("--all", action="store_true", help="Track all workspaces")

    # push
    p_push = sub.add_parser("push", help="Push tracked workspace(s) to remote storage")
    p_push.add_argument("slugs", nargs="*", help="Workspace slug(s)")
    p_push.add_argument("--all", action="store_true", help="Push all workspaces")

    # pull
    p_pull = sub.add_parser("pull", help="Pull workspace data from remote storage")
    p_pull.add_argument("slugs", nargs="*", help="Workspace slug(s)")
    p_pull.add_argument("--all", action="store_true", help="Pull all workspaces")

    # status
    sub.add_parser("status", help="Show DVC and workspace tracking status")

    args = ap.parse_args()

    # Automatically load environment variables into execution environment
    if args.env_file.exists():
        load_env_into_environ(args.env_file)

    if args.command == "status":
        sys.exit(cmd_status(root, workspace_root))

    slugs = resolve_slugs(args.slugs, args.all, workspace_root)

    if args.command == "track":
        sys.exit(cmd_track(slugs, root, workspace_root))
    elif args.command == "push":
        sys.exit(cmd_push(slugs, root, workspace_root))
    elif args.command == "pull":
        sys.exit(cmd_pull(slugs, root, workspace_root))


if __name__ == "__main__":
    main()
