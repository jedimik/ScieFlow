"""validate(): one failing case per rule of M-F 5.5, the valid fixture passes. Synthetic data only."""

import datetime as dt
import hashlib

from scieflow.research.journal import profile


def _errors(problems):
    return {(p.rule, p.path) for p in problems if p.level == "error"}


def _rules(problems):
    return {p.rule for p in problems}


def _check(synth, data, **kw):
    kw.setdefault("run_dir", synth.run_dir)
    kw.setdefault("today", dt.date(2026, 10, 29))
    return profile.validate(data, root=synth.root, **kw)


def _evidence(data, field):
    return [e for e in data["evidence"] if e["field"] != field]


def test_valid_fixture_has_no_problems(synth, profile_data):
    assert _check(synth, profile_data()) == []


def test_problem_to_dict_names_rule_path_message_and_level(synth, profile_data):
    data = profile_data()
    data["scope"]["bogus"] = "x"
    (problem,) = _check(synth, data)
    assert problem.to_dict() == {"rule": "unknown-key", "path": "scope.bogus",
                                 "message": problem.message, "level": "error"}
    assert "scope.bogus" in str(problem)


def test_missing_key(synth, profile_data):
    data = profile_data()
    del data["identity"]["issns"]
    assert ("missing-key", "identity.issns") in _errors(_check(synth, data))


def test_missing_required_top_key(synth, profile_data):
    data = profile_data()
    del data["conflicts"]
    assert ("missing-key", "conflicts") in _errors(_check(synth, data))


def test_type_error_on_string_where_integer_is_required(synth, profile_data):
    data = profile_data()
    data["article_types"][0]["limits"]["words_main"] = "3000"
    assert ("type", "article_types[0].limits.words_main") in _errors(_check(synth, data))


def test_type_error_on_bool_where_integer_is_required(synth, profile_data):
    data = profile_data()
    data["version"] = True
    assert ("type", "version") in _errors(_check(synth, data))


def test_enum_error(synth, profile_data):
    data = profile_data()
    data["review"]["type"] = "triple-blind"
    assert ("enum", "review.type") in _errors(_check(synth, data))


def test_range_error_on_negative_limit(synth, profile_data):
    data = profile_data()
    data["article_types"][0]["limits"]["words_main"] = -1
    assert ("range", "article_types[0].limits.words_main") in _errors(_check(synth, data))


def test_range_error_on_max_age_days(synth, profile_data):
    data = profile_data()
    data["provenance"]["max_age_days"] = 0
    assert ("range", "provenance.max_age_days") in _errors(_check(synth, data))


def test_figures_range_and_format_errors(synth, profile_data):
    data = profile_data()
    data["figures"] = {"single_column_mm": 500, "double_column_mm": 183, "font_pt": 8,
                       "dpi": 300, "formats": ["gif"]}
    errors = _errors(_check(synth, data))
    assert ("range", "figures.single_column_mm") in errors
    assert ("enum", "figures.formats") in errors


def test_article_type_missing_is_refused(synth, profile_data):
    data = profile_data()
    data["article_types"] = []
    assert ("article-type", "article_types") in _errors(_check(synth, data))


def test_article_type_duplicate_and_bad_id(synth, profile_data):
    data = profile_data()
    data["article_types"].append(dict(data["article_types"][0]))
    assert "article-type" in _rules(_check(synth, data))
    data = profile_data()
    data["article_types"][0]["id"] = "Bad Id"
    assert ("article-type", "article_types[0].id") in _errors(_check(synth, data))


def test_evidence_missing_for_a_non_null_integer(synth, profile_data):
    data = profile_data()
    data["evidence"] = _evidence(data, "article_types[0].limits.words_abstract")
    assert ("evidence-missing", "article_types[0].limits.words_abstract") in _errors(_check(synth, data))


def test_evidence_missing_for_a_bool_needs_its_exact_path(synth, profile_data):
    data = profile_data()
    # The parent mapping's evidence does not cover a boolean leaf.
    data["evidence"] = _evidence(data, "article_types[0].abstract.structured")
    assert ("evidence-missing", "article_types[0].abstract.structured") in _errors(_check(synth, data))


def test_evidence_source_unknown(synth, profile_data):
    data = profile_data()
    data["evidence"][0]["source"] = "sffffffffff"
    assert ("evidence-source", "evidence[0].source") in _errors(_check(synth, data))


def test_evidence_source_hash_mismatch(synth, profile_data):
    text = (synth.run_dir / "journal" / "sources" / "s4f1c2a9e07.txt")
    text.write_bytes(text.read_bytes() + b"Edited after the row was recorded.\n")
    assert "evidence-source" in _rules(_check(synth, profile_data()))


