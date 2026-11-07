#!/usr/bin/env python3
"""DVC sync automation for ScieFlow runs.

Manages data tracking and S3 sync for:
  - ScieFlow: workspace/<slug>

Supports auto-loading credentials and remote settings from .env.

Archive mode (the default, see docs/DVC_STORAGE.md) packs a run into
workspace/_archives/<slug>.zip before push and unpacks it after pull, so one
run is one remote object instead of tens of thousands. Per-file directory
tracking still exists but must be asked for with --no-archive.

Browser-facing archive steps (docs/web.md, T-9): `fetch` downloads one zip and verifies it, `restore`
unpacks a verified zip into an absent or empty workspace/<slug>. Both take the archive operation lock of
their slug; push and pull take it too. --state-dir defaults to the project's state directory.

Usage:
    uv run scripts/dvc_sync.py track [SLUG ...] [--all]
    uv run scripts/dvc_sync.py push SLUG [SLUG ...] [--archive | --no-archive] [--keep-zip] [--remote NAME]
    uv run scripts/dvc_sync.py pull SLUG [SLUG ...] [--force] [--remote NAME]
    uv run scripts/dvc_sync.py fetch SLUG [--replace] [--verify-only] [--remote NAME]
    uv run scripts/dvc_sync.py restore SLUG
    uv run scripts/dvc_sync.py status
"""

import argparse
import codecs
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scieflow.core import archives as arch
from scieflow.core import config
from scieflow.core import control_read as cr
from scieflow.core import store
from scieflow.core.project import Project, ProjectError, clean_slug
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


