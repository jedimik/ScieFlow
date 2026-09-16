"""Zip archives for DVC workspace push/pull (docs/DVC_STORAGE.md, archive mode).

Pure filesystem helpers: no dvc invocation, no network.
"""

import fnmatch
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

ARCHIVE_DIR = "_archives"

# Cache noise mirrored from .dvcignore. Logs are deliberately NOT skipped:
# workspace/<slug>/logs/ holds agent prompts and transcripts (AGENTS.md rule 2).
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
_SKIP_FILES = ("*.pyc",)
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


def _collect(src_dir: Path) -> tuple[list[Path], list[Path]]:
    """Directories and files to archive, in deterministic order.

    Raises ArchiveError listing every symlink: ZIP cannot store them, and
    dereferencing one that points into a data mount could multiply the run.
    """
    dirs: list[Path] = []
    files: list[Path] = []
    symlinks: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        base = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            path = base / name
            if path.is_symlink():
                symlinks.append(path)
            elif name not in _SKIP_DIRS:
                kept.append(name)
                dirs.append(path)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                symlinks.append(path)
            elif not any(fnmatch.fnmatch(name, pat) for pat in _SKIP_FILES):
                files.append(path)
    if symlinks:
        listed = ", ".join(str(p) for p in symlinks)
        raise ArchiveError(f"symlinks cannot be archived: {listed}")
    return dirs, files


def workspace_size(src_dir: Path) -> int:
    """Bytes that build_zip would store for src_dir."""
    _, files = _collect(Path(src_dir))
    return sum(f.stat().st_size for f in files)


def build_zip(src_dir: Path, dest_zip: Path) -> None:
    """Pack src_dir into dest_zip (ZIP_STORED, Zip64), atomically."""
    src_dir, dest_zip = Path(src_dir), Path(dest_zip)
    dirs, files = _collect(src_dir)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    partial = dest_zip.with_name(dest_zip.name + ".partial")
    try:
        with zipfile.ZipFile(
            partial, "w", zipfile.ZIP_STORED, allowZip64=True, strict_timestamps=False
        ) as zf:
            for d in dirs:
                zf.write(d, d.relative_to(src_dir).as_posix() + "/")
            for f in files:
                zf.write(f, f.relative_to(src_dir).as_posix())
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