def test_evidence_quote_not_verbatim(synth, profile_data):
    data = profile_data()
    data["evidence"][1]["quote"] = "Research articles may contain up to 3000 words in the main text, excluding figures."
    assert ("evidence-quote", "evidence[1].quote") in _errors(_check(synth, data))


def test_evidence_quote_too_short_and_not_a_line(synth, profile_data):
    data = profile_data()
    data["evidence"][2]["quote"] = "3000 words"
    assert ("evidence-quote", "evidence[2].quote") in _errors(_check(synth, data))


def test_evidence_number_absent_from_quote(synth, profile_data):
    data = profile_data()
    data["article_types"][0]["limits"]["words_main"] = 2500
    assert ("evidence-number", "article_types[0].limits.words_main") in _errors(_check(synth, data))


def test_evidence_name_absent_from_quote(synth, profile_data):
    data = profile_data()
    data["reporting_guidelines"]["by_study_type"]["rct"] = ["consort-2010"]
    data["evidence"] = _evidence(data, "reporting_guidelines.by_study_type")
    data["evidence"].append({"field": "reporting_guidelines.by_study_type", "source": profile_data()["evidence"][0]["source"],
                             "quote": "Review: the review is double-blind."})
    assert ("evidence-name", "reporting_guidelines.by_study_type.rct") in _errors(_check(synth, data))


def test_guideline_unknown(synth, profile_data):
    data = profile_data()
    data["reporting_guidelines"]["by_study_type"]["rct"] = ["no-such-guideline"]
    assert ("guideline-unknown", "reporting_guidelines.by_study_type.rct[0]") in _errors(_check(synth, data))


def test_study_type_unknown(synth, profile_data):
    data = profile_data()
    data["reporting_guidelines"]["by_study_type"]["martian"] = ["consort-2010"]
    assert ("study-type-unknown", "reporting_guidelines.by_study_type.martian") in _errors(_check(synth, data))


def test_conflict_must_choose_the_field_value_and_name_two_statements(synth, profile_data):
    data = profile_data()
    data["conflicts"] = [{
        "field": "article_types[0].limits.words_main", "chosen": 2500, "reason": "test",
        "statements": [
            {"source": "s4f1c2a9e07", "quote": "Research articles may contain up to 3000 words in the main text"},
            {"source": "s9b03d55e2a", "quote": "Research articles are limited to 3,000 words of main text."},
        ]}]
    assert ("conflict", "conflicts[0]") in _errors(_check(synth, data))
    data["conflicts"][0]["chosen"] = 3000
    data["conflicts"][0]["statements"] = data["conflicts"][0]["statements"][:1]
    assert ("conflict", "conflicts[0]") in _errors(_check(synth, data))


def test_conflict_with_two_verbatim_statements_passes(synth, profile_data):
    data = profile_data()
    data["conflicts"] = [{
        "field": "article_types[0].limits.words_main", "chosen": 3000, "reason": "the guide page and the policy page agree",
        "statements": [
            {"source": "s4f1c2a9e07", "quote": "Research articles may contain up to 3000 words in the main text"},
            {"source": "s9b03d55e2a", "quote": "Research articles are limited to 3,000 words of main text."},
        ]}]
    assert _errors(_check(synth, data)) == set()


def test_stale_evidence_is_a_warning(synth, profile_data):
    data = profile_data()
    data["provenance"]["sources"][0]["retrieved_at"] = "2026-01-01"
    problems = _check(synth, data)
    assert [(p.rule, p.level) for p in problems] == [("stale", "warning")]


def test_sources_unavailable_warns_and_skips_verbatim_checks(synth, profile_data):
    data = profile_data()
    # Not verbatim, but the page is not on disk: only the quote-against-page checks are skipped.
    data["evidence"][2]["quote"] = "Research articles may contain up to 3000 words in the main text, excluding figures."
    problems = _check(synth, data, run_dir=None)
    assert "sources-unavailable" in _rules(problems)
    assert not [p for p in problems if p.rule.startswith("evidence-") and p.rule != "evidence-missing"]


def test_facts_blocks_have_no_evidence_requirement(synth, profile_data):
    data = profile_data()
    data["identity"]["publisher"] = "Invented Press"
    assert _check(synth, data) == []


def test_text_hash_helper_matches_fixture_rows(synth):
    raw = (synth.run_dir / "journal" / "sources" / "s4f1c2a9e07.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == profile.sha256_file(
        synth.run_dir / "journal" / "sources" / "s4f1c2a9e07.txt")
