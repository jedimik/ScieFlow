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
import shutil
from datetime import date
from pathlib import Path

import yaml

from scieflow import research
from scieflow.core import drafts, jobs

LATEXMK = "latexmk"
PREVIEW_DIR = "manuscript/curation/preview"
TEMPLATE_DIR = Path(research.__file__).parent / "templates" / "paper"

# The shipped template's own section order (`main.tex`'s `\input{sections/...}`
# lines, top to bottom). A draft's sections that also appear here are put in
# this order, so a preview reads the way a finished assembly would; a
# section the template does not know about (a draft may add one) is not
# dropped — it is appended afterwards, sorted, rather than silently omitted.
TEMPLATE_SECTION_ORDER = ("abstract", "introduction", "methods", "results", "discussion")


class PreviewError(ValueError):
    """A draft or round cannot be assembled into a previewable document."""


def available() -> bool:
    return shutil.which(LATEXMK) is not None


def compile_argv() -> list[str]:
    """`latexmk` invoked exactly as the paper-draft skill's own Phase 6
    verify step does — and no more.

    `-shell-escape` (in any spelling: `--shell-escape`, `-enable-write18`)
    is deliberately absent and must stay absent: the `.tex` being compiled
    was written by an AI agent, so it is untrusted input, and `\\write18`
    would turn a preview into an arbitrary command-execution primitive.
    This function exists on its own precisely so a test
    (`test_shell_escape_is_never_passed`) can assert the flag's absence
    directly, rather than only checking that a compile succeeded — a check
    that would keep passing even after someone added the flag back to make
    some package work.
    """
    return [LATEXMK, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]


def _ordered_sections(names: list[str]) -> list[str]:
    present = set(names)
    ordered = [s for s in TEMPLATE_SECTION_ORDER if s in present]
    extra = sorted(present - set(TEMPLATE_SECTION_ORDER))
    return ordered + extra


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

    The template's own `main.tex` cannot be copied verbatim: it
    `\\input`s `sections/<name>`, the *merged* output a completed run
    assembles, not `manuscript/drafts/<agent>/<name>` — a directory that
    generally has no `sections/` subdirectory of its own at all. So the
    rewrite is done by line, not by regex over the whole file: every line
    containing `\\input{sections/` is dropped, and one `\\input{<name>}`
    line per section actually present is inserted at the position of the
    first such line — in the template's own section order where a name
    matches it, any extra sections afterwards — so a multi-section draft
    reads in the conventional order rather than in whatever order the
    filesystem happens to hand back.

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
    tex_files = sorted(src.glob("*.tex"))
    if not tex_files:
        raise PreviewError(f"no sections to preview: {source!r} has no .tex files")

    ws = Path(ws)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    section_names = []
    for path in tex_files:
        shutil.copyfile(path, dest / path.name)
        section_names.append(path.stem)

    run_preamble = ws / "manuscript" / "preamble.tex"
    preamble_src = run_preamble if run_preamble.is_file() else TEMPLATE_DIR / "preamble.tex"
    shutil.copyfile(preamble_src, dest / "preamble.tex")

    bib_src = ws / "manuscript" / "references.bib"
    has_bib = bib_src.is_file()
    if has_bib:
        shutil.copyfile(bib_src, dest / "references.bib")

    ordered = _ordered_sections(section_names)
    template_lines = (TEMPLATE_DIR / "main.tex").read_text().splitlines(keepends=True)

    out_lines: list[str] = []
    inserted_sections = False
    for line in template_lines:
        if "\\input{sections/" in line:
            if not inserted_sections:
                out_lines.extend(f"\\input{{{name}}}\n" for name in ordered)
                inserted_sections = True
            continue
        if not has_bib and ("\\bibliographystyle{" in line or "\\bibliography{" in line):
            continue
        out_lines.append(line)

    text = "".join(out_lines)
    for placeholder, value in _placeholder_values(ws).items():
        text = text.replace(placeholder, value)

    main = dest / "main.tex"
    main.write_text(text)
    return main


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
    `$HOME` alone) are pointed at a scratch directory *inside* `main`'s own
    preview directory — already part of `writable` via `ws` — created here
    so kpathsea never has to create it under load.
    """
    main = Path(main)
    cache = main.parent / ".texmf-cache"
    for sub in ("texmf-var", "texmf-config", "texmf-home"):
        (cache / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(cache)
    env["TEXMFVAR"] = str(cache / "texmf-var")
    env["TEXMFCONFIG"] = str(cache / "texmf-config")
    env["TEXMFHOME"] = str(cache / "texmf-home")
    return jobs.run_blocking(project, compile_argv(), kind="preview",
                             cwd=main.parent, run_dir=ws,
                             label=f"preview {main.parent.name}",
                             sandbox_writable=writable, env=env)
