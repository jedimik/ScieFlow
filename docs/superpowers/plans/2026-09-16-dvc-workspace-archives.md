# DVC Workspace Archives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opt-in archive mode for `scripts/dvc_sync.py` — pack a workspace into one uncompressed zip before pushing it to the S3 DVC remote, and unpack it after pulling.

**Architecture:** A new pure-filesystem module `scripts/sflib/archive.py` builds, verifies, extracts, and cache-links zips. `scripts/dvc_sync.py` decides per slug between archive and directory mode, and runs `dvc add --to-remote` / `dvc remove` / `dvc pull` around those helpers. Directory mode keeps its current `dvc` command lines.

**Tech Stack:** Python ≥3.11 stdlib (`zipfile`, `shutil`, `tempfile`), PyYAML, DVC 3.67.1 CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-dvc-workspace-archives-design.md` (as amended 2026-09-16)

## Global Constraints

- Archive location: `workspace/_archives/<slug>.zip`; pointer `workspace/_archives/<slug>.zip.dvc`.
- Zip format: `zipfile.ZIP_STORED`, `allowZip64=True`. No compression.
- Archive mode is opt-in. Default `archive: false` in `config/defaults.yml`.
- Never run `dvc gc`, never delete S3 data, never run `git` from the script.
- Do not change `.dvc/config`. Deduplication is `link_to_cache` only (the spec's 2026-09-16 amendment).
- Pull must refuse a non-empty `workspace/<slug>` unless `--force` is passed.
- Symlinks inside a workspace make `build_zip` refuse; they are never followed.
- `logs/` and `*.log` are archived. Skip only `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, `*.pyc`.
- Tests run offline: `uv run pytest -q`. Never call real `dvc` from a test.

## Notes from pre-plan validation (2026-09-16)

Every code block below was run in a scratch copy of the repo, both task by task (`test_archive.py` + `test_dvc_sync.py`: 10+6 → 22+6 → 22+19 → 22+29 tests passing) and end to end against real DVC 3.67.1 with a local-directory remote. Findings that shaped the code:

- `dvc add --to-remote` writes no `.dvc/cache` object and **no** `.gitignore` in `workspace/_archives/`. The printed git hint therefore stages the directory with `git add -A`, never a guessed `.gitignore` path.
- `dvc remove workspace/<slug>.dvc` deletes `workspace/.gitignore` when DVC created it. The hint includes that file only if it existed before the remove. This repo has none today, because the root `.gitignore` covers `workspace/*`.
- DVC 3's cache layout is `.dvc/cache/files/md5/<2 hex>/<30 hex>`, with objects at mode 444. After `link_to_cache`, the zip and the cache object share one inode and `dvc status` stays clean.
- Re-pushing over a hardlinked, read-only zip works, because `build_zip` renames a new file over it and never writes through the link. The old cache object is left intact.

Deviations from the spec's section 2 signature list, all recorded in the spec amendment: path helpers take `workspace_root` instead of `root`; new `archived_slugs()`; new `workspace_summary()` so `status` output is testable; new `build_parser()` so flags are testable.

---

### Task 1: Archive build and verify

**Files:**
- Create: `scripts/sflib/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `archive.ARCHIVE_DIR: str == "_archives"`
  - `class archive.ArchiveError(Exception)`
  - `archive.archive_path(workspace_root: Path, slug: str) -> Path`
  - `archive.pointer_path(workspace_root: Path, slug: str) -> Path`
  - `archive.archived_slugs(workspace_root: Path) -> list[str]`
  - `archive.workspace_size(src_dir: Path) -> int`
  - `archive.build_zip(src_dir: Path, dest_zip: Path) -> None`
  - `archive.verify_zip(zip_path: Path) -> int`

- [ ] **Step 1: Write the failing tests** — create `tests/test_archive.py`:

```python
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sflib import archive


def make_run(root: Path) -> Path:
    run = root / "run-01"
    (run / "logs").mkdir(parents=True)
    (run / "results" / "nested").mkdir(parents=True)
    (run / "empty").mkdir()
    (run / "__pycache__").mkdir()
    (run / "status.yml").write_text("phase: done\n")
    (run / "logs" / "agent.log").write_text("transcript\n")
    (run / "results" / "nested" / "data.bin").write_bytes(os.urandom(4096))
    (run / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"cache")
    (run / "stray.pyc").write_bytes(b"cache")
    return run


