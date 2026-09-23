"""Artifact access confined to one run directory.

The browser may read anything a run produced and nothing else. Every path
is resolved (which also follows symlinks) and then checked to be a strict
descendant of the run — so `..`, an absolute path and a symlink pointing out
of the workspace are all refused by the same test.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

MAX_INLINE = 200_000        # bytes of a text file rendered into a page
TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".log", ".err", ".json", ".jsonl", ".yml", ".yaml",
    ".csv", ".tex", ".bib", ".py", ".sh", ".toml", ".cfg", ".ini",
})

# Types the browser may be told to render inline. Deliberately excludes
# image/svg+xml: an SVG can carry a <script> element and would execute
# same-origin if shown inline, exactly like HTML or JS would.
INLINE_SAFE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
})


def resolve(ws: Path, relpath: str, allow_root: bool = False) -> Path:
    """The absolute path of `relpath` inside `ws`, or ValueError.

    Resolution happens first (normalising `..` and following symlinks),
    and containment is then checked on the *resolved* path against the
    *resolved* run root — never on the raw string. That ordering is what
    keeps a symlink whose target lies outside the run from being trusted:
    resolving it first exposes where it really points before the
    containment check runs.

    Neither `Path.resolve()` nor `Path.exists()` is exception-safe for our
    purposes here: a symlink cycle makes `resolve()` raise bare
    `RuntimeError`, and a component too long for the filesystem makes
    `exists()`'s own `stat()` raise bare `OSError` (e.g. `OSError:
    [Errno 36] File name too long`) that `exists()` does not swallow —
    neither is `ValueError` or `FileNotFoundError`, so both would otherwise
    reach the caller as an unhandled 500. They are refused the same way any
    other escape is: `ValueError`. `FileNotFoundError`/`NotADirectoryError`
    — should the platform's `resolve()` ever raise one of those instead of
    resolving leniently — are normalised to the plain `FileNotFoundError` a
    caller already expects, so they still answer 404, not 400 or 500.
    """
    root = Path(ws).resolve()
    try:
        candidate = (root / relpath).resolve()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise FileNotFoundError(relpath) from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"path escapes the run: {relpath!r}") from exc
    if candidate == root:
        if allow_root:
            return candidate
        raise ValueError("path escapes the run: the run root is not a file")
    if root not in candidate.parents:
        raise ValueError(f"path escapes the run: {relpath!r}")
    try:
        found = candidate.exists()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"path escapes the run: {relpath!r}") from exc
    if not found:
        raise FileNotFoundError(relpath)
    return candidate


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def download_type(path: Path) -> tuple[str, bool]:
    """The media type to send, and whether the browser must download it
    rather than render it.

    Only the small `INLINE_SAFE_TYPES` allowlist — images and PDF — may be
    shown inline. Everything else (HTML, JS, SVG, and anything unrecognised)
    is forced to `application/octet-stream` with a download disposition, so
    a run artifact written by an agent can never execute same-origin in the
    app, whatever extension it was given.
    """
    guessed = media_type(path)
    if guessed in INLINE_SAFE_TYPES:
        return guessed, False
    return "application/octet-stream", True


def listing(ws: Path, relpath: str = "") -> list[dict]:
    """Directories first, then files, each sorted by name.

    An entry this process cannot stat (permission denied on that one
    child) is silently omitted rather than failing the whole listing; a
    directory that cannot be listed at all (permission denied on the
    directory itself) is reported as a `ValueError` — the same 400 a
    caller already gets for any other bad path — rather than surfacing
    as an unhandled 500.
    """
    directory = resolve(ws, relpath, allow_root=True) if relpath else Path(ws).resolve()
    if not directory.is_dir():
        raise ValueError(f"not a directory: {relpath!r}")
    root = Path(ws).resolve()
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise ValueError(f"cannot list {relpath!r}: {exc.strerror or exc}") from exc
    entries = []
    for child in sorted(children, key=lambda p: (not p.is_dir(), p.name)):
        if child.name.endswith(".lock"):
            continue
        try:
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            continue  # unreadable entry — omit it rather than fail the listing
        entries.append({
            "name": child.name,
            "path": str(child.relative_to(root)),
            "is_dir": is_dir,
            "size": size,
        })
    return entries
