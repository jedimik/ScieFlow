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

import unicodedata
from pathlib import Path

DRAFTS_DIR = "manuscript/drafts"
ROUNDS_DIR = "manuscript/curation/rounds"


class DraftError(ValueError):
    """A draft, round or source that cannot be read as asked."""


#: Characters that reorder a line on screen rather than breaking it. They are
#: category `Cf`, but the rest of `Cf` — the joiners scripts like Persian and
#: Devanagari genuinely need — carries no line-breaking or reordering meaning,
#: so refusing the whole category would make legitimate section names
#: unwritable. These nine are the overrides and isolates specifically.
_BIDI_REORDERING = frozenset("\u202a\u202b\u202c\u202d\u202e"
                             "\u2066\u2067\u2068\u2069")


def _breaks_a_line(ch: str) -> bool:
    """Whether `ch` can put a name's text onto a second line, or reorder it.

    `Cc` covers NUL, newline, DEL and U+0085 (NEL, which most readers treat as
    a line break and which an ASCII-only `ch < "\x20"` test misses); `Zl` and
    `Zp` are U+2028 and U+2029. The bidi set is about display rather than
    layout, and is included because a provenance heading is read by a person
    as well as by an agent.
    """
    return unicodedata.category(ch) in {"Cc", "Zl", "Zp"} or ch in _BIDI_REORDERING


def check_name(part: str) -> None:
    """Refuse `part` as a draft/round name: blank, whitespace-only,
    containing a path separator, any character that can break the name across
    lines or reorder it on screen (see `_breaks_a_line` — this is a Unicode
    rule, not an ASCII one), or exactly `.`/`..`.

    The control-character rule is not cosmetic. A drafting agent chooses
    these names — they are directory and file names it writes under
    `manuscript/drafts/` — and `service.keep_passage` enforces this same
    rule on the `agent`/`section` a kept passage records, where the value is
    stored as bare text and later rendered into the merge prompt's
    provenance heading, on a line of its own *outside* any passage wrapping.
    A newline there splits one heading into several lines the agent reads
    top-to-bottom, which is a forged-framing channel and not a filename any
    legitimate draft has. `curation.render` closes the same hole from the
    other side (its boundary token is derived from the headings too), so
    this is one of two independent defences, not the only one. It also
    sanitises the preview directory name and a job's label for free, since
    both are built from the same validated value.

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
    if (not part or not part.strip() or "/" in part
            or any(_breaks_a_line(ch) for ch in part)
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

    Both `.resolve()` calls are wrapped, not just the second: `root` itself
    can be a directory reached through a symlink cycle that has nothing to
    do with any caller-supplied name — e.g. `manuscript/drafts` itself
    symlinked into a loop — and that must not escape as a bare
    `RuntimeError` any more than a cycle in one of `parts` would.
    """
    try:
        base = Path(root).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise DraftError(f"cannot resolve {str(root)!r}: {exc}") from exc
    for part in parts:
        check_name(part)
    try:
        candidate = (base / Path(*parts)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}") from exc
    if base not in candidate.parents:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}")
    return candidate


def _subdirs(directory: Path) -> list[str]:
    """Names of the subdirectories directly inside `directory`, filtered
    exactly as `_tex_stems` filters files.

    `agents()` and `rounds()` used a bare `p.is_dir()` with no `check_name`
    and no containment check, while `sections`/`read_section` resolve through
    `_inside` and refuse both — so this module's own docstring promise ("a
    listing here never offers a name `read_section` would go on to refuse")
    held for files and not for directories. One `ln -s /anywhere
    manuscript/drafts/x` inside a run, which any drafting agent can create,
    therefore made `service.workbench` raise and `pages.drafts_page` 404 the
    entire workbench, permanently, with no escape but deleting the symlink in
    a terminal — precisely what AGENTS.md rule 4 exists to avoid. One stray
    symlink now costs one hidden column instead.
    """
    try:
        base = Path(directory).resolve()
    except (OSError, RuntimeError, ValueError):
        # `manuscript/drafts` itself symlinked into a cycle, say. A run with
        # no readable listing reads as empty here, the same as a run that has
        # not drafted at all; `sections`/`read_section` still raise for a
        # name someone asks for by hand.
        return []
    if not base.is_dir():
        return []
    names = []
    for entry in base.iterdir():
        try:
            check_name(entry.name)
        except DraftError:
            continue
        try:
            resolved = entry.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if base not in resolved.parents or not resolved.is_dir():
            continue
        names.append(entry.name)
    return names


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
    drafted. `[]` when the run has not drafted yet.

    Filtered by `_subdirs`, so an entry `sections`/`read_section` would go on
    to refuse — a name `check_name` rejects, or a symlink resolving outside
    the drafts directory — is skipped here rather than listed and then
    fatally re-refused. See `_subdirs` for what that cost before."""
    return sorted(_subdirs(Path(ws) / DRAFTS_DIR))


def sections(ws: Path, agent: str) -> list[str]:
    """That agent's section names (`.tex` stems), sorted."""
    agent_dir = _inside(Path(ws) / DRAFTS_DIR, agent)
    return _tex_stems(agent_dir)


def read_section(ws: Path, agent: str, section: str) -> str:
    """The raw source of one agent's one section.

    The raw `section` (and `agent`) are validated as names before `.tex` is
    ever appended — see `check_name`.

    Containment is checked against *that agent's own directory*
    (`agent_dir`), not `DRAFTS_DIR` as a whole — the same scope
    `_tex_stems` uses for `sections()`. A wider scope here would let a
    symlink inside one agent's directory that points into a *different*
    agent's directory pass this check while `sections()` (correctly)
    hides it, so `read_section` could hand back another agent's text
    under this agent's name — a false provenance the whole feature exists
    to avoid, since a kept passage's only record of who wrote it is this
    `agent` argument.
    """
    check_name(agent)
    check_name(section)
    agent_dir = _inside(Path(ws) / DRAFTS_DIR, agent)
    path = _inside(agent_dir, f"{section}.tex")
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

    Filtered by `_subdirs` first, for the same reason `agents()` is: a
    numeric-looking *symlink* out of the rounds directory would otherwise be
    listed here and then refused by `read_round_section`, taking the whole
    workbench page down with it.
    """
    return sorted(int(name) for name in _subdirs(Path(ws) / ROUNDS_DIR)
                  if name.isascii() and name.isdigit())


def round_sections(ws: Path, n: int) -> list[str]:
    """That round's section names, sorted — the same shape as `sections`,
    because a merge round's output is read exactly like an agent's draft."""
    round_dir = _inside(Path(ws) / ROUNDS_DIR, str(n))
    return _tex_stems(round_dir)


def read_round_section(ws: Path, n: int, section: str) -> str:
    """The raw source of one round's one section.

    Containment is scoped to that round's own directory, not `ROUNDS_DIR`
    as a whole — the same reasoning as `read_section`'s `agent_dir`: a
    symlink inside round `1`'s directory must not be able to point into
    round `2`'s and be served as round `1`'s content.
    """
    check_name(str(n))
    check_name(section)
    round_dir = _inside(Path(ws) / ROUNDS_DIR, str(n))
    path = _inside(round_dir, f"{section}.tex")
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
