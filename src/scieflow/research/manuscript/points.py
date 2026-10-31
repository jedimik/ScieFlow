"""Provenance points (M-G 5.9, M-G-D12): a pinned copy of the manuscript's source tree.

`add` copies exactly the files `tex.source_tree_hash` counts (the same exclusions) into
revision/points/<label>/files/ and writes point.json. `--from-package` (refused inside a dispatch) takes the
source.zip of report/submission/ once its sha256 matches package.json; every entry name, link and size is
checked before anything is written. Messages name files and rule ids, never a value of a file (M-G 5.16).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from scieflow.core import events
from scieflow.integrations.scite.mcp import in_dispatch
from scieflow.research.journal import state
from scieflow.research.manuscript import tex

VERSION = 1
LABEL_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,39}")
MAX_FILES = 200
MAX_BYTES = 50 * 1024 * 1024
SUBMISSION = Path("report") / "submission"
PACKAGE_NAME = "package.json"
ZIP_NAME = "source.zip"
EXCERPT = 60


class PointError(ValueError):
    """A point is refused: `rule` names the refusal, `message` says why (no values)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def _excerpt(text: str) -> str:
    return text if len(text) <= EXCERPT else text[:EXCERPT - 3] + "..."


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows_sha(digests: dict) -> str:
    """The same digest as tex.source_tree_hash: sorted `<sha256>  <relpath>` lines, newline-terminated."""
    rows = [f"{digest}  {rel}" for rel, digest in digests.items()]
    text = "\n".join(sorted(rows))
    return hashlib.sha256((text + "\n" if rows else "").encode("utf-8")).hexdigest()


def _json(record: dict) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def check_label(label) -> str:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise PointError("point-label", "the label must be 1 to 40 characters of a-z, 0-9, '.' or '-', "
                                        "starting with a letter or digit")
    return label


def points_dir(run) -> Path:
    return Path(run) / "revision" / "points"


def point_dir(run, label: str) -> Path:
    return points_dir(run) / label


def _refuse_links(*parts: Path) -> None:
    for part in parts:
        if part.is_symlink():
            raise PointError("symlink", f"{part.name} is a symbolic link; refused")


def source_files(folder) -> dict:
    """Run-relative path -> file, for every file tex.source_tree_hash counts under `folder`.

    A symbolic link anywhere below the folder is refused (never followed); more than MAX_FILES files or
    MAX_BYTES bytes are refused.
    """
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise PointError("manuscript-missing", "no manuscript folder in this run")
    chosen: dict = {}
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(dirpath)
        for name in dirnames + filenames:
            entry = base / name
            if entry.is_symlink():
                raise PointError("symlink", f"{entry.relative_to(folder).as_posix()} is a symbolic link; refused")
        for name in filenames:
            path = base / name
            if not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            if rel.split("/")[0] in tex.TREE_EXCLUDED_DIRS or ".bak-" in path.name:
                continue
            if path.name.endswith(tex.TEX_BYPRODUCTS):
                continue
            chosen[rel] = path
    if len(chosen) > MAX_FILES:
        raise PointError("point-limit", f"more than {MAX_FILES} files in the source tree; refused")
    if sum(path.stat().st_size for path in chosen.values()) > MAX_BYTES:
        raise PointError("point-limit", f"the source tree is over {MAX_BYTES} bytes; refused")
    return dict(sorted(chosen.items()))


def _write_point(run: Path, label: str, payloads: dict, record: dict) -> dict:
    target = point_dir(run, label)
    _refuse_links(run / "revision", points_dir(run))
    if target.exists() or target.is_symlink():
        raise PointError("point-exists", f"point {label} already exists; choose another label")
    points_dir(run).mkdir(parents=True, exist_ok=True)
    staging = points_dir(run) / f".{label}.{secrets.token_hex(8)}.tmp"
    try:
        staging.mkdir()
        (staging / "files").mkdir()
        for rel, data in sorted(payloads.items()):
            state.atomic_write(staging / "files" / rel, data)
        state.atomic_write(staging / "point.json", _json(record))
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    events.emit(run, "note.manuscript_point", actor="agent", label=label, origin=record["origin"],
                files=len(payloads), tree_sha256=record["tree_sha256"])
    return record


