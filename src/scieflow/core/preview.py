"""Compiling one whole draft into a real PDF, as a sandboxed job.

A drafting agent writes bare section `.tex` files
(`manuscript/drafts/<agent>/*.tex`) — no `\\documentclass`, no preamble —
and the shipped template's own `main.tex` `\\input`s `manuscript/sections/`,
the *merged* output a completed run assembles at Phase 5. Neither is
compilable on its own while a draft is still being written, and a
researcher wants to see what an agent's LaTeX actually renders as long
before that merge happens. `assemble` builds a throwaway document around
whichever draft or round is being looked at, in a scratch directory this
module owns (`manuscript/curation/preview/<source>/`), and never touches
the run's real `manuscript/` — a preview is not an assembly step, and
`paper-draft` Phase 5's own output must not be shadowed by one.

The compile itself is a job, never an inline call: `latexmk` takes seconds
to minutes, and this app runs a single uvicorn process. It also gets the
sandbox, the timeline and a Cancel button for free that way.

`shutil` and `jobs` are imported at module level under those exact names —
tests monkeypatch `preview.shutil.which` and `preview.jobs.run_blocking`,
and both patches only work because this module's own attributes are the
same module objects everything else imports.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date
from pathlib import Path

import yaml

from scieflow import research
from scieflow.core import drafts, jobs

LATEXMK = "latexmk"
PREVIEW_DIR = "manuscript/curation/preview"
TEMPLATE_DIR = Path(research.__file__).parent / "templates" / "paper"

# `\input{sections/<name>}`, wherever it appears in the template — matched
# and re-derived from the template's own text every time `assemble` runs,
# rather than kept as a separate constant here that would go stale the
# moment the template gains, loses or reorders a section.
_SECTION_INPUT = re.compile(r"\\input\{sections/([^}]+)\}")

_ABSTRACT_BEGIN = "\\begin{abstract}"
_ABSTRACT_END = "\\end{abstract}"

# The only variables the compile inherits from the serve process. Everything
# else is set explicitly by `compile_env` or is simply absent — see its
# docstring for why copying `os.environ` was a hole rather than a
# convenience. `PATH` is needed to find `bwrap` and `latexmk`; `LANG` only
# decides how the compiler's own messages are encoded in the log a human
# then reads.
_INHERITED = ("PATH", "LANG")

# kpathsea settings pinned positively, not left to their absence. `latexmk`
# passes none of these on the command line and `-norc` does not touch them:
# they are read straight from the environment by `pdflatex` itself, so an
# operator environment carrying `shell_escape=t` re-enabled `\write18` with
# no `-shell-escape` anywhere in argv. Verified on this host, with `-norc`
# present: `env shell_escape=t latexmk -norc -pdf ... main.tex` ran
# `runsystem(touch pwned.txt)...executed.`
_KPATHSEA_SAFE = {
    "shell_escape": "f",     # \write18 off, whatever the parent said
    "openout_any": "p",      # paranoid: no writing outside the compile dir
    "openin_any": "p",       # paranoid: no reading dotfiles or arbitrary paths
}


class PreviewError(ValueError):
    """A draft or round cannot be assembled into a previewable document."""


def available() -> bool:
    return shutil.which(LATEXMK) is not None


def compile_argv() -> list[str]:
    """`latexmk` invoked exactly as the paper-draft skill's own Phase 6
    verify step does, plus `-norc` — and no more.

    `-shell-escape` (in any spelling: `--shell-escape`, `-enable-write18`)
    is deliberately absent and must stay absent: the `.tex` being compiled
    was written by an AI agent, so it is untrusted input, and `\\write18`
    would turn a preview into an arbitrary command-execution primitive.
    This function exists on its own precisely so a test
    (`test_shell_escape_is_never_passed`) can assert the flag's absence
    directly, rather than only checking that a compile succeeded — a check
    that would keep passing even after someone added the flag back to make
    some package work.

    `-norc` closes a second hole in the same family: without it, `latexmk`
    reads and executes a `.latexmkrc` (as Perl) from the compile directory,
    among other locations — and the compile directory lives inside the run,
    writable by the very agent whose `.tex` is untrusted, so that agent can
    plant one. Verified empirically on this host: a `.latexmkrc` that writes
    a file and exits runs to completion during an ordinary
    `latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex`, with no
    `-shell-escape` in sight — `-norc` stops `latexmk` from reading it at
    all, system, user or local.
    """
    return [LATEXMK, "-norc", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]


def _placeholder_values(ws: Path) -> dict[str, str]:
    """Title/authors/date for the template's three placeholders.

    A preview must never demand the author list be settled first — most
    drafts are previewed long before that decision is even on the table —
    so every value here has a plain fallback. Nothing in ScieFlow writes a
    `title`/`authors`/`date` into a run's `config.yml` today, but a run is
    free to add them (e.g. once a charter or outline settles on one), and
    reading them here — rather than hardcoding the fallback — costs
    nothing and means a preview picks them up for free the day something
    does write them.
    """
    cfg: dict = {}
    cfg_file = Path(ws) / "config.yml"
    if cfg_file.exists():
        try:
            cfg = yaml.safe_load(cfg_file.read_text()) or {}
        except (OSError, yaml.YAMLError):
            cfg = {}
    slug = cfg.get("slug") or Path(ws).name
    return {
        "%%TITLE%%": str(cfg.get("title") or "Draft preview"),
        "%%AUTHORS%%": str(cfg.get("authors") or slug),
        "%%DATE%%": str(cfg.get("date") or date.today().isoformat()),
    }


def assemble(ws: Path, source: str, dest: Path) -> Path:
    """Build a throwaway, compilable document around `source`'s sections,
    under `dest` (a scratch directory this module owns), and return its
    `main.tex`.

    `source` is resolved by `drafts.source_dir` — `"agent:<name>"` or
    `"round:<n>"` — exactly as every other draft reader resolves it, so a
    malformed or escaping `source` is refused there, by that shared rule,
    before this function ever touches a filesystem path built from it.
    Which sections exist is then read with `drafts.sections`/
    `round_sections`, not a raw glob of the directory `source_dir` returned:
    those functions already skip a stem `check_name` would refuse and a
    symlink resolving outside the directory, exactly as the workbench's own
    listing of the same directory does — a raw glob here could disagree
    with that listing and preview content the workbench itself would hide.

    The template's own `main.tex` cannot be copied verbatim: it
    `\\input`s `sections/<name>`, the *merged* output a completed run
    assembles, not `manuscript/drafts/<agent>/<name>` — a directory that
    generally has no `sections/` subdirectory of its own at all. So the
    rewrite maps the template line for line, in the template's own order,
    rather than collapsing every section into one insertion point: each
    `\\input{sections/<name>}` line becomes `\\input{<name>}` when that
    section was actually copied, or is dropped entirely when it was not; a
    section the template never mentions is appended after the template's
    last section line, in whatever order `drafts.sections`/`round_sections`
    already sorted it. Collapsing to one insertion point was tried first
    and was wrong in a way no test caught: the template's *first*
    `\\input{sections/...}` line is `\\input{sections/abstract}`, sitting
    inside `\\begin{abstract}...\\end{abstract}` — `article`'s abstract
    environment sets `\\small\\quotation` — so every section landed there
    together, and a "preview" was the entire manuscript rendered small and
    indented under an Abstract heading, for every draft, whether or not it
    had an abstract section at all. The `\\begin{abstract}`/`\\end{abstract}`
    pair itself is now dropped along with the section line when there is no
    `abstract` section to put inside it, for the same reason: an empty
    abstract environment is not what a draft without one should preview as.

    The bibliography trap: the template ends with
    `\\bibliographystyle{plainnat}` / `\\bibliography{references}`, and
    `compile_argv`'s `-halt-on-error` means a missing `references.bib`
    would halt the compile outright — so a draft with no bibliography yet
    (most of drafting) could never preview at all. Those two lines are
    therefore kept only when a `references.bib` was actually found and
    copied; otherwise they are dropped like the section-input lines, and
    any `\\cite` keys in the draft simply render unresolved, which is the
    right trade for a preview.
    """
    src = drafts.source_dir(ws, source)
    kind, _, rest = source.partition(":")
    # `source_dir` above already validated this exact "agent:"/"round:"
    # split (and refused anything else) while resolving `src`; re-splitting
    # here is not a second copy of that validation, only the trivial
    # dispatch to the matching listing function.
    section_names = (drafts.sections(ws, rest) if kind == "agent"
                     else drafts.round_sections(ws, int(rest)))
    if not section_names:
        raise PreviewError(f"no sections to preview: {source!r} has no .tex files")

    ws = Path(ws)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    for name in section_names:
        shutil.copyfile(src / f"{name}.tex", dest / f"{name}.tex")

    run_preamble = ws / "manuscript" / "preamble.tex"
    preamble_src = run_preamble if run_preamble.is_file() else TEMPLATE_DIR / "preamble.tex"
    shutil.copyfile(preamble_src, dest / "preamble.tex")

    bib_src = ws / "manuscript" / "references.bib"
    has_bib = bib_src.is_file()
    if has_bib:
        shutil.copyfile(bib_src, dest / "references.bib")

    template_lines = (TEMPLATE_DIR / "main.tex").read_text().splitlines(keepends=True)
    out_lines: list[str] = []
    seen_template_names: list[str] = []
    last_input_at: int | None = None
    for line in template_lines:
        stripped = line.strip()
        if stripped in (_ABSTRACT_BEGIN, _ABSTRACT_END):
            if "abstract" in section_names:
                out_lines.append(line)
            continue
        match = _SECTION_INPUT.search(line)
        if match:
            name = match.group(1)
            seen_template_names.append(name)
            if name in section_names:
                out_lines.append(f"\\input{{{name}}}\n")
            last_input_at = len(out_lines)
            continue
        if not has_bib and ("\\bibliographystyle{" in line or "\\bibliography{" in line):
            continue
        out_lines.append(line)

    extra = [name for name in section_names if name not in seen_template_names]
    if extra:
        insert_at = last_input_at if last_input_at is not None else len(out_lines)
        out_lines[insert_at:insert_at] = [f"\\input{{{name}}}\n" for name in extra]

    text = "".join(out_lines)
    for placeholder, value in _placeholder_values(ws).items():
        text = text.replace(placeholder, value)

    main = dest / "main.tex"
    main.write_text(text)
    return main


def compile_cache(ws: Path) -> Path:
    """The one shared kpathsea scratch tree this run's previews compile
    against, created if absent. See `run_compile` for why it is shared.
    """
    cache = Path(ws) / PREVIEW_DIR / ".texmf-cache"
    for sub in ("texmf-var", "texmf-config", "texmf-home"):
        (cache / sub).mkdir(parents=True, exist_ok=True)
    return cache


def compile_env(cache: Path, parent: dict | None = None) -> dict[str, str]:
    """The compile's *whole* environment, built explicitly — never a copy of
    the serve process's.

    `dict(os.environ)` was the hole, and it was not theoretical.
    `compile_argv` keeps `-shell-escape` out of argv and `-norc` keeps
    `latexmk` from reading a `.latexmkrc` the drafting agent could plant, but
    neither touches the environment, and `pdflatex` reads kpathsea's
    `shell_escape` straight from it. Verified on this host, with `-norc`
    present and no `-shell-escape` anywhere:

        $ env shell_escape=t latexmk -norc -pdf -interaction=nonstopmode \
              -halt-on-error main.tex
        runsystem(touch pwned.txt)...executed.

    The precondition is an operator environment carrying `shell_escape=t` (or
    `openout_any=a`) — rare, but it is exactly the workaround someone reaches
    for when a package misbehaves, which is the scenario `compile_argv`'s own
    docstring warns about. An agent-planted `texmf.cnf` under the redirected
    `TEXMF*` was checked and is correctly *not* honoured, so the inherited
    environment was the whole of it.

    So the three kpathsea settings are pinned positively (`_KPATHSEA_SAFE`)
    rather than relied on to be absent, and only `_INHERITED` comes across.
    A welcome side effect: the serve process's model API keys stop being
    handed to `latexmk` at all.

    `parent` is the environment to inherit from, defaulting to this
    process's — a parameter only so a test can hand in a hostile one without
    mutating the real `os.environ`.
    """
    src = os.environ if parent is None else parent
    cache = Path(cache)
    env = {name: src[name] for name in _INHERITED if name in src}
    env["HOME"] = str(cache)
    env["TEXMFVAR"] = str(cache / "texmf-var")
    env["TEXMFCONFIG"] = str(cache / "texmf-config")
    env["TEXMFHOME"] = str(cache / "texmf-home")
    env.update(_KPATHSEA_SAFE)
    return env


def run_compile(project, ws: Path, main: Path, writable: list[Path]) -> jobs.Job:
    """The compile, as a job confined to its own run.

    Blocking here is correct: the caller (`service.compile_preview`) is a
    `def` route handler running in Starlette's threadpool, so the wait
    costs a thread, not the event loop.

    `latexmk`/`pdflatex` want a writable `$HOME` (font and format caches
    normally live under `$HOME/.texlive*`, per `kpsewhich -var-value=
    TEXMFVAR` and friends) — but the sandbox binds the real `$HOME`
    read-only, granting write access only inside `writable` (the run plus
    the allowlist). Left alone this fails confusingly, deep inside
    `pdflatex`'s own cache setup, with no hint that the sandbox is the
    cause. So `$HOME` (and, explicitly, `TEXMFVAR`/`TEXMFCONFIG`/
    `TEXMFHOME`, in case a host's texmf.cnf does not derive them from
    `$HOME` alone) are pointed at one scratch directory shared by every
    source this run ever previews — `<ws>/manuscript/curation/preview/
    .texmf-cache`, already inside `ws` and so already part of `writable` —
    rather than one per source: kpathsea's own font/format cache is
    designed to be shared, and a fresh tree per agent draft and per merge
    round would otherwise pile up, unpruned, in the researcher's own run
    directory for no benefit. Sharing it this way is only safe because
    `service.compile_preview` refuses to start a second preview compile
    anywhere in this run while one is genuinely still running — two
    concurrent `latexmk` processes racing on this same cache with no
    locking of our own would risk a corrupted `.fmt` file breaking every
    later compile. If that per-run serialization is ever loosened, this
    cache needs to go back to being per-`main.parent` (as it was before),
    not stay shared.

    The environment itself is `compile_env`'s job, not this function's: it is
    built explicitly rather than copied from the serve process, because
    `pdflatex` honours kpathsea's `shell_escape` from the environment whether
    or not `-shell-escape` is in argv. See `compile_env`.
    """
    main = Path(main)
    ws = Path(ws)
    return jobs.run_blocking(project, compile_argv(), kind="preview",
                             cwd=main.parent, run_dir=ws,
                             label=f"preview {main.parent.name}",
                             sandbox_writable=writable,
                             env=compile_env(compile_cache(ws)))
