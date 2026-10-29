"""Locate a LaTeX engine and compile a document with it.

The plugin does not require any particular TeX distribution. It uses, in order
of preference:

1. an engine named explicitly (`--engine`, or the LATEX_ENGINE env var);
2. `tectonic` (recommended: one binary, fetches packages on demand, reproducible),
   found on PATH or via the TECTONIC env var;
3. `latexmk`, which ships with TeX Live, MiKTeX, and MacTeX and handles the
   rerun and bibliography passes itself;
4. a raw engine (`pdflatex`, `xelatex`, `lualatex`) driven directly, with the
   BibTeX or Biber passes run explicitly.

Import this from the other scripts rather than duplicating engine detection.
Stdlib only, Windows-safe.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

# Preference order when nothing is requested explicitly.
ENGINE_ORDER = ("tectonic", "latexmk", "pdflatex", "xelatex", "lualatex")

LATEXMK_FLAG = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}

BIBDATA_RE = re.compile(r"\\bibdata\{")
CITATION_RE = re.compile(r"\\citation\{")

DEFAULT_TIMEOUT = 600


class Engine:
    """A resolved LaTeX engine: its name, its executable, and how it is driven."""

    def __init__(self, name, executable, latexmk_engine=None):
        self.name = name
        self.executable = executable
        # Which underlying TeX engine latexmk should drive (latexmk only).
        self.latexmk_engine = latexmk_engine or "pdflatex"

    @property
    def kind(self):
        if self.name == "tectonic":
            return "tectonic"
        if self.name == "latexmk":
            return "latexmk"
        return "raw"

    def __repr__(self):
        return f"<Engine {self.name} at {self.executable}>"


def _which(name):
    return shutil.which(name)


def find_engine(preferred=None, env=None):
    """Resolve a LaTeX engine, or return None when no TeX installation is found.

    `preferred` may be an engine name ("tectonic", "latexmk", "pdflatex",
    "xelatex", "lualatex") or a path to an executable. The LATEX_ENGINE
    environment variable does the same thing; the TECTONIC variable keeps
    working and points at a tectonic binary.
    """
    env = os.environ if env is None else env
    requested = preferred or env.get("LATEX_ENGINE")

    if requested:
        # An explicit path to a binary.
        candidate = Path(requested)
        if candidate.exists() and candidate.is_file():
            return Engine(candidate.stem.lower(), str(candidate))
        name = requested.lower()
        if name in LATEXMK_FLAG and _which("latexmk") and not _which(name):
            # Asked for xelatex/lualatex but only latexmk is present: drive it.
            return Engine("latexmk", _which("latexmk"), latexmk_engine=name)
        path = _which(name)
        if path:
            return Engine(name, path, latexmk_engine=name if name in LATEXMK_FLAG else None)
        raise FileNotFoundError(f"Requested LaTeX engine not found: {requested}")

    tectonic_env = env.get("TECTONIC")
    if tectonic_env and Path(tectonic_env).exists():
        return Engine("tectonic", tectonic_env)

    for name in ENGINE_ORDER:
        path = _which(name)
        if path:
            return Engine(name, path, latexmk_engine=name if name in LATEXMK_FLAG else None)
    return None


def install_hint():
    return (
        "No LaTeX engine found. Install one of:\n"
        "  tectonic   https://tectonic-typesetting.github.io  (single binary, fetches packages)\n"
        "  TeX Live   https://tug.org/texlive/                (Linux, Windows, macOS)\n"
        "  MiKTeX     https://miktex.org                      (Windows, macOS, Linux)\n"
        "  MacTeX     https://tug.org/mactex/                 (macOS)\n"
        "Set LATEX_ENGINE to pick one explicitly (for example: LATEX_ENGINE=latexmk)."
    )


def _run(command, workdir, timeout):
    return subprocess.run(
        command,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _needs_bibliography(aux_path):
    if not aux_path.exists():
        return False
    text = aux_path.read_text(encoding="utf-8", errors="replace")
    return bool(BIBDATA_RE.search(text) and CITATION_RE.search(text))


def build_commands(engine, tex_name, keep_logs=True):
    """The first-pass command for this engine. Exposed for tests and for
    callers that want to show the user exactly what will run."""
    if engine.kind == "tectonic":
        command = [engine.executable, tex_name]
        if keep_logs:
            command += ["--keep-logs", "--keep-intermediates"]
        return command
    if engine.kind == "latexmk":
        return [
            engine.executable,
            LATEXMK_FLAG.get(engine.latexmk_engine, "-pdf"),
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_name,
        ]
    return [
        engine.executable,
        "-interaction=nonstopmode",
        "-halt-on-error",
        tex_name,
    ]


def compile_document(tex_path, engine=None, timeout=DEFAULT_TIMEOUT, keep_logs=True):
    """Compile `tex_path` to PDF. Returns (ok, output, pdf_path).

    tectonic and latexmk handle their own rerun and bibliography passes. A raw
    engine is driven here: one pass, then BibTeX or Biber when the .aux calls
    for it, then two more passes to settle references.
    """
    tex_path = Path(tex_path)
    engine = engine or find_engine()
    if engine is None:
        return False, install_hint(), None

    workdir = tex_path.resolve().parent
    tex_name = tex_path.name
    stem = tex_path.stem
    pdf_path = workdir / f"{stem}.pdf"
    transcript = []

    def record(result):
        transcript.append((result.stdout or "") + (result.stderr or ""))
        return result

    try:
        result = record(_run(build_commands(engine, tex_name, keep_logs), workdir, timeout))
        if result.returncode != 0:
            return False, "\n".join(transcript), None

        if engine.kind == "raw":
            aux = workdir / f"{stem}.aux"
            bcf = workdir / f"{stem}.bcf"
            if bcf.exists() and _which("biber"):
                record(_run([_which("biber"), stem], workdir, timeout))
            elif _needs_bibliography(aux) and _which("bibtex"):
                record(_run([_which("bibtex"), stem], workdir, timeout))
            for _ in range(2):
                result = record(_run(build_commands(engine, tex_name, keep_logs), workdir, timeout))
                if result.returncode != 0:
                    return False, "\n".join(transcript), None
    except subprocess.TimeoutExpired:
        transcript.append(f"Timed out after {timeout}s")
        return False, "\n".join(transcript), None
    except OSError as err:
        transcript.append(str(err))
        return False, "\n".join(transcript), None

    if not pdf_path.exists():
        transcript.append(f"The engine reported success but produced no {pdf_path.name}")
        return False, "\n".join(transcript), None
    return True, "\n".join(transcript), pdf_path
