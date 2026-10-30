"""`manuscript check`: one case per rule of M-F 5.9 (except guideline-*, M-F t6). Synthetic data only."""

import datetime as dt

import yaml

from scieflow.research.manuscript import check
from tests.research.manuscript.conftest import make_profile

TODAY = dt.date(2026, 10, 29)


def _check(synth, **kw):
    kw.setdefault("today", TODAY)
    return check.run_check(synth.root, synth.run_dir, **kw)


def _rules(report, level=None):
    return sorted({f.rule for f in report.findings if level is None or f.severity == level})


def _one(report, rule):
    found = [f for f in report.findings if f.rule == rule]
    assert len(found) == 1, [f.to_dict() for f in report.findings]
    return found[0]


def test_the_synthetic_article_has_no_errors(synth):
    report = _check(synth)
    assert report.errors == []
    assert report.exit_code == 0
    assert report.article_type == "research-article"


def test_abstract_over_the_limit_is_flagged_with_limit_and_value(synth):
    synth.set_abstract(" ".join(["word"] * 251))
    finding = _one(_check(synth), "abstract-words")
    assert finding.severity == "error"
    assert finding.limit == 250 and finding.value == 251


def test_abstract_at_the_limit_is_not_flagged(synth):
    synth.set_abstract(" ".join(["word"] * 250))
    assert "abstract-words" not in _rules(_check(synth))


def test_missing_abstract_is_an_error(synth):
    synth.edit("main.tex", "\\begin{abstract}", "\\begin{notes}")
    synth.edit("main.tex", "\\end{abstract}", "\\end{notes}")
    assert _one(_check(synth), "abstract-missing").severity == "error"


def test_unstructured_abstract_is_a_warning_only(synth):
    synth.set_abstract("A single paragraph with synthetic words and no headings at all.")
    finding = _one(_check(synth), "abstract-structure")
    assert finding.severity == "warning"
    assert _check(synth).errors == []


def test_title_over_the_limit(synth):
    synth.write_profile(_profile_data(words_title=3))
    assert _one(_check(synth), "title-words").severity == "error"


def test_main_text_over_the_limit(synth):
    synth.write_profile(_profile_data(words_main=10))
    finding = _one(_check(synth), "main-words")
    assert finding.limit == 10 and finding.value == 15


def test_section_over_its_own_limit(synth):
    synth.write_profile(_profile_data(sections=[
        {"title": "Introduction", "aliases": [], "required": True, "words_max": 2},
        {"title": "Methods", "aliases": [], "required": True, "words_max": None},
        {"title": "Results", "aliases": [], "required": True, "words_max": None},
        {"title": "Discussion", "aliases": [], "required": True, "words_max": None},
    ]))
    finding = _one(_check(synth), "section-words")
    assert finding.where == "Introduction" and finding.limit == 2 and finding.value == 3


def test_keyword_count_outside_the_range(synth):
    synth.write_profile(_profile_data(keywords_min=4))
    assert _one(_check(synth), "keywords").value == 3


def test_highlights_count_and_length(synth):
    synth.write_profile(_profile_data(highlights_max=0))
    assert _one(_check(synth), "highlights").severity == "error"
    synth.write_profile(_profile_data(highlight_chars_max=5))
    assert _one(_check(synth), "highlights").limit == 5


def test_figures_and_tables_over_their_limits(synth):
    synth.write_profile(_profile_data(figures_max=1, tables_max=0))
    assert _one(_check(synth), "figures").value == 2
    assert _one(_check(synth), "tables").value == 1


def test_display_items_over_the_limit(synth):
    synth.edit("main.tex", "Discussion of synthetic results.",
               "Discussion of synthetic results.\n\\begin{equation}\na=b\n\\end{equation}")
    synth.write_profile(_profile_data(display_items_max=0))
    assert _one(_check(synth), "display-items").value == 1


def test_references_count_outside_the_range(synth):
    synth.write_profile(_profile_data(references_max=1))
    assert _one(_check(synth), "references-count").value == 2
    synth.write_profile(_profile_data(references_min=3))
    assert _one(_check(synth), "references-count").limit == 3


def test_cited_key_without_a_bib_entry_is_an_error(synth):
    synth.edit("main.tex", "Timing changed \\cite{lee2010}", "Timing changed \\cite{ghost2020}")
    finding = _one(_check(synth), "citation-missing")
    assert finding.severity == "error" and "ghost2020" in finding.message


def test_bib_entry_never_cited_is_a_warning(synth):
    with (synth.ms / "references.bib").open("a", encoding="utf-8") as handle:
        handle.write("@article{orphan2000,\n  year = {2000}\n}\n")
    finding = _one(_check(synth), "citation-uncited")
    assert finding.severity == "warning"


def test_cited_entry_without_a_year_is_a_warning(synth):
    synth.edit("references.bib", "year = {2010}", "note = {none}")
    assert _one(_check(synth), "reference-year").severity == "warning"


