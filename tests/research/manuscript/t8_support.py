"""Synthetic fixtures for M-F t8 (package, letters, zip). Every name, page and value here is invented.

The run is the conftest `synth` project. `make_build` writes the build record the way `build_run` does
(the PDF bytes are a stand-in; no TeX runs here). The review blocker fixture is the one under
tests/fixtures/review/run/ (copied into the run's review/ folder, never edited).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from scieflow.core.project import Project
from scieflow.research.manuscript import tex
from tests.research.manuscript.conftest import RUN

REVIEW_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "review" / "run"
PDF = b"%PDF-1.4\n% synthetic stand-in for a compiled manuscript\n%%EOF\n"
OVERRULE = {"K1": {"decision": "reject", "overrule_reason": "Retraction concerns Fig. 3 only"}}
TEXT_SUFFIXES = (".md", ".txt", ".tex", ".json", ".yml")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def project_of(synth) -> Project:
    return Project(synth.root)


def add_review(synth) -> None:
    shutil.copytree(REVIEW_FIXTURE / "review", synth.run_dir / "review")


def make_build(synth, *, pdf: bytes = PDF, ok: bool = True) -> Path:
    """Write manuscript/build/ as build_run would for the current source tree."""
    build = synth.ms / "build"
    build.mkdir(exist_ok=True)
    (build / "main.pdf").write_bytes(pdf)
    record = {
        "version": 1, "ok": ok, "engine": "latexmk",
        "argv": ["latexmk", "-no-shell-escape", "-norc", "-pdf", "-interaction=nonstopmode",
                 "-halt-on-error", "main.tex"],
        "returncodes": [0], "pdf_sha256": sha(pdf), "source_tree_sha256": tex.source_tree_hash(synth.ms),
        "class": "article", "bibtex_style": "plainnat", "notes": [],
    }
    (build / "build.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return build


def snapshot(folder: Path) -> dict:
    """Relative path -> bytes of every regular file under folder (for 'nothing was written' checks)."""
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(Path(folder).rglob("*")) if p.is_file() and not p.is_symlink()}


def submission(synth) -> Path:
    return synth.run_dir / "report" / "submission"


def run_slug() -> str:
    return RUN
