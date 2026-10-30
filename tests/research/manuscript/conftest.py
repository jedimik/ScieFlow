"""Synthetic manuscript fixtures (M-F t5). Every journal, author, page and value here is invented.

`synth` builds a temporary ScieFlow project: copies of the repo's schemas/ and config/reporting/, a
stub config/agents.yml (so repo_root() finds it), an empty config/journals/ and one run whose
manuscript/ folder holds a small article. The real config/journals/ is never touched.
"""

from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]

RUN = "ms-run"
SLUG = "journal-of-synthetic-neurology"
ORCID = "0000-0002-1825-0097"
ROR = "https://ror.org/00f54p054"

ABSTRACT = """Background: Synthetic background words here.

Methods: Synthetic methods words here.

Results: Synthetic results words here.

Conclusions: Synthetic conclusion words here.
"""

MAIN_TEX = r"""\documentclass[11pt]{article}
\bibliographystyle{plainnat}
\begin{document}
\title{Gait timing in synthetic cohorts}
\begin{abstract}
@@ABSTRACT@@
\end{abstract}
\maketitle
\section{Introduction}
Gait timing matters \cite{smith2001}.
\input{methods}
\section{Results}
Timing changed \cite{lee2010} as Figure~\ref{fig:one} shows.
\begin{figure}
\includegraphics{fig1.pdf}
\caption{Synthetic figure one.}
\end{figure}
\begin{figure}
\includegraphics{fig2.pdf}
\caption{Synthetic figure two.}
\end{figure}
\begin{table}
\caption{Synthetic table.}
\end{table}
\section{Discussion}
Discussion of synthetic results.
\section{Funding}
Synthetic funder.
\section{References}
\bibliography{references}
\end{document}
"""

METHODS_TEX = r"""\section{Methods}
Synthetic methods text \cite{smith2001}.
"""

BIB = """@article{smith2001,
  author = {Synthetic, A.},
  title = {A synthetic title},
  year = {2001}
}
@article{lee2010,
  author = {Invented, B.},
  title = {Another invented title},
  year = {2010}
}
"""

METADATA = f"""version: 1
journal: {SLUG}
article_type: research-article
title: Gait timing in synthetic cohorts
keywords: [gait, timing, synthetic]
highlights: [Synthetic highlight one.]
authors:
  - name: Ada Synthetic
    email: ada.synthetic@example.test
    orcid: {ORCID}
    affiliations:
      - name: Synthetic Institute
        ror: {ROR}
    credit: [conceptualization]
    corresponding: true
declarations:
  coi: None declared.
  funding: Synthetic funder.
  ethics: Synthetic approval.
letters:
  editor_name: Synthetic Editor
"""

SECTIONS = [
    {"title": "Introduction", "aliases": ["Background"], "required": True, "words_max": None},
    {"title": "Methods", "aliases": [], "required": True, "words_max": None},
    {"title": "Results", "aliases": [], "required": True, "words_max": None},
    {"title": "Discussion", "aliases": [], "required": True, "words_max": None},
]

LIMIT_KEYS = (
    "words_main", "words_abstract", "words_title", "keywords_min", "keywords_max", "figures_max",
    "tables_max", "display_items_max", "references_min", "references_max", "highlights_max",
    "highlight_chars_max", "pages_max",
)

BASE_LIMITS = {"words_main": 3000, "words_abstract": 250, "references_max": 40}


def default_files(abstract: str = ABSTRACT) -> dict:
    """The synthetic article as relative path -> text (manuscript/ folder)."""
    return {
        "main.tex": MAIN_TEX.replace("@@ABSTRACT@@", abstract.rstrip("\n")),
        "methods.tex": METHODS_TEX,
        "references.bib": BIB,
        "metadata.yml": METADATA,
    }


def _evidence_for(data: dict) -> list:
    """Synthetic evidence entries covering every non-null leaf (quotes are not re-checked: no sources).

    A list of scalars is one leaf at the list's own path; a list of mappings is walked by index.
    """
    out = []

    def entry(path, text):
        out.append({"field": path, "source": "s4f1c2a9e07", "quote": f"Synthetic evidence for {path}: {text}"})

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            if all(not isinstance(x, (dict, list)) for x in value):
                entry(path, ", ".join(str(x) for x in value) or "none")
            else:
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")
        elif value is not None and path != "version":
            entry(path, str(value))

    for block in ("scope", "article_types", "references", "templates", "submission",
                  "declarations", "reporting_guidelines", "review"):
        walk(data.get(block), block)
    return out



