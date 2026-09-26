"""Reading what the drafting agents wrote, and what a merge round produced.

Nothing here writes anything. A merge round (a later task) reads several
agents' `manuscript/drafts/<agent>/*.tex` at once, plus — once at least one
round has run — the previous round's own output under
`manuscript/curation/rounds/<n>/`, which is read exactly the same way: it is
just another directory of `.tex` sections, so the next round's "agents" pane
and "already merged" pane share one reading path.

A run that has not drafted yet is an ordinary state, not an error:
`agents(ws)` and `rounds(ws)` both read as empty rather than raising.

`agent`, `section` and `source` all arrive from a web form and are used to
build a filesystem path, so every reader here goes through `_inside`, which
refuses anything that resolves outside the directory it is confined to.
`check_name` — the per-part rule `_inside` enforces — is public so a writer
that stores `agent`/`section` as bare text with no path of its own
(`service.keep_passage`) can still enforce the identical rule: a value one
side would accept and the other refuse is exactly the leak a shared check
prevents.
"""

from __future__ import annotations

from pathlib import Path

DRAFTS_DIR = "manuscript/drafts"
ROUNDS_DIR = "manuscript/curation/rounds"


class DraftError(ValueError):
    """A draft, round or source that cannot be read as asked."""


def check_name(part: str) -> None:
    """Refuse `part` as a draft/round name: blank, whitespace-only,
    containing a path separator or a NUL byte, or exactly `.`/`..`.

    Deliberately does *not* forbid `:` — it is an ordinary, legal filename
    character with no traversal meaning of its own. The one place `:` is
    meaningful is `source_dir`'s `"agent:<name>"` / `"round:<n>"` grammar,
    and that parsing lives there, not here — banning it generically would
    make a real file `drafts/claude/a:b.tex` listable by `sections` and
    then refused by `read_section`, and would make a malformed `source`
    string fail with this function's generic message instead of naming the
    actual problem.

    Called on the raw, un-suffixed value *before* a filename is built from
    it — `read_section` checks `section` before appending `.tex`, so a
    bad name is refused as a name, not caught only incidentally as
    `".tex"` (from `""`) or `"...tex"` (from `".."`) failing to exist.
    """
    if (not part or not part.strip() or "/" in part or "\x00" in part
            or part in {".", ".."}):
        raise DraftError(f"not a draft name: {part!r}")


def _inside(root: Path, *parts: str) -> Path:
    """`root/parts…`, refusing anything that resolves outside `root`.

    Mirrors the containment check `scieflow.web.files.resolve` applies to a
    browser-requested artifact path — resolve first (normalising `..` and
    following symlinks), then compare the *resolved* candidate against the
    *resolved* root, never the raw string. Written out here rather than
    imported, because core must not depend on the web layer.

    Two things `web.files.resolve` also does, and this mirrors:
    `.resolve()` is not exception-safe — a symlink cycle raises a bare
    `RuntimeError`, an over-long or invalid (e.g. embedded-NUL) component a
    bare `OSError`/`ValueError` — none of which is `DraftError`, so all
    three are caught and re-raised as one; and the root itself is refused
    as a result, exactly like `resolve()` refuses the run root unless
    `allow_root=True` — a symlink that resolves back to `root` (e.g. a
    `self -> ..` entry inside a drafts directory) is not anyone's draft.
    """
    base = Path(root).resolve()
    for part in parts:
        check_name(part)
    try:
        candidate = (base / Path(*parts)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}") from exc
    if base not in candidate.parents:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}")
    return candidate