def tree(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


# --- paths -------------------------------------------------------------------

def test_archive_and_pointer_paths(tmp_path):
    ws = tmp_path / "workspace"
    assert archive.archive_path(ws, "run-01") == ws / "_archives" / "run-01.zip"
    assert archive.pointer_path(ws, "run-01") == ws / "_archives" / "run-01.zip.dvc"


def test_archived_slugs_lists_pointers_only(tmp_path):
    ws = tmp_path / "workspace"
    assert archive.archived_slugs(ws) == []
    (ws / "_archives").mkdir(parents=True)
    (ws / "_archives" / "b-run.zip.dvc").write_text("outs: []\n")
    (ws / "_archives" / "a-run.zip.dvc").write_text("outs: []\n")
    (ws / "_archives" / "a-run.zip").write_bytes(b"zip body")
    assert archive.archived_slugs(ws) == ["a-run", "b-run"]


# --- build / verify ----------------------------------------------------------

def test_build_zip_round_trip_skips_cache_keeps_logs(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "out" / "run-01.zip"

    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        zf.extractall(tmp_path / "restored")

    expected = {k: v for k, v in tree(run).items()
                if "__pycache__" not in k and not k.endswith(".pyc")}
    assert tree(tmp_path / "restored") == expected
    assert "logs/agent.log" in expected
    assert "empty" in expected


def test_build_zip_members_are_stored_uncompressed(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}


def test_build_zip_is_deterministic(tmp_path):
    run = make_run(tmp_path)
    archive.build_zip(run, tmp_path / "a.zip")
    archive.build_zip(run, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_build_zip_refuses_symlinks_and_names_them(tmp_path):
    run = make_run(tmp_path)
    (run / "link-to-data").symlink_to(tmp_path)
    with pytest.raises(archive.ArchiveError, match="link-to-data"):
        archive.build_zip(run, tmp_path / "run-01.zip")
    assert not (tmp_path / "run-01.zip").exists()


def test_build_zip_failure_leaves_no_zip_and_no_partial(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"

    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "write", boom)
    with pytest.raises(OSError, match="disk full"):
        archive.build_zip(run, dest)
    assert not dest.exists()
    assert not dest.with_name("run-01.zip.partial").exists()


def test_workspace_size_counts_archived_bytes_only(tmp_path):
    run = make_run(tmp_path)
    expected = len("phase: done\n") + len("transcript\n") + 4096
    assert archive.workspace_size(run) == expected


def test_verify_zip_returns_member_count(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    with zipfile.ZipFile(dest) as zf:
        assert archive.verify_zip(dest) == len(zf.infolist())


def test_verify_zip_rejects_truncated_file(tmp_path):
    run = make_run(tmp_path)
    dest = tmp_path / "run-01.zip"
    archive.build_zip(run, dest)
    dest.write_bytes(dest.read_bytes()[:100])
    with pytest.raises(archive.ArchiveError, match="not a readable zip"):
        archive.verify_zip(dest)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_archive.py -q`
Expected: collection error, `ImportError: cannot import name 'archive' from 'sflib'`

- [ ] **Step 3: Implement** — create `scripts/sflib/archive.py`:

```python
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
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_archive.py -q`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sflib/archive.py tests/test_archive.py
git commit -m "feat(dvc): build and verify uncompressed workspace zips"
```

---

### Task 2: Disk preflight, safe extraction, cache hardlink

**Files:**
- Modify: `scripts/sflib/archive.py` (append)
- Test: `tests/test_archive.py` (append)

**Interfaces:**
- Consumes: `ArchiveError`, `workspace_size`, `build_zip` from Task 1
- Produces:
  - `archive.ensure_space(src_dir: Path, dest_dir: Path, headroom: float = 1.05) -> None` — raises `ArchiveError("not enough disk: needs X.XG, Y.YG free")`
  - `archive.extract_zip(zip_path: Path, dest: Path, *, force: bool = False) -> None`
  - `archive.link_to_cache(zip_path: Path, pointer: Path, cache_root: Path) -> bool`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_archive.py`:

```python
# --- disk preflight ----------------------------------------------------------

def test_ensure_space_refuses_with_both_figures(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 254 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=100 * 1024**3))
    with pytest.raises(archive.ArchiveError, match=r"needs 266\.7G, 100\.0G free"):
        archive.ensure_space(run, tmp_path / "workspace" / "_archives")


def test_ensure_space_passes_with_headroom(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 10 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=11 * 1024**3))
    archive.ensure_space(run, tmp_path / "does" / "not" / "exist")


# --- extract -----------------------------------------------------------------

def test_extract_zip_refuses_non_empty_dest_without_force(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    target = tmp_path / "target"
    target.mkdir()
    (target / "local-only.txt").write_text("keep me")

    with pytest.raises(archive.ArchiveError, match="--force"):
        archive.extract_zip(dest_zip, target)
    assert (target / "local-only.txt").read_text() == "keep me"


def test_extract_zip_force_replaces_dest(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    target = tmp_path / "target"
    target.mkdir()
    (target / "local-only.txt").write_text("stale")

    archive.extract_zip(dest_zip, target, force=True)
    assert not (target / "local-only.txt").exists()
    assert (target / "status.yml").read_text() == "phase: done\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_extract_zip_into_empty_existing_dir_needs_no_force(tmp_path):
    run = make_run(tmp_path)
    dest_zip = tmp_path / "run-01.zip"
    archive.build_zip(run, dest_zip)
    (tmp_path / "target").mkdir()
    archive.extract_zip(dest_zip, tmp_path / "target")
    assert (tmp_path / "target" / "status.yml").exists()


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil.txt", "C:evil.txt"])
def test_extract_zip_rejects_unsafe_member_and_writes_nothing(tmp_path, name):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr(name, "pwned")
    target = tmp_path / "area" / "target"

    with pytest.raises(archive.ArchiveError, match="unsafe member"):
        archive.extract_zip(bad, target)
    assert not target.exists()
    assert not (tmp_path / "area" / "evil.txt").exists()
    assert list((tmp_path / "area").iterdir()) == []


# --- cache hardlink ----------------------------------------------------------

def fake_cache(tmp_path: Path, body: bytes, md5: str = "4d3431208b9bf81bdaa193b71a99af1c"):
    cache = tmp_path / "cache"
    obj = cache / "files" / "md5" / md5[:2] / md5[2:]
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    pointer = tmp_path / "run-01.zip.dvc"
    pointer.write_text(f"outs:\n- md5: {md5}\n  size: {len(body)}\n  hash: md5\n  path: run-01.zip\n")
    zip_path = tmp_path / "run-01.zip"
    zip_path.write_bytes(body)
    return cache, obj, pointer, zip_path


def test_link_to_cache_hardlinks_zip_to_cache_object(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    assert archive.link_to_cache(zip_path, pointer, cache) is True
    assert os.path.samefile(zip_path, obj)
    assert zip_path.read_bytes() == b"archive body"
    assert archive.link_to_cache(zip_path, pointer, cache) is True


def test_link_to_cache_missing_object_leaves_copy(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    obj.unlink()
    assert archive.link_to_cache(zip_path, pointer, cache) is False
    assert zip_path.read_bytes() == b"archive body"


def test_link_to_cache_size_mismatch_leaves_copy(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    obj.write_bytes(b"something else entirely")
    assert archive.link_to_cache(zip_path, pointer, cache) is False
    assert not os.path.samefile(zip_path, obj)


def test_link_to_cache_ignores_directory_hash(tmp_path):
    cache, obj, pointer, zip_path = fake_cache(tmp_path, b"archive body")
    pointer.write_text("outs:\n- md5: 68c1d3127031576cf055cb445c10fb32.dir\n  path: run-01\n")
    assert archive.link_to_cache(zip_path, pointer, cache) is False
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_archive.py -q`
Expected: 12 failures with `AttributeError: module 'sflib.archive' has no attribute 'ensure_space'` / `'extract_zip'` / `'link_to_cache'`; the 10 Task 1 tests still pass.

- [ ] **Step 3: Implement** — append to `scripts/sflib/archive.py`:

```python
def _gib(n: float) -> str:
    return f"{n / 1024**3:.1f}G"


def ensure_space(src_dir: Path, dest_dir: Path, headroom: float = 1.05) -> None:
    """Raise ArchiveError unless dest_dir's filesystem can hold the archive."""
    needed = workspace_size(src_dir) * headroom
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
            zf.extractall(staging)
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
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_archive.py -q`
Expected: `22 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/sflib/archive.py tests/test_archive.py
git commit -m "feat(dvc): safe zip extraction, disk preflight, cache hardlink"
```

---

### Task 3: Mode resolution, slug discovery, status

**Files:**
- Modify: `scripts/dvc_sync.py` — import, `find_workspaces`, `resolve_slugs` (plus new `_clean_slug`), `cmd_track`, new `use_archive`, `cmd_status` (plus new `workspace_summary`)
- Modify: `config/defaults.yml`
- Test: `tests/test_dvc_sync.py` (imports + append)

**Interfaces:**
- Consumes: `archive.ARCHIVE_DIR`, `archive.pointer_path`, `archive.archive_path`, `archive.archived_slugs`; `config.load_run_config(ws: Path, root: Path) -> dict`
- Produces:
  - `dvc_sync.use_archive(slug: str, root: Path, workspace_root: Path, *, command: str, archive_flag: bool | None = None) -> bool` — `command` is `"push"` or `"pull"`
  - `dvc_sync.workspace_summary(workspace_root: Path) -> list[str]`
  - `resolve_slugs(..., all_flag=True)` now returns the sorted union of local run directories and archived slugs

- [ ] **Step 1: Write the failing tests**

In `tests/test_dvc_sync.py`, replace the import block at the top with:

```python
from pathlib import Path
import os
from types import SimpleNamespace

import pytest
import yaml

import dvc_sync
import dvc_setup_s3
from sflib import archive
```

Append to the end of `tests/test_dvc_sync.py`:

```python
@pytest.fixture
def repo(tmp_path):
    """Minimal repo: config/defaults.yml (archive: false) + workspace/."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "defaults.yml").write_text("archive: false\n")
    ws = tmp_path / "workspace"
    ws.mkdir()
    return tmp_path, ws


@pytest.fixture
def calls(monkeypatch):
    """Record dvc argv instead of running it; every command succeeds."""
    recorded = []

    def fake_run_cmd(cmd, cwd):
        recorded.append(cmd)
        return 0

    monkeypatch.setattr(dvc_sync, "run_cmd", fake_run_cmd)
    return recorded


def make_ws(ws: Path, slug: str, run_config: dict | None = None) -> Path:
    run = ws / slug
    (run / "logs").mkdir(parents=True)
    (run / "status.yml").write_text("phase: done\n")
    (run / "logs" / "agent.log").write_text("transcript\n")
    if run_config is not None:
        (run / "config.yml").write_text(yaml.safe_dump(run_config))
    return run


def add_pointer(ws: Path, slug: str) -> Path:
    pointer = archive.pointer_path(ws, slug)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"outs:\n- md5: 00000000000000000000000000000000\n  path: {slug}.zip\n")
    return pointer


def test_find_workspaces_skips_archive_dir(repo):
    _, ws = repo
    make_ws(ws, "run-01")
    (ws / "_archives").mkdir()
    assert dvc_sync.find_workspaces(ws) == ["run-01"]


def test_resolve_slugs_all_includes_pointer_only_slugs(repo):
    _, ws = repo
    make_ws(ws, "run-01")
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.resolve_slugs([], True, ws) == ["run-00-archived", "run-01"]


def test_resolve_slugs_accepts_pointer_only_and_rejects_archive_dir(repo):
    _, ws = repo
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.resolve_slugs(["workspace/run-00-archived"], False, ws) == ["run-00-archived"]
    with pytest.raises(ValueError, match="archive directory"):
        dvc_sync.resolve_slugs(["_archives"], False, ws)


@pytest.mark.parametrize(
    "has_pointer, flag, run_config, expected",
    [
        (False, None, None, False),              # default: directory mode
        (False, None, {"archive": True}, True),   # opted in via config.yml
        (False, None, {"archive": False}, False),
        (False, True, None, True),               # --archive
        (True, None, None, True),                # pointer already exists
        (True, False, None, False),              # --no-archive beats pointer
        (False, False, {"archive": True}, False),  # --no-archive beats config
    ],
)
def test_use_archive_push(repo, has_pointer, flag, run_config, expected):
    root, ws = repo
    make_ws(ws, "run-01", run_config)
    if has_pointer:
        add_pointer(ws, "run-01")
    assert dvc_sync.use_archive("run-01", root, ws, command="push", archive_flag=flag) is expected


def test_use_archive_pull_depends_on_pointer_only(repo):
    root, ws = repo
    make_ws(ws, "run-01", {"archive": True})
    assert dvc_sync.use_archive("run-01", root, ws, command="pull") is False
    add_pointer(ws, "run-01")
    assert dvc_sync.use_archive("run-01", root, ws, command="pull") is True


def test_track_skips_archived_workspace(repo, calls):
    root, ws = repo
    make_ws(ws, "run-01")
    add_pointer(ws, "run-01")
    assert dvc_sync.cmd_track(["run-01"], root, ws) == 0
    assert calls == []


def test_workspace_summary_three_states(repo):
    _, ws = repo
    make_ws(ws, "run-a")
    add_pointer(ws, "run-a")
    make_ws(ws, "run-b")
    (ws / "run-b.dvc").write_text("outs: []\n")
    make_ws(ws, "run-c")
    add_pointer(ws, "run-d")
    archive.archive_path(ws, "run-d").write_bytes(b"zip")

    lines = dvc_sync.workspace_summary(ws)
    assert "[ARCHIVE] zip: not downloaded" in lines[0]
    assert "[TRACKED in DVC]" in lines[1]
    assert "[LOCAL ONLY - NOT TRACKED]" in lines[2]
    assert "[ARCHIVE] zip: present" in lines[3]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_dvc_sync.py -q`
Expected: failures including `AttributeError: module 'dvc_sync' has no attribute 'use_archive'`, and `test_find_workspaces_skips_archive_dir` failing with `['_archives', 'run-01'] != ['run-01']`. The 6 existing tests still pass.

- [ ] **Step 3: Implement in `scripts/dvc_sync.py`**

Change the import line `from sflib import config` to:

```python
from sflib import archive, config
```

Replace `find_workspaces` with:

```python
def find_workspaces(workspace_root: Path) -> list[str]:
    """List valid workspace run slugs in workspace root."""
    if not workspace_root.exists():
        return []
    slugs = []
    for item in sorted(workspace_root.iterdir()):
        if item.is_dir() and not item.name.startswith(".") and item.name != archive.ARCHIVE_DIR:
            slugs.append(item.name)
    return slugs
```

Replace `resolve_slugs` with these two functions:

```python
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
```

Replace `cmd_track` with:

```python
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
```

Insert directly above `def cmd_push`:

```python
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
```

Replace `cmd_status` with these two functions:

```python
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
```

- [ ] **Step 4: Add the config key** — in `config/defaults.yml`, append after the `claim_check` block:

```yaml
archive: false                # true = push/pull this run as a single zip
                              # (docs/DVC_STORAGE.md, archive mode). Opt in per
                              # run in workspace/<slug>/config.yml once the run
                              # is finished: it trades DVC's per-file dedup for
                              # far fewer S3 objects.
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_dvc_sync.py tests/test_config.py -q`
Expected: all pass. `test_dvc_sync.py` alone has 19 tests: 6 existing + 13 new, counting the 7 parametrized cases.

- [ ] **Step 6: Commit**

```bash
git add scripts/dvc_sync.py config/defaults.yml tests/test_dvc_sync.py
git commit -m "feat(dvc): resolve archive vs directory mode per workspace"
```

---

### Task 4: Archive push and pull, CLI flags

**Files:**
- Modify: `scripts/dvc_sync.py` — docstring; new `_remote_args`, `push_archive`, `pull_archive`; existing `cmd_push` renamed to `_push_directories` and `cmd_pull` to `_pull_directories`, both taking `remote`; new `cmd_push`, `cmd_pull`, `build_parser`; `main` rewritten
- Test: `tests/test_dvc_sync.py` (append)

**Interfaces:**
- Consumes: Task 2's `ensure_space`, `build_zip`, `verify_zip`, `extract_zip`, `link_to_cache`; Task 3's `use_archive`
- Produces:
  - `dvc_sync.cmd_push(slugs, root, workspace_root, *, archive_flag: bool | None = None, keep_zip: bool = False, remote: str | None = None) -> int`
  - `dvc_sync.cmd_pull(slugs, root, workspace_root, *, force: bool = False, remote: str | None = None) -> int`
  - `dvc_sync.push_archive(slug, root, workspace_root, *, keep_zip=False, remote=None) -> int`
  - `dvc_sync.pull_archive(slug, root, workspace_root, *, force=False, remote=None) -> int`
  - `dvc_sync.build_parser(default_env_path: Path) -> argparse.ArgumentParser`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_dvc_sync.py`:

```python
def test_push_archive_uploads_removes_zip_and_old_pointer(repo, calls, capsys):
    root, ws = repo
    make_ws(ws, "run-01")
    (ws / "run-01.dvc").write_text("outs: []\n")

    rc = dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True)

    assert rc == 0
    assert calls == [
        ["dvc", "add", "--to-remote", "workspace/_archives/run-01.zip"],
        ["dvc", "remove", "workspace/run-01.dvc"],
    ]
    assert not archive.archive_path(ws, "run-01").exists()
    out = capsys.readouterr().out
    assert "git add -A workspace/_archives workspace/run-01.dvc\n" in out


def test_push_archive_keep_zip_and_remote(repo, calls):
    root, ws = repo
    make_ws(ws, "run-01")

    rc = dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True, keep_zip=True, remote="alt")

    assert rc == 0
    assert calls == [["dvc", "add", "--to-remote", "-r", "alt", "workspace/_archives/run-01.zip"]]
    assert archive.verify_zip(archive.archive_path(ws, "run-01")) > 0


def test_push_archive_upload_failure_keeps_zip(repo, monkeypatch):
    root, ws = repo
    make_ws(ws, "run-01")
    monkeypatch.setattr(dvc_sync, "run_cmd", lambda cmd, cwd: 3)

    assert dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True) == 3
    assert archive.archive_path(ws, "run-01").exists()


def test_push_archive_disk_preflight_refuses(repo, calls, monkeypatch, capsys):
    root, ws = repo
    make_ws(ws, "run-01")
    monkeypatch.setattr(archive, "workspace_size", lambda _src: 254 * 1024**3)
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: SimpleNamespace(free=100 * 1024**3))

    assert dvc_sync.cmd_push(["run-01"], root, ws, archive_flag=True) == 1
    assert calls == []
    assert "needs 266.7G, 100.0G free" in capsys.readouterr().out


def test_push_archive_skips_pointer_only_slug(repo, calls):
    root, ws = repo
    add_pointer(ws, "run-00-archived")
    assert dvc_sync.cmd_push(["run-00-archived"], root, ws) == 0
    assert calls == []


def test_push_mixed_batch_keeps_directory_argv(repo, calls):
    root, ws = repo
    make_ws(ws, "run-01", {"archive": True})
    make_ws(ws, "run-02")
    (ws / "run-02.dvc").write_text("outs: []\n")

    assert dvc_sync.cmd_push(["run-01", "run-02"], root, ws) == 0
    assert calls == [
        ["dvc", "add", "--to-remote", "workspace/_archives/run-01.zip"],
        ["dvc", "push", "workspace/run-02.dvc"],
    ]


def test_pull_archive_extracts_and_keeps_zip(repo, monkeypatch, capsys):
    root, ws = repo
    source = make_ws(root / "elsewhere", "run-01")
    zip_path = archive.archive_path(ws, "run-01")
    add_pointer(ws, "run-01")
    recorded = []

    def fake_pull(cmd, cwd):  # dvc pull materializes the zip
        recorded.append(cmd)
        archive.build_zip(source, zip_path)
        return 0

    monkeypatch.setattr(dvc_sync, "run_cmd", fake_pull)

    assert dvc_sync.cmd_pull(["run-01"], root, ws) == 0
    assert recorded == [["dvc", "pull", "workspace/_archives/run-01.zip.dvc"]]
    assert (ws / "run-01" / "logs" / "agent.log").read_text() == "transcript\n"
    assert zip_path.exists()
    assert "could not hardlink" in capsys.readouterr().out  # no cache object in tmp repo


def test_pull_archive_refuses_non_empty_dir_without_force(repo, monkeypatch):
    root, ws = repo
    source = make_ws(root / "elsewhere", "run-01")
    make_ws(ws, "run-01")
    (ws / "run-01" / "local-only.txt").write_text("keep me")
    add_pointer(ws, "run-01")
    monkeypatch.setattr(
        dvc_sync, "run_cmd",
        lambda cmd, cwd: archive.build_zip(source, archive.archive_path(ws, "run-01")) or 0,
    )

    assert dvc_sync.cmd_pull(["run-01"], root, ws) == 1
    assert (ws / "run-01" / "local-only.txt").exists()
    assert dvc_sync.cmd_pull(["run-01"], root, ws, force=True) == 0
    assert not (ws / "run-01" / "local-only.txt").exists()


def test_pull_directory_argv_unchanged(repo, calls):
    root, ws = repo
    (ws / "run-02.dvc").write_text("outs: []\n")
    assert dvc_sync.cmd_pull(["run-02"], root, ws) == 0
    assert calls == [["dvc", "pull", "workspace/run-02.dvc"]]


def test_parser_archive_flags():
    parser = dvc_sync.build_parser(Path(".env"))
    assert parser.parse_args(["push", "run-01"]).archive is None
    assert parser.parse_args(["push", "run-01", "--archive"]).archive is True
    assert parser.parse_args(["push", "run-01", "--no-archive"]).archive is False
    with pytest.raises(SystemExit):
        parser.parse_args(["push", "run-01", "--archive", "--no-archive"])
    pulled = parser.parse_args(["pull", "--all", "--force", "--remote", "alt"])
    assert (pulled.force, pulled.remote) == (True, "alt")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_dvc_sync.py -q`
Expected: new tests fail with `TypeError: cmd_push() got an unexpected keyword argument 'archive_flag'`, `TypeError: cmd_pull() got an unexpected keyword argument 'force'`, or `AttributeError: ... 'build_parser'`. `test_pull_directory_argv_unchanged` already passes, which is intended: it guards against regressions.

- [ ] **Step 3: Implement in `scripts/dvc_sync.py`**

Replace the `Usage:` part of the module docstring with:

```text
Archive mode (opt-in per workspace, see docs/DVC_STORAGE.md) packs a run
into workspace/_archives/<slug>.zip before push and unpacks it after pull.

Usage:
    uv run scripts/dvc_sync.py track [SLUG ...] [--all]
    uv run scripts/dvc_sync.py push [SLUG ...] [--all] [--archive | --no-archive] [--keep-zip] [--remote NAME]
    uv run scripts/dvc_sync.py pull [SLUG ...] [--all] [--force] [--remote NAME]
    uv run scripts/dvc_sync.py status
```

Replace the existing `cmd_push` function (the directory-mode one) with the following functions, in this order:

```python
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
    exit_code = 0
    for slug in archived:
        rc = push_archive(slug, root, workspace_root, keep_zip=keep_zip, remote=remote)
        exit_code = exit_code or rc
    if directories or not archived:
        rc = _push_directories(directories, root, workspace_root, remote)
        exit_code = exit_code or rc
    return exit_code
```

Replace the existing `cmd_pull` function with:

```python
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
```

Replace `main` with:

```python
def build_parser(default_env_path: Path) -> argparse.ArgumentParser:
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
    p_pull = sub.add_parser("pull", help="Pull workspace data from remote storage")
    p_pull.add_argument("slugs", nargs="*", help="Workspace slug(s)")
    p_pull.add_argument("--all", action="store_true", help="Pull all workspaces")
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

    slugs = resolve_slugs(args.slugs, args.all, workspace_root)

    if args.command == "track":
        sys.exit(cmd_track(slugs, root, workspace_root))
    elif args.command == "push":
        sys.exit(cmd_push(slugs, root, workspace_root, archive_flag=args.archive,
                          keep_zip=args.keep_zip, remote=args.remote))
    elif args.command == "pull":
        sys.exit(cmd_pull(slugs, root, workspace_root, force=args.force, remote=args.remote))
```

- [ ] **Step 4: Run the full suite and confirm it passes**

Run: `uv run pytest -q`
Expected: all pass. `test_dvc_sync.py` has 29 tests (10 added in this task). Whole suite: the 203 passing before this plan + 22 in `test_archive.py` + 23 new in `test_dvc_sync.py` = 248. Do not treat the exact total as the goal; zero failures is.

- [ ] **Step 5: Check the CLI wiring**

Run: `uv run scripts/dvc_sync.py push --help && uv run scripts/dvc_sync.py pull --help && uv run scripts/dvc_sync.py status`
Expected: help lists `--archive`, `--no-archive`, `--keep-zip`, `--remote` for push and `--force`, `--remote` for pull. `status` prints the workspace summary with no Python traceback. `dvc status` itself may report changes; that is repo state, not a failure.

- [ ] **Step 6: Commit**

```bash
git add scripts/dvc_sync.py tests/test_dvc_sync.py
git commit -m "feat(dvc): zip archive push and pull for opted-in workspaces"
```

---

### Task 5: Docs, ignore-rule check, real-DVC smoke test

**Files:**
- Modify: `docs/DVC_STORAGE.md`
- Verify (no edit expected): `.gitignore`, `.dvcignore`

**Interfaces:**
- Consumes: the CLI from Task 4
- Produces: user documentation

- [ ] **Step 1: Verify ignore rules**

Run:
```bash
mkdir -p workspace/_archives
touch workspace/_archives/probe.zip workspace/_archives/probe.zip.dvc
git check-ignore -v workspace/_archives/probe.zip; echo "zip rc=$?"
git check-ignore -v workspace/_archives/probe.zip.dvc; echo "pointer rc=$?"
rm workspace/_archives/probe.zip workspace/_archives/probe.zip.dvc
rmdir workspace/_archives
```
Expected: the zip is ignored (`.gitignore:…:workspace/*`, `zip rc=0`), and the pointer is **not** ignored (no output, `pointer rc=1`). If either result differs, stop and report it. Do not edit `.gitignore` without asking.

`.dvcignore` has no `workspace` or `_archives` pattern, so DVC can see `workspace/_archives/`. Confirm with `grep -n "workspace\|_archives" .dvcignore` (expect no output).

- [ ] **Step 2: Document archive mode** — in `docs/DVC_STORAGE.md`, insert this section after section 3.4 and before `## 4. Cache & Ignore Policies`:

```markdown
### 3.5 Archive Mode (single zip per workspace)

Large finished runs with many small files transfer much faster as one S3
object. Archive mode packs `workspace/<slug>` into
`workspace/_archives/<slug>.zip` (uncompressed, Zip64) and tracks that zip
instead of the directory.

**Trade-off.** DVC deduplicates per file. An archive is one blob, so changing
any file re-uploads the whole run. Use archive mode for finished runs, not
runs you are still iterating on.

**Opt in** with a flag on the first push, or in the run's config:
```bash
uv run scripts/dvc_sync.py push 2026-09-job1-posthoc-wta --archive
```
```yaml
# workspace/<slug>/config.yml
archive: true
```
After the first archive push, the pointer `workspace/_archives/<slug>.zip.dvc`
decides the mode on its own; later `push` and `pull` need no flag.

**Push** checks free disk (the workspace size + 5%), builds the zip, uploads it
with `dvc add --to-remote` (no local cache copy), deletes the local zip
(`--keep-zip` keeps it), and replaces any old `workspace/<slug>.dvc` pointer
with `dvc remove`. Old per-file data in S3 is left untouched. The command
prints the `git add -A …` line to run; it never commits.

**Pull** downloads the zip, verifies it, extracts it into `workspace/<slug>`,
and keeps the zip, hardlinked to its DVC cache object so it takes no extra
disk. If `workspace/<slug>` already has content, pull refuses; rerun with
`--force` to replace it.

**Back to directory mode:** `push <slug> --no-archive`.

**Limits.** Building an archive needs free disk equal to the workspace size.
Symlinks inside a workspace are refused, not followed.
```

- [ ] **Step 3: Smoke test against real DVC** (manual, scratch directory, local remote; never the S3 remote)

```bash
S=$(mktemp -d) && cd "$S" && git init -q && dvc init -q
mkdir remote && dvc remote add -q -d local "$S/remote"
cp -r ~/Github/ScieFlow/scripts ~/Github/ScieFlow/config .
mkdir -p workspace/run-01/logs && echo "phase: done" > workspace/run-01/status.yml
head -c 200000 /dev/urandom > workspace/run-01/logs/big.bin
printf 'workspace/*\n!workspace/**/*.dvc\n' > .gitignore
dvc add -q workspace/run-01 && dvc push -q
uv run --project ~/Github/ScieFlow python scripts/dvc_sync.py push run-01 --archive
ls workspace/_archives            # expect only run-01.zip.dvc
ls workspace/run-01.dvc           # expect: No such file
mv workspace/run-01 run-01.orig
uv run --project ~/Github/ScieFlow python scripts/dvc_sync.py pull run-01
cmp run-01.orig/logs/big.bin workspace/run-01/logs/big.bin && echo CONTENT-OK
stat -c '%h %i' workspace/_archives/run-01.zip   # expect link count 2
uv run --project ~/Github/ScieFlow python scripts/dvc_sync.py pull run-01; echo "rc=$?"   # expect refusal, rc=1
```
Expected: every `expect` holds, and `CONTENT-OK` is printed.

- [ ] **Step 4: Commit**

```bash
git add docs/DVC_STORAGE.md
git commit -m "docs(dvc): document archive mode"
```

---

## Out of scope (from spec section 6)

Streaming or split archives; `dvc gc --cloud` or any S3 deletion; compression tuning; changes to `scripts/remote/`; archiving `vendors/`; committing the currently untracked `.dvc/config` (a separate decision, flagged in the spec).
