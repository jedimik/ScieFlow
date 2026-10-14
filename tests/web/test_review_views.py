"""Shared review helpers for the web layer (ledger §1, §5 W1/W2).

Pure functions and the degradation rules: the scite toggle mapping, the one
form mapper that serves both the live preview and Apply, the hardened DOI
link helper, and the lazy import of the review package.
"""

import builtins
import copy

import pytest

from scieflow.web import review_views as rv


# --- W1: the scite toggle maps to one value (or nothing) -------------------------


@pytest.mark.parametrize("form, expected", [
    ({}, None),                                                        # sentinel absent: write nothing
    ({"scite_present": "1"}, "off"),                                   # sentinel, unchecked
    ({"scite_present": "1", "scite_use": "on"}, "ask"),                # checked
    ({"scite_present": "1", "scite_use": "on", "scite_no_ask": "on"}, "approved"),
    ({"scite_present": "1", "scite_no_ask": "on"}, "off"),             # no-ask without use
])
def test_scite_value_maps_each_case(form, expected):
    assert rv.scite_value(form) == expected


# --- W2: one mapper for preview and Apply -----------------------------------------


def test_pool_rows_keep_their_order_and_drop_blank_agents():
    form = {"pool_present": "1",
            "pool-2-agent": "codex-review", "pool-2-covers": "all",
            "pool-0-agent": "claude-paper", "pool-0-covers": "auto",
            "pool-1-agent": "", "pool-1-covers": "all",
            "pool-3-agent": "  "}
    changes = rv.review_changes_from_form(form)
    assert changes["pool"] == [{"agent": "claude-paper", "covers": "auto"},
                               {"agent": "codex-review", "covers": "all"}]


def test_an_empty_pool_is_an_empty_list_not_a_reset():
    assert rv.review_changes_from_form({"pool_present": "1"})["pool"] == []


def test_reset_to_default_sends_only_the_pool_key_as_none():
    form = {"pool_present": "1", "pool_reset": "1", "pool-0-agent": "claude-paper",
            "reviews_per_draft": "5", "scite_present": "1", "scite_use": "on"}
    assert rv.review_changes_from_form(form) == {"pool": None}


def test_the_pool_is_untouched_without_its_sentinel():
    changes = rv.review_changes_from_form({"pool-0-agent": "claude-paper"})
    assert "pool" not in changes


def test_an_inheriting_pool_with_only_blank_rows_is_not_written():
    # Owner finding O1: a run that inherits research.draft-reviewers must keep
    # inheriting unless the user fills a row.
    form = {"pool_present": "1", "pool_inherits": "1", "pool-0-agent": "",
            "pool-1-agent": ""}
    assert "pool" not in rv.review_changes_from_form(form)


def test_an_inheriting_pool_with_a_filled_row_is_written():
    form = {"pool_present": "1", "pool_inherits": "1", "pool-0-agent": "agy",
            "pool-1-agent": ""}
    assert rv.review_changes_from_form(form)["pool"] == [{"agent": "agy"}]


def test_a_blank_covers_choice_is_the_core_default_not_all():
    form = {"pool_present": "1", "pool-0-agent": "claude-paper", "pool-0-covers": ""}
    assert rv.review_changes_from_form(form)["pool"] == [{"agent": "claude-paper"}]


def test_switches_are_false_when_unchecked_and_absent_when_not_shown():
    both_off = rv.review_changes_from_form({"switches_present": "1"})
    assert both_off["allow_same_family_review"] is False
    assert both_off["allow_self_review"] is False
    one_on = rv.review_changes_from_form({"switches_present": "1", "allow_self_review": "on"})
    assert one_on["allow_self_review"] is True and one_on["allow_same_family_review"] is False
    assert "allow_self_review" not in rv.review_changes_from_form({})


@pytest.mark.parametrize("raw", ["lots", "", "0", "-1", "2.5"])
def test_a_garbage_reviews_per_draft_is_refused_by_the_mapper(raw):
    with pytest.raises(rv.FormMapError):
        rv.review_changes_from_form({"reviews_per_draft": raw})


def test_a_valid_reviews_per_draft_is_an_int():
    assert rv.review_changes_from_form({"reviews_per_draft": " 3 "})["reviews_per_draft"] == 3


def test_the_scite_key_is_omitted_when_scite_is_not_enabled():
    form = {"scite_present": "1", "scite_use": "on"}
    assert rv.review_changes_from_form(form, scite_enabled=False).get("scite") is None
    assert rv.review_changes_from_form(form, scite_enabled=True)["scite"] == "ask"
    assert "scite" not in rv.review_changes_from_form({})


# --- §1: hardened DOI link helper -----------------------------------------------


def test_a_plain_doi_becomes_a_doi_org_link():
    assert rv.doi_link("10.1000/abc.def") == "https://doi.org/10.1000/abc.def"


@pytest.mark.parametrize("doi", [
    "10.1000/a?b",            # query smuggling
    "10.1000/a#b",            # fragment smuggling
    "10.1000/../x",           # path traversal
    "javascript:alert(1)",    # scheme
    "not a doi",
    "10.1000/a b",            # whitespace
    "",
    None,
])
def test_a_non_doi_or_unsafe_value_gets_no_link(doi):
    assert rv.doi_link(doi) is None