def test_required_section_missing(synth):
    synth.write_profile(_profile_data(sections=[
        {"title": "Introduction", "aliases": [], "required": True, "words_max": None},
        {"title": "Conclusions", "aliases": [], "required": True, "words_max": None},
    ]))
    assert _one(_check(synth), "section-missing").message.count("Conclusions") == 1


def test_strict_section_order(synth):
    synth.write_profile(_profile_data(sections=[
        {"title": "Introduction", "aliases": [], "required": True, "words_max": None},
        {"title": "Results", "aliases": [], "required": True, "words_max": None},
        {"title": "Methods", "aliases": [], "required": True, "words_max": None},
    ]))
    assert _one(_check(synth), "section-order").severity == "warning"


def test_declaration_without_text_or_section_is_an_error(synth):
    synth.write_profile(_profile_data(declarations=["ai-use"]))
    assert _one(_check(synth), "declaration-missing").severity == "error"


def test_declaration_met_by_metadata_text(synth):
    synth.write_profile(_profile_data(declarations=["coi", "funding", "ethics"]))
    assert "declaration-missing" not in _rules(_check(synth))


def test_orcid_and_credit_declarations_need_every_author(synth):
    data = yaml.safe_load((synth.ms / "metadata.yml").read_text(encoding="utf-8"))
    data["authors"][0].pop("orcid")
    data["authors"][0]["credit"] = []
    (synth.ms / "metadata.yml").write_text(yaml.safe_dump(data), encoding="utf-8")
    synth.write_profile(_profile_data(declarations=["orcid", "credit"]))
    found = [f for f in _check(synth).findings if f.rule == "declaration-missing"]
    assert sorted(f.where for f in found) == ["credit", "orcid"]


def test_template_class_and_bibliography_style_warnings(synth):
    synth.write_profile(_profile_data(latex_class="elsarticle", bib_style="elsarticle-num"))
    report = _check(synth)
    assert _one(report, "template-class").severity == "warning"
    assert _one(report, "bib-style").severity == "warning"


def test_profile_without_a_class_is_a_default_warning(synth):
    assert _one(_check(synth), "template-default").severity == "warning"


def test_word_first_profile_is_a_warning(synth):
    synth.write_profile(_profile_data(latex_class="article", word_first=True))
    assert _one(_check(synth), "word-first").severity == "warning"


def test_missing_profile_is_an_error(synth):
    (synth.profiles_dir / "journal-of-synthetic-neurology.yml").unlink()
    assert _one(_check(synth), "profile-missing").severity == "error"


def test_markdown_only_journal_is_legacy(synth):
    (synth.profiles_dir / "journal-of-synthetic-neurology.yml").unlink()
    (synth.profiles_dir / "journal-of-synthetic-neurology.md").write_text("# Old page\n", encoding="utf-8")
    assert _one(_check(synth), "profile-legacy").severity == "error"


def test_invalid_profile_is_an_error(synth):
    data = _profile_data()
    data["article_types"][0]["limits"]["words_main"] = "lots"
    synth.write_profile(data)
    assert "profile-invalid" in _rules(_check(synth), "error")


def test_stale_profile_is_a_warning_with_a_patched_clock(synth):
    data = _profile_data()
    data["provenance"]["sources"] = [{
        "id": "s4f1c2a9e07", "url": "https://guides.example.test/a", "retrieved_at": "2026-10-01",
        "retrieved_by": "gateway", "text_sha256": "0" * 64,
    }]
    synth.write_profile(data)
    assert _one(_check(synth, today=dt.date(2027, 6, 1)), "profile-stale").severity == "warning"
    assert "profile-stale" not in _rules(_check(synth, today=dt.date(2026, 10, 30)))


def test_unknown_article_type_is_an_error(synth):
    assert _one(_check(synth, article_type="letter-to-editor"), "article-type").severity == "error"


def test_metadata_identifier_problems_appear_in_the_report(synth):
    data = yaml.safe_load((synth.ms / "metadata.yml").read_text(encoding="utf-8"))
    data["authors"][0]["orcid"] = "0000-0002-1825-0098"
    (synth.ms / "metadata.yml").write_text(yaml.safe_dump(data), encoding="utf-8")
    finding = _one(_check(synth), "orcid-invalid")
    assert finding.where == "authors[0].orcid" and finding.severity == "error"


def _profile_data(**kw):
    limits = {k: kw.pop(k) for k in list(kw) if k in _LIMIT_NAMES}
    return make_profile(limits=limits, **kw)


_LIMIT_NAMES = {
    "words_main", "words_abstract", "words_title", "keywords_min", "keywords_max", "figures_max",
    "tables_max", "display_items_max", "references_min", "references_max", "highlights_max",
    "highlight_chars_max", "pages_max",
}
