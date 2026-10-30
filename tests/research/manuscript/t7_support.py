"""Synthetic fixtures for M-F t7 (templates, render, build). Every name, page and value here is invented."""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.research.manuscript.conftest import BIB

GOLDEN = Path(__file__).parent / "golden"
CLASSES = ("article", "elsarticle", "IEEEtran", "svjour3")

GOLD_METADATA = """version: 1
journal: journal-of-synthetic-neurology
article_type: research-article
title: "Gait_timing & 5% of #1 costs $2 in synthetic cohorts"
keywords: [gait, "timing_model", "5% rule"]
highlights: [Synthetic highlight one.]
authors:
  - name: Ada Synthetic
    email: ada.synthetic@example.test
    affiliations:
      - name: Synthetic Institute
    credit: [conceptualization]
    corresponding: true
  - name: Ben Invented
    affiliations:
      - name: Synthetic Institute
      - name: Invented Clinic
    credit: [methodology]
    corresponding: false
declarations:
  coi: None declared.
  funding: Synthetic funder.
  ethics: Synthetic approval.
"""

SECTIONS = {
    "abstract": "Background: Synthetic background words.\n\nMethods: Synthetic methods words.\n",
    "introduction": "Gait timing matters \\cite{smith2001}.\n",
    "methods": "Synthetic methods text \\cite{smith2001}.\n",
    "results": "Timing changed \\cite{lee2010}.\n",
    "discussion": "Discussion of synthetic results.\n",
}


def fresh_article(synth, *, metadata: str = GOLD_METADATA, remove=(), extra=None) -> Path:
    """Replace the manuscript folder with the synthetic article (no main.tex: render writes it).

    `remove` drops relative paths from the default set; `extra` adds or replaces files.
    """
    shutil.rmtree(synth.ms, ignore_errors=True)
    files = {"metadata.yml": metadata, "references.bib": BIB}
    for stem, text in SECTIONS.items():
        files[f"sections/{stem}.tex"] = text
    files.update(extra or {})
    for rel in remove:
        files.pop(rel, None)
    for rel, text in files.items():
        path = synth.ms / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return synth.ms


def golden_body(cls: str) -> str:
    return (GOLDEN / f"t7-{cls}.tex").read_text(encoding="utf-8")