def add(run, label: str, *, from_package: bool = False) -> dict:
    """Pin the current source tree (or, with `from_package`, the package's source.zip) as point `label`."""
    run = Path(run)
    if from_package and in_dispatch():
        raise PointError("dispatch", "refused: a point from a package is added by the coordinator; sub-agents "
                                     "never do (SCIEFLOW_DISPATCH is set)")
    check_label(label)
    if point_dir(run, label).exists():
        raise PointError("point-exists", f"point {label} already exists; choose another label")
    if from_package:
        return _add_package(run, label)
    chosen = source_files(run / "manuscript")
    payloads = {rel: path.read_bytes() for rel, path in chosen.items()}
    digests = {rel: _sha(data) for rel, data in payloads.items()}
    record = {"version": VERSION, "label": label, "origin": "current", "tree_sha256": _rows_sha(digests),
              "files": digests}
    return _write_point(run, label, payloads, record)


def _regular(run: Path, path: Path) -> bytes:
    rel = path.relative_to(run).as_posix()
    if path.is_symlink():
        raise PointError("symlink", f"{rel} is a symbolic link; refused")
    if not path.is_file():
        raise PointError("package-missing", f"{rel} is missing: package the manuscript first")
    if not path.resolve().is_relative_to(run.resolve()):
        raise PointError("confined", f"{rel} lies outside the run folder; refused")
    if path.stat().st_size > MAX_BYTES:
        raise PointError("point-limit", f"{rel} is over {MAX_BYTES} bytes; refused")
    return path.read_bytes()


def _entry_name_ok(name: str) -> None:
    unsafe = (not name or name.startswith("/") or name.endswith("/") or "\\" in name or "\0" in name
              or re.match(r"^[A-Za-z]:", name) is not None
              or any(part in ("", ".", "..") for part in name.split("/")))
    if unsafe:
        raise PointError("zip-name", f"an archive entry name is not a safe relative path; refused: "
                                     f"{_excerpt(name)!r}")


def _read_zip(data: bytes) -> dict:
    """Every entry of a source archive, after all names, links and sizes are checked (nothing is written)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PointError("package-invalid", "source.zip is not a valid archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise PointError("point-limit", f"more than {MAX_FILES} entries in source.zip; refused")
        seen: set = set()
        total = 0
        for info in infos:
            _entry_name_ok(info.filename)
            if info.filename in seen:
                raise PointError("zip-name", "a duplicate archive entry name; refused")
            seen.add(info.filename)
            kind = (info.external_attr >> 16) & 0o170000
            if kind == stat.S_IFLNK:
                raise PointError("symlink", f"{_excerpt(info.filename)!r} is a symbolic link entry; refused")
            if kind not in (0, stat.S_IFREG):
                raise PointError("zip-name", f"{_excerpt(info.filename)!r} is not a regular file; refused")
            total += info.file_size
        if total > MAX_BYTES:
            raise PointError("point-limit", f"the entries of source.zip are over {MAX_BYTES} bytes; refused")
        payloads: dict = {}
        for info in infos:
            with archive.open(info) as handle:
                payload = handle.read(info.file_size + 1)
            if len(payload) != info.file_size:
                raise PointError("package-invalid", "an archive entry does not match its declared size")
            payloads[info.filename] = payload
        return payloads


def _add_package(run: Path, label: str) -> dict:
    sub = run / SUBMISSION
    _refuse_links(run / "report", sub)
    if not sub.is_dir():
        raise PointError("package-missing", "no report/submission folder: package the manuscript first")
    try:
        manifest = json.loads(_regular(run, sub / PACKAGE_NAME).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PointError("package-invalid", "package.json is not valid JSON") from exc
    pinned = (manifest.get("files") or {}).get(ZIP_NAME) if isinstance(manifest, dict) else None
    if not isinstance(pinned, str):
        raise PointError("package-invalid", "package.json does not pin source.zip")
    data = _regular(run, sub / ZIP_NAME)
    package_sha = _sha(data)
    if package_sha != pinned:
        raise PointError("package-digest", "source.zip does not match the digest in package.json; refused")
    payloads = _read_zip(data)
    digests = {rel: _sha(payload) for rel, payload in sorted(payloads.items())}
    record = {"version": VERSION, "label": label, "origin": "package", "package_sha256": package_sha,
              "tree_sha256": _rows_sha(digests), "files": digests}
    return _write_point(run, label, payloads, record)


def read_point(run, label: str) -> dict:
    check_label(label)
    path = point_dir(run, label) / "point.json"
    if path.is_symlink() or not path.is_file():
        raise PointError("point-unknown", f"no point {label!r} in this run")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PointError("point-invalid", f"point {label!r} has an unreadable point.json") from exc


def list_points(run) -> list:
    folder = points_dir(run)
    if not folder.is_dir():
        return []
    _refuse_links(folder)
    records = []
    for child in sorted(folder.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if child.is_symlink():
            raise PointError("symlink", f"{child.name} is a symbolic link; refused")
        records.append(read_point(run, child.name))
    return records