def make_profile(limits: dict | None = None, sections: list | None = None,
                 declarations: list | None = None, latex_class=None, bib_style=None,
                 word_first=None, structured=True, order="strict") -> dict:
    """A structured synthetic profile that passes profile.validate (run sources not on disk)."""
    all_limits: dict = {key: None for key in LIMIT_KEYS}
    all_limits.update(BASE_LIMITS)
    all_limits.update(limits or {})
    data = {
        "version": 1,
        "identity": {
            "name": "Journal of Synthetic Neurology", "slug": SLUG, "issn_l": "1234-5679",
            "issns": ["1234-5679"], "publisher": None, "homepage_url": None,
            "openalex_id": None, "crossref_title": None,
        },
        "oa": {"is_oa": None, "is_in_doaj": None, "apc_usd": None},
        "norms": {
            "sample": {"n": 0, "from_date": None, "to_date": None},
            "references": {"median": None, "p25": None, "p75": None},
            "abstract_words": {"median": None, "p25": None, "p75": None, "share_with_abstract": None},
            "title_words": {"median": None},
        },
        "scope": {"text": "The journal publishes clinical neurology research for an international readership."},
        "article_types": [{
            "id": "research-article",
            "label": "Research articles",
            "limits": all_limits,
            "abstract": {"structured": structured,
                         "headings": ["Background", "Methods", "Results", "Conclusions"]},
            "structure": {"order": order, "sections": sections if sections is not None else
                          copy.deepcopy(SECTIONS)},
        }],
        "references": {"style_name": None, "bibtex_style": bib_style, "natbib": None,
                       "numbering": "numeric"},
        "templates": {"latex_class": latex_class, "latex_class_name": None, "latex_options": None,
                      "template_url": None, "word_first": word_first},
        "submission": {"system": None, "required_files": [], "line_numbers": None, "blinding": None},
        "declarations": {"required": declarations if declarations is not None else ["coi", "funding", "ethics"]},
        "reporting_guidelines": {"by_study_type": {"rct": ["consort-2010"]}, "other": []},
        "review": {"type": "double-blind", "criteria": []},
        "conflicts": [],
        "provenance": {
            "run": RUN, "facts_sha256": "0" * 64, "sources": [],
            "max_age_days": 90, "adopted_at": "2026-10-01T12:00:00Z", "adopted_by": "human",
            "tool": "scieflow-test", "format": 1,
        },
    }
    data["evidence"] = _evidence_for(data)
    return data


@dataclass
class Synth:
    root: Path
    run_dir: Path

    @property
    def ms(self) -> Path:
        return self.run_dir / "manuscript"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "config" / "journals"

    def write_manuscript(self, files: dict | None = None) -> None:
        """Write the default article, with `files` (relative path -> text) replacing entries."""
        merged = default_files()
        merged.update(files or {})
        for rel, text in merged.items():
            path = self.ms / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.ms / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"{old!r} must occur once in {rel}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    def set_abstract(self, text: str) -> None:
        body = (self.ms / "main.tex").read_text(encoding="utf-8")
        new = re.sub(r"\\begin\{abstract\}.*?\\end\{abstract\}",
                     lambda _m: "\\begin{abstract}\n" + text + "\n\\end{abstract}", body, flags=re.S)
        (self.ms / "main.tex").write_text(new, encoding="utf-8")

    def write_profile(self, data: dict | None = None, slug: str = SLUG) -> Path:
        path = self.profiles_dir / f"{slug}.yml"
        path.write_text(yaml.safe_dump(data or make_profile(), sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return path


@pytest.fixture
def synth(tmp_path, monkeypatch) -> Synth:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(REPO / "schemas", root / "schemas")
    shutil.copytree(REPO / "config" / "reporting", root / "config" / "reporting")
    (root / "config" / "agents.yml").write_text("agents: {}\n", encoding="utf-8")
    (root / "config" / "journals").mkdir()
    run_dir = root / "workspace" / RUN
    run_dir.mkdir(parents=True)
    monkeypatch.chdir(root)
    fixture = Synth(root=root, run_dir=run_dir)
    fixture.write_manuscript()
    fixture.write_profile()
    return fixture
