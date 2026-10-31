"""`scieflow manuscript diff` (M-G 5.9, M-G-D12): the changes between two provenance points, or a point and now.

Writes report/diff/<from>..<to>/changes.json (added, removed, changed with both digests) and diff.txt
(difflib unified diff, n=3, comments kept). When latexdiff is on the minimal PATH, both sides are flattened by
ScieFlow (\\input followed through tex.read_tree) into scratch/manuscript-diff/{old,new}.tex and latexdiff runs
on those two files only: fixed argv, no shell, a minimal environment, a timeout and capped output. Without
latexdiff the note "latexdiff-not-installed" is given and the exit is still zero. Nothing here prints a value
of a file; the event carries counts only.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from scieflow.core import events
from scieflow.research.journal import state
from scieflow.research.manuscript import points, tex

VERSION = 1
CURRENT = "current"
MINIMAL_PATH = "/usr/bin:/bin"
LATEXDIFF_ARGV = ["latexdiff", "--encoding=utf8", "old.tex", "new.tex"]
LATEXDIFF_TIMEOUT = 120
LATEXDIFF_STDOUT_CAP = 20 * 1024 * 1024
LATEXDIFF_STDERR_BYTES = 64 * 1024
OUT_NAME = "latexdiff.out"
ERR_NAME = "latexdiff.err"
STDERR_LINES = 20
EXCERPT = 60
NOT_INSTALLED = "latexdiff-not-installed"


class DiffError(ValueError):
    """A diff is refused: `rule` names the refusal, `message` says why (no values)."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class DiffResult:
    folder: str
    from_label: str
    to_label: str
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    latexdiff: bool = False

    def to_dict(self) -> dict:
        return {"from": self.from_label, "to": self.to_label, "folder": self.folder, "added": self.added,
                "removed": self.removed, "changed": self.changed, "notes": self.notes,
                "latexdiff": self.latexdiff}


@dataclass
class _Side:
    label: str
    folder: Path
    digests: dict


def _digests(folder: Path) -> dict:
    return {rel: hashlib.sha256(path.read_bytes()).hexdigest() for rel, path in points.source_files(folder).items()}


def _side(run: Path, label: str) -> _Side:
    if label == CURRENT:
        folder = run / "manuscript"
        return _Side(label, folder, _digests(folder))
    record = points.read_point(run, label)
    folder = points.point_dir(run, label) / "files"
    if not folder.is_dir():
        raise DiffError("point-tampered", f"point {label} has no files folder; refused")
    digests = _digests(folder)
    if digests != dict(record["files"]):
        raise DiffError("point-tampered", f"point {label} does not match its point.json: a file was changed, "
                                          "added or removed after it was pinned; refused")
    return _Side(label, folder, digests)


def _flatten(side: _Side, main: str, *, keep_comments: bool) -> list:
    try:
        tree = tex.read_tree(side.folder, main=main, keep_comments=keep_comments)
    except tex.TexError as exc:
        raise DiffError("flatten", f"{side.label}: {exc.message}") from exc
    return [line.text for line in tree.lines]


def _latexdiff_binary() -> str | None:
    return shutil.which("latexdiff", path=MINIMAL_PATH)


