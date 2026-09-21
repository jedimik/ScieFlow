"""Zip archives for DVC workspace push/pull (docs/DVC_STORAGE.md, archive mode).

Pure filesystem helpers: no dvc invocation, no network.
"""

import fnmatch
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

import yaml

ARCHIVE_DIR = "_archives"

# Rebuildable noise, never results. Logs are deliberately NOT skipped:
# workspace/<slug>/logs/ holds agent prompts and transcripts (AGENTS.md rule 2).
# `scratch/` is where runs are told to keep tests, envs, clones and caches.
_SKIP_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv",
    ".snakemake", ".cache", ".uv-cache", "uv-cache", "mpl-cache", "tmp", "scratch",
}
_SKIP_DIR_PATTERNS = ("pytest-*",)
# Relative-path suffixes: conda prefixes unpacked into a run (`runtime/host`).
_SKIP_DIR_SUFFIXES = ("runtime/host",)
_SKIP_FILES = ("*.pyc",)
_LINK_MODE = (stat.S_IFLNK | 0o777) << 16
_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(Exception):
    """An archive cannot be built, verified, or extracted safely."""


def archive_path(workspace_root: Path, slug: str) -> Path:
    return Path(workspace_root) / ARCHIVE_DIR / f"{slug}.zip"


def pointer_path(workspace_root: Path, slug: str) -> Path:
    zip_path = archive_path(workspace_root, slug)
    return zip_path.with_name(zip_path.name + ".dvc")


def archived_slugs(workspace_root: Path) -> list[str]:
    """Slugs that have an archive pointer, sorted."""
    archive_dir = Path(workspace_root) / ARCHIVE_DIR
    if not archive_dir.is_dir():
        return []
    return sorted(p.name[: -len(".zip.dvc")] for p in archive_dir.glob("*.zip.dvc"))


def _skip_dir(path: Path, src_dir: Path) -> bool:
    name = path.name
    if name in _SKIP_DIRS or any(fnmatch.fnmatch(name, p) for p in _SKIP_DIR_PATTERNS):
        return True
    rel = path.relative_to(src_dir).as_posix()
    return any(rel == s or rel.endswith("/" + s) for s in _SKIP_DIR_SUFFIXES)


def _collect(
    src_dir: Path, skip_rebuildable: bool = True
) -> tuple[list[Path], list[Path], list[Path]]:
    """Directories, files and symlinks to archive, in deterministic order.

    Symlinks are returned separately and stored as link entries — never
    followed, since one pointing into a data mount could multiply the run.
    `skip_rebuildable` applies the workspace-run skip rules; callers packing
    other trees (chat bundles keep Gemini chats under `tmp/`) turn it off.
    """
    dirs: list[Path] = []
    files: list[Path] = []
    links: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        base = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            path = base / name
            if skip_rebuildable and _skip_dir(path, src_dir):
                continue
            if path.is_symlink():
                links.append(path)
            else:
                kept.append(name)
                dirs.append(path)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                links.append(path)
            elif not any(fnmatch.fnmatch(name, pat) for pat in _SKIP_FILES):
                files.append(path)
    return dirs, files, links


def workspace_size(src_dir: Path, skip_rebuildable: bool = True) -> int:
    """Bytes that build_zip would store for src_dir."""
    _, files, _ = _collect(Path(src_dir), skip_rebuildable)
    return sum(f.stat().st_size for f in files)


