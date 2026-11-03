"""Evidence files of a claim reference (M-G 5.4): confined paths, file lists, hashes and the file limit.

Every path is taken relative to the run folder and resolved inside it; a symbolic link on the way is refused,
never followed; only regular files are read. Callers receive run-relative paths and digests. No value of an
evidence file is returned, printed or put in a message (M-G 5.16).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

MAX_FILES_PER_REF = 200
_CHUNK = 1 << 20


class EvidenceError(ValueError):
    """Evidence for one reference is refused. `rule` is the finding rule id; `message` names a path, not a value."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def path_problem(rel) -> str | None:
    """Why a run-relative path is unacceptable, or None."""
    if not isinstance(rel, str) or not rel.strip():
        return "is empty"
    if rel.startswith(("/", "~")) or "\\" in rel or "\0" in rel:
        return "must be relative to the run"
    if ".." in PurePosixPath(rel).parts:
        return "must not contain '..'"
    return None


def _chain(run_folder, rel: str) -> Path:
    """The path of `rel` under the run; every component that exists is checked to be no symbolic link."""
    cur = Path(run_folder)
    for part in PurePosixPath(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise EvidenceError("ref-unsafe", f"{rel}: a symbolic link is not followed")
    return cur


def _inside(run_folder, path: Path) -> None:
    if not path.resolve(strict=False).is_relative_to(Path(run_folder).resolve()):
        raise EvidenceError("ref-unsafe", f"{path.name} lies outside the run folder")


def regular_file(run_folder, rel: str) -> Path | None:
    """The regular file at run-relative `rel`; None when it does not exist.

    EvidenceError(ref-unsafe) for a bad path, a symbolic link on the way, a path outside the run or a
    name that is not a regular file.
    """
    problem = path_problem(rel)
    if problem:
        raise EvidenceError("ref-unsafe", f"{rel!r} {problem}")
    cur = _chain(run_folder, rel)
    if not os.path.lexists(cur):
        return None
    if not cur.is_file():
        raise EvidenceError("ref-unsafe", f"{rel}: not a regular file")
    _inside(run_folder, cur)
    return cur


def folder_files(run_folder, rel: str) -> list[str] | None:
    """Every regular file below the run-relative folder `rel`, sorted; None when the folder is absent.

    A symbolic link anywhere below it is refused; more than MAX_FILES_PER_REF files is refused.
    """
    problem = path_problem(rel)
    if problem:
        raise EvidenceError("ref-unsafe", f"{rel!r} {problem}")
    cur = _chain(run_folder, rel)
    if not os.path.lexists(cur):
        return None
    if cur.is_symlink() or not cur.is_dir():
        raise EvidenceError("ref-unsafe", f"{rel}: not a folder")
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(cur, followlinks=False):
        base = Path(dirpath)
        for name in dirnames + filenames:
            entry = base / name
            if entry.is_symlink():
                raise EvidenceError("ref-unsafe",
                                    f"{entry.relative_to(run_folder).as_posix()}: a symbolic link is not followed")
        for name in filenames:
            entry = base / name
            if not entry.is_file():
                raise EvidenceError("ref-unsafe", f"{entry.relative_to(run_folder).as_posix()}: not a regular file")
            found.append(entry.relative_to(run_folder).as_posix())
    if len(found) > MAX_FILES_PER_REF:
        raise EvidenceError("ref-unsafe", f"{rel}: more than {MAX_FILES_PER_REF} evidence files")
    return sorted(found)


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def pins_for(run_folder, files) -> dict:
    """The pin of a reference (M-G 5.4): {"files": {run-relative path: sha256}} over its evidence files."""
    out = {}
    for rel in sorted(files):
        path = regular_file(run_folder, rel)
        if path is None:
            raise EvidenceError("ref-unsafe", f"{rel}: evidence vanished while it was pinned")
        out[rel] = sha256_file(path)
    return {"files": out}