# --- §1: lazy import and degradation ----------------------------------------------


def test_a_core_refusal_is_degraded_to_a_notice(monkeypatch):
    from scieflow.core.review import service as review_service

    def refuse(project, slug):
        raise ValueError("review.pool: 'x' listed more than once")

    monkeypatch.setattr(review_service, "review_settings", refuse)
    value, notice = rv.review_call("review_settings", None, "rv1")
    assert value is None
    assert "listed more than once" in notice


def test_a_missing_research_extra_is_a_notice_not_a_crash(monkeypatch):
    real_import = builtins.__import__

    def without_review(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("scieflow.core.review"):
            raise ImportError("No module named 'jsonschema'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", without_review)
    assert rv._review() is None
    assert rv.review_call("review_settings", None, "rv1") == (None, rv.NEEDS_EXTRA)


def test_a_deeply_nested_review_file_is_a_notice_not_a_crash(monkeypatch):
    """R4 C2: json.loads raises RecursionError on deep nesting, which is not a ValueError."""
    from scieflow.core.review import service as review_service

    def deep(project, slug):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(review_service, "rounds", deep)
    value, notice = rv.review_call("rounds", None, "rv1")
    assert value is None
    assert "nested too deeply" in notice


def test_an_unreadable_outcome_author_gets_its_own_notice_for_recursion():
    def deep(ws, n, author):
        raise RecursionError("maximum recursion depth exceeded")

    entry = rv._outcome_author(None, 1, "claude-paper", deep)
    assert entry["accepted"] == [] and "could not be read" in entry["notice"]


def test_a_doi_with_non_ascii_digits_is_plain_text():
    """R4 C6.1: \\d must not match Arabic-Indic digits in the DOI helper."""
    assert rv.doi_link("10.\u0661\u0662\u0663\u0664/x") is None
    assert rv.doi_link("10.1234/x") == "https://doi.org/10.1234/x"


def test_clean_pass_keeps_a_well_formed_pass_unchanged():
    doc = {"major": [{"id": "M1", "problem": "p",
                      "evidence": [{"quote": "q", "doi": "10.1/x"}]}],
           "minor": [{"id": "m1", "comment": "c"}]}
    clean, problems = rv.clean_pass(doc)
    assert problems == []
    assert clean == doc


def test_clean_pass_drops_wrong_types_reports_them_and_never_mutates_its_input():
    doc = {"major": 5, "minor": [{"id": "m1", "comment": ["x"]}, "stray"]}
    before = copy.deepcopy(doc)
    clean, problems = rv.clean_pass(doc)
    assert doc == before
    assert clean["major"] == []
    assert clean["minor"] == [{"id": "m1", "comment": ""}]
    assert problems == ["major", "minor[0].comment", "minor[1]"]


def test_clean_pass_coerces_scalars_to_text_without_a_problem():
    clean, problems = rv.clean_pass({"major": [{"id": 7, "problem": None,
                                                "evidence": [{"doi": 5}]}]})
    assert problems == []
    assert clean["major"][0] == {"id": "7", "problem": "",
                                 "evidence": [{"doi": "5"}]}


# --- R5 N1: evidence is cleaned two levels deep, never recursively ----------------------


def nested_evidence(levels):
    """An evidence entry nested `levels` times, built by a loop and never by recursion."""
    node = {"quote": "q"}
    for _ in range(levels):
        node = {"evidence": [node]}
    return {"major": [{"id": "M1", "problem": "p", "evidence": [node]}]}


@pytest.mark.parametrize("levels", [700, 3000])
def test_clean_pass_survives_evidence_nested_deeply(levels):
    clean, problems = rv.clean_pass(nested_evidence(levels))
    assert problems == ["major[0].evidence[0].evidence"]
    assert clean["major"][0]["evidence"] == [{}]


def test_clean_pass_drops_a_nested_container_inside_an_evidence_entry():
    doc = {"major": [{"id": "M1", "evidence": [{"quote": "q", "meta": {"a": 1}}]}]}
    clean, problems = rv.clean_pass(doc)
    assert problems == ["major[0].evidence[0].meta"]
    assert clean["major"][0]["evidence"] == [{"quote": "q"}]


# --- R5 NIT-1: the problems list is capped and its names truncated ----------------------


def test_clean_pass_names_ten_problems_then_says_how_many_more():
    clean, problems = rv.clean_pass({"major": [1] * 25})
    assert len(problems) == 11
    assert problems[0] == "major[0]"
    assert problems[-1] == "and 15 more"


def test_a_long_problem_name_is_truncated_to_sixty_characters():
    key = "k" * 500
    clean, problems = rv.clean_pass({"minor": [{"id": "m1", key: {"x": 1}}]})
    assert len(problems) == 1
    assert problems[0].startswith("minor[0].kkk") and problems[0].endswith("\u2026")
    assert len(problems[0]) <= 61