def build_zip(
    src_dir: Path,
    dest_zip: Path,
    compression: int = zipfile.ZIP_STORED,
    skip_rebuildable: bool = True,
) -> None:
    """Pack src_dir into dest_zip (Zip64, stored by default), atomically.

    Workspace archives stay ZIP_STORED so DVC can dedupe them; callers packing
    text (chat transcripts) pass ZIP_DEFLATED instead.
    """
    src_dir, dest_zip = Path(src_dir), Path(dest_zip)
    dirs, files, links = _collect(src_dir, skip_rebuildable)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    partial = dest_zip.with_name(dest_zip.name + ".partial")
    try:
        with zipfile.ZipFile(
            partial, "w", compression, allowZip64=True, strict_timestamps=False
        ) as zf:
            for d in dirs:
                zf.write(d, d.relative_to(src_dir).as_posix() + "/")
            for f in files:
                zf.write(f, f.relative_to(src_dir).as_posix())
            for link in links:
                info = zipfile.ZipInfo(link.relative_to(src_dir).as_posix())
                info.create_system = 3  # unix, so external_attr carries the mode
                info.external_attr = _LINK_MODE
                zf.writestr(info, os.readlink(link))
        partial.replace(dest_zip)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def verify_zip(zip_path: Path) -> int:
    """Check every member's CRC; return the member count."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            count = len(zf.infolist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"{zip_path}: not a readable zip ({exc})") from exc
    if bad is not None:
        raise ArchiveError(f"{zip_path}: corrupt member {bad}")
    return count


def _gib(n: float) -> str:
    return f"{n / 1024**3:.1f}G"


def ensure_space(
    src_dir: Path, dest_dir: Path, headroom: float = 1.05, skip_rebuildable: bool = True
) -> None:
    """Raise ArchiveError unless dest_dir's filesystem can hold the archive."""
    needed = workspace_size(src_dir, skip_rebuildable) * headroom
    probe = Path(dest_dir)
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < needed:
        raise ArchiveError(f"not enough disk: needs {_gib(needed)}, {_gib(free)} free")


def _check_members(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for name in zf.namelist():
        target = (root / name).resolve()
        if name.startswith(("/", "\\")) or _DRIVE.match(name) or not target.is_relative_to(root):
            raise ArchiveError(f"unsafe member path in archive: {name!r}")


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _restore_link(zf: zipfile.ZipFile, info: zipfile.ZipInfo, root: Path) -> None:
    """Recreate a stored symlink.

    Absolute targets are restored verbatim — they are records of where data
    lived. A relative target must stay inside the extracted tree.
    """
    target = zf.read(info).decode()
    path = root / info.filename.rstrip("/")
    if not os.path.isabs(target):
        resolved = (path.parent / target).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ArchiveError(f"symlink escapes the archive: {info.filename} -> {target}")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def extract_zip(zip_path: Path, dest: Path, *, force: bool = False) -> None:
    """Extract zip_path into dest via a staging dir; refuse non-empty dest unless force."""
    zip_path, dest = Path(zip_path), Path(dest)
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ArchiveError(f"{dest} exists and is not empty; pass --force to replace it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.extract-", dir=dest.parent))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _check_members(zf, staging)
            links = [i for i in zf.infolist() if _is_link(i)]
            zf.extractall(staging, members=[i for i in zf.infolist() if not _is_link(i)])
            for info in links:
                _restore_link(zf, info, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    old = None
    if dest.exists():
        old = dest.with_name(f".{dest.name}.replaced-{os.getpid()}")
        dest.rename(old)
    try:
        staging.rename(dest)
    except BaseException:
        if old is not None:
            old.rename(dest)
        raise
    if old is not None:
        shutil.rmtree(old)


def link_to_cache(zip_path: Path, pointer: Path, cache_root: Path) -> bool:
    """Replace zip_path with a hardlink to its DVC 3 cache object.

    Returns False, leaving zip_path untouched, whenever the link cannot be
    made safely (no md5, directory hash, missing object, size mismatch,
    cross-device link).
    """
    zip_path = Path(zip_path)
    outs = (yaml.safe_load(Path(pointer).read_text()) or {}).get("outs") or []
    md5 = outs[0].get("md5") if outs else None
    if not md5 or md5.endswith(".dir"):
        return False
    obj = Path(cache_root) / "files" / "md5" / md5[:2] / md5[2:]
    if not obj.is_file() or obj.stat().st_size != zip_path.stat().st_size:
        return False
    if os.path.samefile(obj, zip_path):
        return True
    tmp = zip_path.with_name(zip_path.name + ".link")
    tmp.unlink(missing_ok=True)
    try:
        os.link(obj, tmp)
    except OSError:
        return False
    tmp.replace(zip_path)
    return True