def _tex_stems(directory: Path) -> list[str]:
    """Sorted `.tex` stems directly inside `directory` (already resolved and
    confined by the caller's own `_inside` call).

    An entry whose name `check_name` would refuse (never produced by a
    legitimate draft) or whose resolved target lies outside `directory` (a
    symlink escaping it) is silently skipped — the same way
    `web.files.listing` omits an entry it cannot stat rather than failing
    the whole listing — so a listing here never offers a name `read_section`
    would go on to refuse, and one stray or malicious file cannot take the
    rest of the page down with it.
    """
    if not directory.is_dir():
        return []
    stems = []
    for p in directory.glob("*.tex"):
        try:
            check_name(p.stem)
        except DraftError:
            continue
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if directory not in resolved.parents or not resolved.is_file():
            continue
        stems.append(p.stem)
    return sorted(stems)


def agents(ws: Path) -> list[str]:
    """Sorted subdirectories of `manuscript/drafts/` — one per agent that has
    drafted. `[]` when the run has not drafted yet."""
    drafts_dir = Path(ws) / DRAFTS_DIR
    if not drafts_dir.is_dir():
        return []
    return sorted(p.name for p in drafts_dir.iterdir() if p.is_dir())


def sections(ws: Path, agent: str) -> list[str]:
    """That agent's section names (`.tex` stems), sorted."""
    agent_dir = _inside(Path(ws) / DRAFTS_DIR, agent)
    return _tex_stems(agent_dir)


def read_section(ws: Path, agent: str, section: str) -> str:
    """The raw source of one agent's one section.

    The raw `section` (and `agent`) are validated as names before `.tex` is
    ever appended — see `check_name`.
    """
    check_name(agent)
    check_name(section)
    path = _inside(Path(ws) / DRAFTS_DIR, agent, f"{section}.tex")
    if not path.is_file():
        raise DraftError(f"no such section {section!r} for agent {agent!r}")
    return path.read_text(encoding="utf-8")


def rounds(ws: Path) -> list[int]:
    """Completed merge rounds, ascending. Only numbered subdirectories of
    `manuscript/curation/rounds/` count — that directory also holds
    `document.yml`'s neighbours, which are not rounds and are ignored.

    `isascii() and isdigit()`, not `isdigit()` alone: a Unicode digit like
    `"²"` or `"١"` makes `str.isdigit()` true but `int()` raise, and a
    directory named that way exists only to be ignored, not to crash the
    one function whose job is ignoring it.
    """
    rounds_dir = Path(ws) / ROUNDS_DIR
    if not rounds_dir.is_dir():
        return []
    return sorted(int(p.name) for p in rounds_dir.iterdir()
                  if p.is_dir() and p.name.isascii() and p.name.isdigit())


def round_sections(ws: Path, n: int) -> list[str]:
    """That round's section names, sorted — the same shape as `sections`,
    because a merge round's output is read exactly like an agent's draft."""
    round_dir = _inside(Path(ws) / ROUNDS_DIR, str(n))
    return _tex_stems(round_dir)


def read_round_section(ws: Path, n: int, section: str) -> str:
    """The raw source of one round's one section."""
    check_name(str(n))
    check_name(section)
    path = _inside(Path(ws) / ROUNDS_DIR, str(n), f"{section}.tex")
    if not path.is_file():
        raise DraftError(f"no such section {section!r} in round {n!r}")
    return path.read_text(encoding="utf-8")


def source_dir(ws: Path, source: str) -> Path:
    """The directory holding one previewable draft's sections.

    `source` is `"agent:<name>"` or `"round:<n>"` and arrives from a form.
    Split once; a second `:` anywhere in the value, or none at all, makes
    the string malformed rather than a name that merely fails `_inside`'s
    check, so it is refused here, by name, before either kind is matched —
    never by handing an unmatched string down to become part of a path.
    """
    kind, sep, rest = str(source).partition(":")
    if not sep or ":" in rest:
        raise DraftError(f"not a previewable draft: {source!r}")
    if kind == "agent" and rest:
        return _inside(Path(ws) / DRAFTS_DIR, rest)
    if kind == "round" and rest.isascii() and rest.isdigit():
        return _inside(Path(ws) / ROUNDS_DIR, rest)
    raise DraftError(f"not a previewable draft: {source!r}")
