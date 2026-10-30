"""Synthetic journal fixtures (M-F t2). Every journal, page and value here is invented.

`synth` builds a temporary ScieFlow project: copies of the repo's schemas/ and config/reporting/,
a stub config/agents.yml (so repo_root() finds it), an empty config/journals/ and one run whose
journal/ folder holds two saved guideline pages. The real config/journals/ is never touched.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]

SRC1 = "s4f1c2a9e07"
SRC2 = "s9b03d55e2a"
SLUG = "journal-of-synthetic-neurology"
RUN = "synth-run"

SRC1_URL = "https://guides.example.test/synthetic-neurology/authors"
SRC2_URL = "https://policies.example.test/synthetic-neurology/word-limits"

SRC1_TEXT = """Journal of Synthetic Neurology: guide for authors
Scope
The journal publishes clinical neurology research for an international readership.
Article types
Research articles may contain up to 3000 words in the main text, excluding the abstract and references.
The abstract must not exceed 250 words and must be structured with the headings Background, Methods, Results and Conclusions.
Research articles have at most 40 references, numbered in order of appearance.
Manuscripts must follow the CONSORT 2010 reporting guideline for randomised trials.
Declarations: a conflict of interest statement, a funding statement and an ethics statement are required.
Review: the review is double-blind.
"""

SRC2_TEXT = """Synthetic Neurology author policies
Research articles are limited to 3,000 words of main text.
Word limits exclude the abstract and references.
"""

LIMIT_NULLS = (
    "words_title", "keywords_min", "keywords_max", "figures_max", "tables_max",
    "display_items_max", "references_min", "highlights_max", "highlight_chars_max", "pages_max",
)

QUOTE_WORDS = "Research articles may contain up to 3000 words in the main text"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_profile_data(text1: str = SRC1_TEXT, text2: str = SRC2_TEXT) -> dict:
    """A valid synthetic profile: every non-null sourced leaf has an evidence entry."""
    limits = {key: None for key in LIMIT_NULLS}
    limits.update({"words_main": 3000, "words_abstract": 250, "references_max": 40})
    return {
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
            "limits": limits,
            "abstract": {"structured": True,
                         "headings": ["Background", "Methods", "Results", "Conclusions"]},
            "structure": {"order": None, "sections": []},
        }],
        "references": {"style_name": None, "bibtex_style": None, "natbib": None, "numbering": "numeric"},
        "templates": {"latex_class": None, "latex_class_name": None, "latex_options": None,
                      "template_url": None, "word_first": None},
        "submission": {"system": None, "required_files": [], "line_numbers": None, "blinding": None},
        "declarations": {"required": ["coi", "funding", "ethics"]},
        "reporting_guidelines": {"by_study_type": {"rct": ["consort-2010"]}, "other": []},
        "review": {"type": "double-blind", "criteria": []},
        "evidence": [
            {"field": "scope.text", "source": SRC1,
             "quote": "The journal publishes clinical neurology research for an international readership."},
            {"field": "article_types[0]", "source": SRC1, "quote": QUOTE_WORDS},
            {"field": "article_types[0].limits.words_main", "source": SRC1, "quote": QUOTE_WORDS},
            {"field": "article_types[0].limits.words_abstract", "source": SRC1,
             "quote": "The abstract must not exceed 250 words"},
            {"field": "article_types[0].limits.references_max", "source": SRC1,
             "quote": "Research articles have at most 40 references"},
            {"field": "article_types[0].abstract.structured", "source": SRC1,
             "quote": "must be structured with the headings Background, Methods, Results and Conclusions"},
            {"field": "article_types[0].abstract", "source": SRC1,
             "quote": "The abstract must not exceed 250 words and must be structured with the headings "
                      "Background, Methods, Results and Conclusions."},
            {"field": "references", "source": SRC1,
             "quote": "Research articles have at most 40 references, numbered in order of appearance."},
            {"field": "declarations", "source": SRC1,
             "quote": "a conflict of interest statement, a funding statement and an ethics statement are required"},
            {"field": "reporting_guidelines.by_study_type", "source": SRC1,
             "quote": "Manuscripts must follow the CONSORT 2010 reporting guideline for randomised trials."},
            {"field": "review", "source": SRC1, "quote": "Review: the review is double-blind."},
        ],
        "conflicts": [],
        "provenance": {
            "run": RUN, "facts_sha256": "0" * 64,
            "sources": [
                {"id": SRC1, "url": SRC1_URL, "retrieved_at": "2026-10-01", "retrieved_by": "gateway",
                 "text_sha256": _sha(text1.encode("utf-8"))},
                {"id": SRC2, "url": SRC2_URL, "retrieved_at": "2026-10-01", "retrieved_by": "gateway",
                 "text_sha256": _sha(text2.encode("utf-8"))},
            ],
            "max_age_days": 90, "adopted_at": "2026-10-01T12:00:00Z", "adopted_by": "human",
            "tool": "scieflow-test", "format": 1,
        },
    }


@pytest.fixture
def profile_data():
    """Factory for a fresh valid synthetic profile dict (mutate freely in a test)."""
    return valid_profile_data


@dataclass
class Synth:
    root: Path
    run_dir: Path

    @property
    def journal_dir(self) -> Path:
        return self.run_dir / "journal"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "config" / "journals"

    def profile(self) -> dict:
        return valid_profile_data()

    def write_profile(self, data: dict, slug: str = SLUG) -> Path:
        path = self.profiles_dir / f"{slug}.yml"
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def sources_json(self) -> Path:
        return self.journal_dir / "sources.json"


@pytest.fixture
def synth(tmp_path, monkeypatch) -> Synth:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(REPO / "schemas", root / "schemas")
    shutil.copytree(REPO / "config" / "reporting", root / "config" / "reporting")
    (root / "config" / "agents.yml").write_text("agents: {}\n", encoding="utf-8")
    (root / "config" / "journals").mkdir()
    run_dir = root / "workspace" / RUN
    (run_dir / "journal" / "sources").mkdir(parents=True)
    rows = []
    for sid, url, text in ((SRC1, SRC1_URL, SRC1_TEXT), (SRC2, SRC2_URL, SRC2_TEXT)):
        raw = text.encode("utf-8")
        (run_dir / "journal" / "sources" / f"{sid}.raw").write_bytes(raw)
        (run_dir / "journal" / "sources" / f"{sid}.txt").write_bytes(raw)
        rows.append({"id": sid, "url": url, "host": "guides.example.test", "status": "ok",
                     "classification": "public", "retrieved_at": "2026-10-01",
                     "retrieved_by": "gateway", "raw_sha256": _sha(raw), "text_sha256": _sha(raw),
                     "chars": len(text)})
    (run_dir / "journal" / "sources.json").write_text(
        json.dumps({"version": 1, "rows": rows}, indent=2), encoding="utf-8")
    monkeypatch.chdir(root)
    return Synth(root=root, run_dir=run_dir)
