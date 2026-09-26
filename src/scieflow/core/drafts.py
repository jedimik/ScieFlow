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
"""

from __future__ import annotations

from pathlib import Path

DRAFTS_DIR = "manuscript/drafts"
ROUNDS_DIR = "manuscript/curation/rounds"


class DraftError(ValueError):
    """A draft, round or source that cannot be read as asked."""


def _check_name(part: str) -> None:
    """Refuse `part` as a draft/round name: blank, whitespace-only,
    containing a path separator or a `:` (the separator `source_dir` uses
    between a source's kind and its value — a name that itself contains one
    can never be a genuine draft or round name), or exactly `.`/`..`.

    Exposed separately from `_inside` so a caller that is about to build a
    filename from a name (`read_section` appending `.tex`) can validate the
    raw, un-suffixed value first — appending first would turn `""` into the
    plausible filename `".tex"` and `".."` into `"...tex"`, catching a bad
    name only incidentally, as a missing file, instead of refusing it as a
    name.
    """
    if not part or not part.strip() or "/" in part or ":" in part or part in {".", ".."}:
        raise DraftError(f"not a draft name: {part!r}")


def _inside(root: Path, *parts: str) -> Path:
    """`root/parts…`, refusing anything that resolves outside `root`.

    Mirrors the containment check `scieflow.web.files.resolve` applies to a
    browser-requested artifact path — resolve first (normalising `..` and
    following symlinks), then compare the *resolved* candidate against the
    *resolved* root, never the raw string. Written out here rather than
    imported, because core must not depend on the web layer.

    Each `part` is checked with `_check_name` before any path is built from
    it.
    """
    base = Path(root).resolve()
    for part in parts:
        _check_name(part)
    candidate = (base / Path(*parts)).resolve()
    if candidate != base and base not in candidate.parents:
        raise DraftError(f"path escapes the run: {'/'.join(parts)!r}")
    return candidate


def _tex_stems(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.tex") if p.is_file())


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
    ever appended — see `_check_name`.
    """
    _check_name(agent)
    _check_name(section)
    path = _inside(Path(ws) / DRAFTS_DIR, agent, f"{section}.tex")
    if not path.is_file():
        raise DraftError(f"no such section {section!r} for agent {agent!r}")
    return path.read_text(encoding="utf-8")


def rounds(ws: Path) -> list[int]:
    """Completed merge rounds, ascending. Only numbered subdirectories of
    `manuscript/curation/rounds/` count — that directory also holds
    `document.yml`'s neighbours, which are not rounds and are ignored."""
    rounds_dir = Path(ws) / ROUNDS_DIR
    if not rounds_dir.is_dir():
        return []
    return sorted(int(p.name) for p in rounds_dir.iterdir()
                  if p.is_dir() and p.name.isdigit())


def round_sections(ws: Path, n: int) -> list[str]:
    """That round's section names, sorted — the same shape as `sections`,
    because a merge round's output is read exactly like an agent's draft."""
    round_dir = _inside(Path(ws) / ROUNDS_DIR, str(n))
    return _tex_stems(round_dir)


def read_round_section(ws: Path, n: int, section: str) -> str:
    """The raw source of one round's one section."""
    _check_name(str(n))
    _check_name(section)
    path = _inside(Path(ws) / ROUNDS_DIR, str(n), f"{section}.tex")
    if not path.is_file():
        raise DraftError(f"no such section {section!r} in round {n!r}")
    return path.read_text(encoding="utf-8")


def source_dir(ws: Path, source: str) -> Path:
    """The directory holding one previewable draft's sections.

    `source` is `"agent:<name>"` or `"round:<n>"` and arrives from a form.
    Split once, match the kind exactly, and hand the remainder to `_inside`
    — never build a path from an unmatched string.
    """
    kind, _, rest = str(source).partition(":")
    if kind == "agent" and rest:
        return _inside(Path(ws) / DRAFTS_DIR, rest)
    if kind == "round" and rest.isdigit():
        return _inside(Path(ws) / ROUNDS_DIR, rest)
    raise DraftError(f"not a previewable draft: {source!r}")