def run_cmd_tail(cmd: list[str], cwd: Path, tail: int = arch.DVC_OUTPUT_TAIL) -> tuple[int, bytes]:
    """Run cmd with stdout and stderr merged: echo every line, keep only the last `tail` bytes.

    The echo goes to this process's stdout, so the job log keeps dvc's full output (B2:5.9). A program
    that is not installed returns 127, as the shell does; classify() maps that to dvc-missing.
    """
    print(f">> {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        return 127, b""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    kept = bytearray()
    while True:
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        sys.stdout.write(decoder.decode(chunk))
        sys.stdout.flush()
        kept += chunk
        if len(kept) > tail:
            del kept[: len(kept) - tail]
    sys.stdout.write(decoder.decode(b"", final=True))
    sys.stdout.flush()
    return proc.wait(), bytes(kept)


def _state_dir(root: Path, state_dir: Path | None) -> Path:
    return Path(state_dir) if state_dir is not None else Project(root).state_dir


def _archive_dir(state_dir: Path) -> Path:
    return Path(state_dir) / arch.ARCHIVE_STATE


def _locked_slug(slug: str, arch_dir: Path, action) -> int:
    """Run action() under the slug's operation lock; a held lock writes nothing and exits LOCKED_EXIT."""
    arch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with store.try_locked(arch.lock_path(arch_dir, slug)):
            return action()
    except store.Busy:
        print(f"Error: {slug}: {arch.sentence('locked')}")
        return arch.LOCKED_EXIT


def _pointer(root: Path, slug: str) -> arch.Pointer:
    """The zip pointer of slug, read bounded and no-follow (B2:5.5)."""
    rel = f"_archives/{slug}.zip.dvc"
    try:
        fd = cr.open_dir(root, ("workspace", archive.ARCHIVE_DIR))
    except OSError:
        return arch.Pointer(slug, "unreadable", None, None, False, "missing", rel)
    try:
        return arch.read_pointer(fd, f"{slug}.zip.dvc", slug)
    finally:
        os.close(fd)


def _read_record(state_dir: Path, slug: str) -> dict | None:
    """The verification record of slug, or None when absent or unreadable (bounded, no-follow)."""
    try:
        fd = cr.open_dir(Path(state_dir), (arch.ARCHIVE_STATE,))
    except OSError:
        return None
    try:
        raw = cr.read_bytes(fd, f"{slug}.json", arch.RECORD_MAX_BYTES)
    finally:
        os.close(fd)
    if raw.status != "ok":
        return None
    try:
        doc = json.loads(raw.data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_record(state_dir: Path, slug: str, data: dict) -> None:
    archive.write_record(arch.record_path(_archive_dir(state_dir), slug), data)


def _stat(path: Path) -> os.stat_result | None:
    try:
        return os.stat(path)
    except OSError:
        return None


def _stat_fields(st: os.stat_result) -> dict:
    return {"size": st.st_size, "st_dev": st.st_dev, "st_ino": st.st_ino,
            "st_mtime_ns": st.st_mtime_ns}


def _same_file(record: dict | None, st: os.stat_result | None) -> bool:
    if record is None or st is None:
        return False
    return all(record.get(key) == value for key, value in _stat_fields(st).items())


def _outside_cache(zip_file: Path, root: Path) -> bool:
    """A zip that is a link to somewhere other than the DVC cache is never opened (B2:5.5)."""
    if not zip_file.is_symlink():
        return False
    cache = Path(os.path.realpath(root / ".dvc" / "cache"))
    return not Path(os.path.realpath(zip_file)).is_relative_to(cache)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_failure(state_dir: Path, slug: str, prev: dict | None, st: os.stat_result | None,
                   pointer_md5: str, code: str, exit_code: int | None, pid: int) -> None:
    """Record a failure. A verification the file still matches is kept, so a failed re-download over an
    untouched ready copy stays ready (B2:5.5); otherwise the record only names the file it saw."""
    if _same_file(prev, st):
        record = dict(prev)
    elif st is not None:
        record = {"pointer_md5": pointer_md5, **_stat_fields(st)}
    else:
        record = {}
    record.update(outcome="failed", reason=code, exit_code=exit_code, pid=pid)
    _write_record(state_dir, slug, record)


def cmd_fetch(slug: str, root: Path, workspace_root: Path, state_dir: Path, *,
              replace: bool = False, verify_only: bool = False, remote: str | None = None) -> int:
    """Download one archive zip with dvc, then check its size and MD5 and record the verification.

    Never unpacks and never deletes a zip. --verify-only checks the local file and never runs dvc.
    """
    arch_dir = _archive_dir(state_dir)
    arch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with store.try_locked(arch.lock_path(arch_dir, slug)):
            return _fetch_locked(slug, root, workspace_root, state_dir, replace=replace,
                                 verify_only=verify_only, remote=remote)
    except store.Busy:
        print(f"Error: {slug}: {arch.sentence('locked')}")
        return arch.LOCKED_EXIT


def _fetch_locked(slug: str, root: Path, workspace_root: Path, state_dir: Path, *,
                  replace: bool, verify_only: bool, remote: str | None) -> int:
    ptr = _pointer(root, slug)
    if ptr.kind != "zip":
        print(f"Error: {slug}: not a zip archive pointer ({ptr.reason or ptr.kind})")
        return 1
    zip_file = archive.archive_path(workspace_root, slug)
    pointer = archive.pointer_path(workspace_root, slug)
    if _outside_cache(zip_file, root):
        print(f"Error: {slug}: the local zip is a link outside the DVC cache")
        return 1
    prev = _read_record(state_dir, slug)
    pid = os.getpid()
    st = _stat(zip_file)
    size_matches = st is not None and ptr.size is not None and st.st_size == ptr.size

    def fail(code: str, exit_code: int | None) -> int:
        _write_failure(state_dir, slug, prev, _stat(zip_file), ptr.md5, code, exit_code, pid)
        print(f"Error: {slug}: {arch.sentence(code, n=exit_code, md5=ptr.md5, log_path='the job log')}")
        return 1

    rc = None
    if not verify_only and not (size_matches and not replace):
        cmd = ["dvc", "pull", *_remote_args(remote), *(["--force"] if replace else []),
               str(pointer.relative_to(root))]
        rc, tail = run_cmd_tail(cmd, cwd=root)
        if rc != 0:
            return fail(arch.classify(rc, tail), rc)
    st = _stat(zip_file)
    if st is None or (ptr.size is not None and st.st_size != ptr.size):
        return fail("verify-size", rc)
    if archive.md5_file(zip_file, arch.MD5_CHUNK_BYTES) != ptr.md5:
        return fail("verify-md5", rc)
    linked = archive.link_to_cache(zip_file, pointer, root / ".dvc" / "cache")
    st = os.stat(zip_file)  # after the link: a hard link has its own inode
    _write_record(state_dir, slug, {
        "pointer_md5": ptr.md5, **_stat_fields(st), "verified_at": _now(), "linked": linked,
        "outcome": "ready", "reason": None, "exit_code": 0, "pid": pid,
    })
    print(f"Verified workspace/{archive.ARCHIVE_DIR}/{slug}.zip ({st.st_size} bytes)")
    return 0


RESTORE_MESSAGES = {
    "run-exists": "workspace/{slug} exists and is not empty; restore needs it absent or empty.",
    "corrupt-member": "the archive has a corrupt member (its CRC does not match).",
    "unreadable-zip": "the archive is not a readable zip.",
}


def _ready_for_restore(record: dict | None, ptr: arch.Pointer, st: os.stat_result) -> bool:
    """The verification the state machine calls ready (B2:5.5), re-checked under the lock (T9-D15)."""
    if record is None or record.get("verified_at") is None:
        return False
    if record.get("pointer_md5") != ptr.md5 or not _same_file(record, st):
        return False
    if record.get("outcome") == "failed" and record.get("reason") == "verify-md5":
        return False
    return ptr.size is None or st.st_size == ptr.size


def cmd_restore(slug: str, root: Path, workspace_root: Path, state_dir: Path) -> int:
    """Unpack a verified zip into workspace/<slug>, only when that folder is absent or empty."""
    arch_dir = _archive_dir(state_dir)
    arch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with store.try_locked(arch.lock_path(arch_dir, slug)):
            return _restore_locked(slug, root, workspace_root, state_dir)
    except store.Busy:
        print(f"Error: {slug}: {arch.sentence('locked')}")
        return arch.LOCKED_EXIT


def _restore_locked(slug: str, root: Path, workspace_root: Path, state_dir: Path) -> int:
    ptr = _pointer(root, slug)
    zip_file = archive.archive_path(workspace_root, slug)
    st = _stat(zip_file)
    record = _read_record(state_dir, slug)
    if (ptr.kind != "zip" or st is None or _outside_cache(zip_file, root)
            or not _ready_for_restore(record, ptr, st)):
        print(f"Error: {slug}: not ready to restore; fetch and verify the archive first.")
        return 1
    pid = os.getpid()
    dest = workspace_root / slug

    def fail(code: str) -> int:
        _write_record(state_dir, slug, {**record, "outcome": "failed", "reason": code,
                                        "exit_code": None, "pid": pid})
        print(f"Error: {slug}: restore refused: "
              + RESTORE_MESSAGES[code].format(slug=slug))
        return 1

    if os.path.lexists(dest) and not (dest.is_dir() and not os.path.islink(dest) and not any(dest.iterdir())):
        return fail("run-exists")
    try:
        archive.verify_zip(zip_file)
    except archive.ArchiveError as exc:
        return fail("corrupt-member" if "corrupt member" in str(exc) else "unreadable-zip")
    try:
        archive.extract_zip(zip_file, dest, force=False)
    except archive.ArchiveError:
        return fail("unreadable-zip")
    _write_record(state_dir, slug, {**record, "outcome": "restored", "reason": None,
                                    "exit_code": 0, "pid": pid})
    print(f"Restored workspace/{slug} from {archive.ARCHIVE_DIR}/{slug}.zip")
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
    state_dir: Path | None = None,
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
    arch_dir = _archive_dir(_state_dir(root, state_dir))
    for slug in archived:
        rc = _locked_slug(slug, arch_dir, lambda s=slug: push_archive(
            s, root, workspace_root, keep_zip=keep_zip, remote=remote))
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
    state_dir: Path | None = None,
) -> int:
    archived = [s for s in slugs if use_archive(s, root, workspace_root, command="pull")]
    directories = [s for s in slugs if s not in archived]
    exit_code = 0
    arch_dir = _archive_dir(_state_dir(root, state_dir))
    for slug in archived:
        rc = _locked_slug(slug, arch_dir, lambda s=slug: pull_archive(
            s, root, workspace_root, force=force, remote=remote))
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

    # fetch / restore (one archived run; the web app and `scieflow workspace archive` call these)
    p_fetch = sub.add_parser("fetch", help="Download one archive zip and verify it (never unpacks)")
    p_fetch.add_argument("slug", help="Archived run slug")
    p_fetch.add_argument("--replace", action="store_true",
                         help="Pull again over an existing zip (dvc pull --force)")
    p_fetch.add_argument("--verify-only", action="store_true",
                         help="Check the local zip only; never runs dvc")
    p_fetch.add_argument("--remote", help="DVC remote name (default: core.remote)")

    p_restore = sub.add_parser("restore", help="Unpack a verified zip into an absent or empty run")
    p_restore.add_argument("slug", help="Archived run slug")

    for p in (p_push, p_pull, p_fetch, p_restore):
        p.add_argument("--state-dir", type=Path, default=None,
                       help="Project state directory (default: the project's state directory)")

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

    if args.command in ("fetch", "restore"):
        # One archived run, named by its slug; no workspace directory need exist (the pointer is enough).
        try:
            slug = clean_slug(_clean_slug(args.slug))
        except ProjectError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        state = _state_dir(root, args.state_dir)
        if args.command == "fetch":
            sys.exit(cmd_fetch(slug, root, workspace_root, state, replace=args.replace,
                               verify_only=args.verify_only, remote=args.remote))
        sys.exit(cmd_restore(slug, root, workspace_root, state))

    # push/pull are selection-only: `--all` exists for `track` alone, because
    # moving hundreds of gigabytes should never be one flag away.
    slugs = resolve_slugs(args.slugs, getattr(args, "all", False), workspace_root)

    if args.command == "track":
        sys.exit(cmd_track(slugs, root, workspace_root))
    elif args.command == "push":
        sys.exit(cmd_push(slugs, root, workspace_root, archive_flag=args.archive,
                          keep_zip=args.keep_zip, remote=args.remote,
                          state_dir=args.state_dir))
    elif args.command == "pull":
        sys.exit(cmd_pull(slugs, root, workspace_root, force=args.force, remote=args.remote,
                          state_dir=args.state_dir))


if __name__ == "__main__":
    main()