def _latexdiff(run: Path, old_lines: list, new_lines: list) -> bytes:
    scratch = run / "scratch" / "manuscript-diff"
    for part in (run / "scratch", scratch):
        if part.is_symlink():
            raise DiffError("symlink", f"{part.name} is a symbolic link; refused")
    scratch.mkdir(parents=True, exist_ok=True)
    state.atomic_write(scratch / "old.tex", "\n".join(old_lines).encode("utf-8"))
    state.atomic_write(scratch / "new.tex", "\n".join(new_lines).encode("utf-8"))
    env = {"PATH": MINIMAL_PATH, "HOME": str(scratch)}
    out_path = scratch / OUT_NAME
    err_path = scratch / ERR_NAME
    try:
        # stdout and stderr are files, not pipes: nothing is held in memory. The cap is checked on the file after
        # the run; while it runs, its growth is bounded by the timeout only.
        with out_path.open("wb") as out, err_path.open("wb") as err:
            try:
                done = subprocess.run(LATEXDIFF_ARGV, cwd=scratch, env=env, timeout=LATEXDIFF_TIMEOUT,
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False)
            except subprocess.TimeoutExpired as exc:
                raise DiffError("latexdiff-timeout", f"latexdiff ran past {LATEXDIFF_TIMEOUT} s; refused") from exc
            except OSError as exc:
                raise DiffError("latexdiff-failed", "latexdiff could not be started") from exc
        if done.returncode != 0:
            shown = [line[:EXCERPT] for line in _stderr_tail(err_path)]
            raise DiffError("latexdiff-failed", "latexdiff failed; last stderr lines:\n" + "\n".join(shown))
        if out_path.stat().st_size > LATEXDIFF_STDOUT_CAP:
            raise DiffError("latexdiff-output", "latexdiff output is over 20 MB; refused")
        return out_path.read_bytes()
    finally:
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)


def _stderr_tail(path: Path) -> list:
    """The last STDERR_LINES lines of a stderr file, reading at most LATEXDIFF_STDERR_BYTES from its end."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - LATEXDIFF_STDERR_BYTES))
        data = handle.read()
    return data.decode("utf-8", "replace").splitlines()[-STDERR_LINES:]


def diff(run, from_label: str, to_label: str = CURRENT, *, main: str = "main.tex") -> DiffResult:
    """Compare provenance point `from_label` with point `to_label` (or the current tree) and write the outputs."""
    run = Path(run)
    if from_label == CURRENT:
        raise DiffError("from-current", "--from names a provenance point; the current tree is --to")
    points.check_label(from_label)
    if to_label != CURRENT:
        points.check_label(to_label)
    old = _side(run, from_label)
    new = _side(run, to_label)

    added = sorted(set(new.digests) - set(old.digests))
    removed = sorted(set(old.digests) - set(new.digests))
    changed = [{"path": rel, "from_sha256": old.digests[rel], "to_sha256": new.digests[rel]}
               for rel in sorted(set(old.digests) & set(new.digests)) if old.digests[rel] != new.digests[rel]]

    old_lines = _flatten(old, main, keep_comments=True)
    new_lines = _flatten(new, main, keep_comments=True)
    unified = difflib.unified_diff(old_lines, new_lines, fromfile=from_label, tofile=to_label, n=3, lineterm="")
    diff_text = "".join(line + "\n" for line in unified)

    name = f"{from_label}..{to_label}"
    out = run / "report" / "diff" / name
    for part in (run / "report", run / "report" / "diff", out):
        if part.is_symlink():
            raise DiffError("symlink", f"{part.name} is a symbolic link; refused")

    # latexdiff runs before anything in report/ is written, so a refused latexdiff leaves no partial report
    result = DiffResult(folder=f"report/diff/{name}", from_label=from_label, to_label=to_label, added=added,
                        removed=removed, changed=changed)
    latexdiff_bytes = None
    if _latexdiff_binary() is None:
        result.notes.append(NOT_INSTALLED)
    else:
        old_plain = _flatten(old, main, keep_comments=False)
        new_plain = _flatten(new, main, keep_comments=False)
        latexdiff_bytes = _latexdiff(run, old_plain, new_plain)
        result.latexdiff = True

    changes = {"version": VERSION, "from": from_label, "to": to_label, "added": added, "removed": removed,
               "changed": changed}
    state.atomic_write(out / "changes.json", (json.dumps(changes, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    state.atomic_write(out / "diff.txt", diff_text.encode("utf-8"))
    stale = out / "diff.tex"
    if latexdiff_bytes is None:
        if stale.is_file() and not stale.is_symlink():
            stale.unlink()
    else:
        state.atomic_write(stale, latexdiff_bytes)

    events.emit(run, "note.manuscript_diff", actor="agent", from_label=from_label, to_label=to_label,
                added=len(added), removed=len(removed), changed=len(changed), latexdiff=result.latexdiff)
    return result
