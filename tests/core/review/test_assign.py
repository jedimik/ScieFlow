"""Reviewer assignment: the pure planner, its persistence and `scieflow review assign`."""

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from scieflow.core.project import Project
from scieflow.core.review import assign
from scieflow.core.review.cli import review
from scieflow.core.review.settings import DEFAULTS
from scieflow.research import validate as research_validate

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "src" / "scieflow" / "research" / "schemas"
                     / "review-assignment.schema.json").read_text())

FAMILIES = {
    "claude-paper": "claude", "claude": "claude",
    "codex-paper": "openai", "codex-review": "openai", "codex": "openai",
    "agy": "gemini", "stub": "stub",
}


def settings(**changes) -> dict:
    return {**DEFAULTS, **changes}


def pool(*names, covers=None) -> list[dict]:
    return [{"agent": n, **({"covers": covers} if covers else {})} for n in names]


def pairs(plan: dict) -> set[tuple[str, str]]:
    return {(p["reviewer"], p["author"]) for p in plan["pairs"]}


def reviewers_of(plan: dict, author: str) -> list[str]:
    return [p["reviewer"] for p in plan["pairs"] if p["author"] == author]


def load(plan: dict) -> Counter:
    return Counter(p["reviewer"] for p in plan["pairs"])


def valid(plan: dict) -> None:
    """The plan as `write()` persists it (with its round) validates."""
    errors = list(Draft202012Validator(SCHEMA).iter_errors({"round": 1, **plan}))
    assert not errors, [e.message for e in errors]


# --- table tests ---------------------------------------------------------------


def test_two_authors_review_each_other():
    authors = ["codex-paper", "claude-paper"]
    plan = assign.plan(authors, pool(*authors), settings(), FAMILIES)
    assert pairs(plan) == {("codex-paper", "claude-paper"), ("claude-paper", "codex-paper")}
    # Only one eligible reviewer per draft: reported, not an error.
    assert plan["coverage"]["claude-paper"] == {"reviewers": 1, "target": 2, "shortfall": 1}
    assert plan["warnings"]
    valid(plan)


def test_three_authors_each_draft_gets_two_reviewers_nobody_self():
    authors = ["claude-paper", "codex-paper", "agy"]
    plan = assign.plan(authors, pool(*authors), settings(), FAMILIES)
    for author in authors:
        got = reviewers_of(plan, author)
        assert len(got) == 2 and author not in got
    counts = load(plan)
    assert max(counts.values()) - min(counts.values()) <= 1
    assert all(not p["self"] for p in plan["pairs"])
    assert plan["warnings"] == []
    valid(plan)


def test_extra_reviewer_covers_all_reviews_every_draft():
    authors = ["claude-paper", "codex-paper", "agy"]
    plan = assign.plan(authors, pool(*authors) + pool("stub", covers="all"),
                       settings(), FAMILIES)
    assert {a for r, a in pairs(plan) if r == "stub"} == set(authors)
    # On top of the authors, who still review each other's drafts.
    for author in authors:
        assert len([r for r in reviewers_of(plan, author) if r != "stub"]) == 2
    valid(plan)


def test_extra_reviewer_defaults_to_covers_all_and_skips_its_own_family():
    authors = ["claude-paper", "codex-paper", "agy"]
    plan = assign.plan(authors, pool(*authors, "codex-review"), settings(), FAMILIES)
    covers = {r["agent"]: r["covers"] for r in plan["reviewers"]}
    assert covers == {"claude-paper": "auto", "codex-paper": "auto", "agy": "auto",
                      "codex-review": "all"}
    # codex-review (openai) reviews every eligible draft: not codex-paper's.
    assert {a for r, a in pairs(plan) if r == "codex-review"} == {"claude-paper", "agy"}


def test_all_same_family_with_switch_off_is_an_error_naming_the_switch():
    fams = {"a": "claude", "b": "claude", "c": "claude"}
    with pytest.raises(assign.AssignmentError, match="allow_same_family_review"):
        assign.plan(["a", "b", "c"], pool("a", "b", "c"), settings(), fams)


def test_all_same_family_with_switch_on_is_allowed():
    fams = {"a": "claude", "b": "claude", "c": "claude"}
    plan = assign.plan(["a", "b", "c"], pool("a", "b", "c"),
                       settings(allow_same_family_review=True), fams)
    for author in "abc":
        got = reviewers_of(plan, author)
        assert len(got) == 2 and author not in got
    assert all(p["same_family"] and not p["self"] for p in plan["pairs"])
    valid(plan)


def test_self_review_off_is_an_error_naming_that_switch():
    with pytest.raises(assign.AssignmentError, match="allow_self_review"):
        assign.plan(["claude-paper"], pool("claude-paper"), settings(), FAMILIES)


def test_self_review_on_makes_self_pairs_eligible_and_marked():
    plan = assign.plan(["claude-paper"], pool("claude-paper"),
                       settings(allow_self_review=True), FAMILIES)
    assert plan["pairs"] == [{
        "reviewer": "claude-paper", "author": "claude-paper",
        "reviewer_family": "claude", "author_family": "claude",
        "covers": "auto", "self": True, "same_family": True, "fresh_dispatch": True,
    }]
    valid(plan)


def test_self_review_prefers_other_reviewers_first():
    authors = ["claude-paper", "codex-paper"]
    plan = assign.plan(authors, pool(*authors), settings(allow_self_review=True,
                                                          reviews_per_draft=1), FAMILIES)
    assert pairs(plan) == {("codex-paper", "claude-paper"), ("claude-paper", "codex-paper")}


def test_every_pair_is_a_fresh_dispatch():
    authors = ["claude-paper", "codex-paper", "agy"]
    plan = assign.plan(authors, pool(*authors, "stub"), settings(), FAMILIES)
    assert plan["pairs"] and all(p["fresh_dispatch"] is True for p in plan["pairs"])


def test_balanced_within_one_for_larger_runs():
    fams = {f"a{i}": f"f{i}" for i in range(7)}
    for n in range(3, 8):
        for rpd in (1, 2, 3):
            authors = [f"a{i}" for i in range(n)]
            plan = assign.plan(authors, pool(*authors), settings(reviews_per_draft=rpd), fams)
            counts = load(plan)
            assert all(len(reviewers_of(plan, a)) == min(rpd, n - 1) for a in authors)
            assert max(counts.values()) - min(counts.values()) <= 1, (n, rpd, counts)


def test_pool_member_without_a_family_is_refused():
    with pytest.raises(assign.AssignmentError, match="model_family"):
        assign.plan(["claude-paper"], pool("claude-paper", "mystery"), settings(),
                    FAMILIES)


def test_empty_pool_is_an_error():
    with pytest.raises(assign.AssignmentError, match="no eligible reviewer"):
        assign.plan(["claude-paper"], [], settings(), FAMILIES)


def test_no_authors_is_an_error():
    with pytest.raises(assign.AssignmentError, match="no draft authors"):
        assign.plan([], pool("claude-paper"), settings(), FAMILIES)


# --- matrix_override -----------------------------------------------------------


def test_matrix_override_is_respected():
    authors = ["claude-paper", "codex-paper", "agy"]
    override = {"codex-review": ["claude-paper", "agy"], "claude-paper": ["codex-paper"]}
    plan = assign.plan(authors, pool(*authors), settings(matrix_override=override), FAMILIES)
    assert pairs(plan) == {("codex-review", "claude-paper"), ("codex-review", "agy"),
                           ("claude-paper", "codex-paper")}
    assert plan["source"] == "matrix_override"
    assert plan["coverage"]["agy"]["shortfall"] == 1
    valid(plan)


def test_matrix_override_same_family_pair_is_refused_naming_the_switch():
    authors = ["claude-paper", "codex-paper"]
    override = {"codex-review": ["codex-paper"], "codex-paper": ["claude-paper"]}
    with pytest.raises(assign.AssignmentError, match="allow_same_family_review"):
        assign.plan(authors, pool(*authors), settings(matrix_override=override), FAMILIES)


def test_matrix_override_self_pair_is_refused_naming_the_switch():
    authors = ["claude-paper", "codex-paper"]
    override = {"claude-paper": ["claude-paper", "codex-paper"], "codex-paper": ["claude-paper"]}
    with pytest.raises(assign.AssignmentError, match="allow_self_review"):
        assign.plan(authors, pool(*authors), settings(matrix_override=override), FAMILIES)


def test_matrix_override_unknown_author_is_refused():
    override = {"codex-review": ["claude-paper", "nobody"]}
    with pytest.raises(assign.AssignmentError, match="nobody"):
        assign.plan(["claude-paper"], pool("claude-paper"),
                    settings(matrix_override=override), FAMILIES)


def test_matrix_override_leaving_a_draft_unreviewed_is_an_error():
    authors = ["claude-paper", "codex-paper"]
    override = {"codex-review": ["claude-paper"]}
    with pytest.raises(assign.AssignmentError, match="codex-paper"):
        assign.plan(authors, pool(*authors), settings(matrix_override=override), FAMILIES)


def test_matrix_override_reviewer_without_family_is_refused():
    override = {"mystery": ["claude-paper"]}
    with pytest.raises(assign.AssignmentError, match="model_family"):
        assign.plan(["claude-paper"], pool("claude-paper"),
                    settings(matrix_override=override), FAMILIES)


# --- determinism, schema, persistence ------------------------------------------


def test_identical_input_gives_byte_identical_json():
    authors = ["claude-paper", "codex-paper", "agy"]
    args = (authors, pool(*authors, "stub"), settings(), FAMILIES)
    first = assign.dumps(assign.plan(*args))
    for _ in range(5):
        assert assign.dumps(assign.plan(*args)) == first


def test_input_order_of_families_does_not_matter():
    authors = ["claude-paper", "codex-paper", "agy"]
    shuffled = dict(reversed(list(FAMILIES.items())))
    assert (assign.dumps(assign.plan(authors, pool(*authors), settings(), FAMILIES))
            == assign.dumps(assign.plan(authors, pool(*authors), settings(), shuffled)))


def test_write_persists_round_assignment(tmp_path):
    authors = ["claude-paper", "codex-paper", "agy"]
    plan = assign.plan(authors, pool(*authors), settings(), FAMILIES)
    path = assign.write(tmp_path, 2, plan)
    assert path == tmp_path / "review" / "round-2" / "assignment.json"
    data = json.loads(path.read_text())
    assert data["round"] == 2 and data["pairs"] == plan["pairs"]
    valid(data)


def test_write_refuses_a_bad_round(tmp_path):
    plan = assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)
    with pytest.raises(assign.AssignmentError, match="round"):
        assign.write(tmp_path, 0, plan)


def test_schema_is_known_to_research_validate(tmp_path, capsys):
    plan = assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)
    path = assign.write(tmp_path, 1, plan)
    research_validate.main([str(path), "--schema", "review-assignment"])
    assert capsys.readouterr().out.strip() == "OK"


def test_schema_refuses_a_pair_without_fresh_dispatch():
    plan = assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)
    plan["pairs"][0]["fresh_dispatch"] = False
    assert list(Draft202012Validator(SCHEMA).iter_errors(plan))


# --- CLI -----------------------------------------------------------------------


def _set_authors(repo, write_ws, data):
    write_ws(repo, data)


def test_cli_assign_json_writes_and_prints(repo, write_ws):
    # conftest registry: claude (claude), codex (openai), agy (gemini, support).
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "codex"]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1", "--json"])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    written = repo / "workspace" / "run-1" / "review" / "round-1" / "assignment.json"
    assert json.loads(written.read_text()) == printed
    assert pairs(printed) == {("claude", "codex"), ("codex", "claude")}


def test_cli_assign_uses_review_pool_setting(repo, write_ws):
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "codex"]},
                    "review": {"pool": [{"agent": "claude"}, {"agent": "codex"},
                                        {"agent": "agy", "covers": "all"}]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1", "--json"])
    assert result.exit_code == 0, result.output
    assert {a for r, a in pairs(json.loads(result.output)) if r == "agy"} == {"claude", "codex"}


def test_cli_assign_table_output(repo, write_ws):
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "codex"]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1"])
    assert result.exit_code == 0, result.output
    assert "claude" in result.output and "codex" in result.output
    assert "assignment.json" in result.output


def test_cli_assign_error_names_switch_and_writes_nothing(repo, write_ws):
    registry = yaml.safe_load((repo / "config" / "agents.yml").read_text())
    registry["agents"]["codex"]["model_family"] = "claude"
    (repo / "config" / "agents.yml").write_text(yaml.safe_dump(registry))
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "codex"]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1"])
    assert result.exit_code != 0
    assert "allow_same_family_review" in result.output
    assert not (repo / "workspace" / "run-1" / "review").exists()


def test_cli_assign_refuses_disabled_pool_member(repo, write_ws):
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "codex"],
                                    "research.draft-reviewers": ["claude", "codex", "off"]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1"])
    assert result.exit_code != 0
    assert "off" in result.output and "disabled" in result.output


def test_cli_assign_unknown_run(repo):
    result = CliRunner().invoke(review, ["assign", "nope", "--round", "1"])
    assert result.exit_code != 0


# --- review findings -------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../evil", "a/b", "x\ny", "a\\b", "..", ".", "\u202eab", "", None, 5])
def test_path_shaped_or_non_string_author_is_refused(bad):
    fams = {**FAMILIES, bad: "zz"} if isinstance(bad, str) else FAMILIES
    with pytest.raises(assign.AssignmentError):
        assign.plan(["claude-paper", bad], pool("codex-paper"), settings(), fams)


@pytest.mark.parametrize("bad", ["../evil", "..", "a/b", "\u202eab"])
def test_path_shaped_reviewer_is_refused(bad):
    fams = {**FAMILIES, bad: "zz"}
    with pytest.raises(assign.AssignmentError):
        assign.plan(["claude-paper"], pool(bad), settings(), fams)
    with pytest.raises(assign.AssignmentError):
        assign.plan(["claude-paper"], [], settings(matrix_override={bad: ["claude-paper"]}), fams)


def test_write_refuses_a_plan_that_breaks_the_schema(tmp_path):
    plan = assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)
    plan["pairs"][0]["author"] = "../evil"
    with pytest.raises(assign.AssignmentError, match="schema"):
        assign.write(tmp_path, 1, plan)
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("name", ["..", ".", "a\u202eb", "x\u0007"])
def test_schema_refuses_dot_names_and_control_characters(name):
    plan = assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)
    plan["pairs"][0]["author"] = name
    assert list(Draft202012Validator(SCHEMA).iter_errors({"round": 1, **plan}))


@pytest.mark.parametrize("where", ["top", "settings", "authors", "reviewers", "pairs", "coverage"])
def test_schema_refuses_unknown_keys(where):
    plan = {"round": 1, **assign.plan(["claude-paper"], pool("codex-paper"), settings(), FAMILIES)}
    target = {"top": plan, "settings": plan["settings"], "authors": plan["authors"][0],
              "reviewers": plan["reviewers"][0], "pairs": plan["pairs"][0],
              "coverage": plan["coverage"]["claude-paper"]}[where]
    target["typo"] = 1
    assert list(Draft202012Validator(SCHEMA).iter_errors(plan))


def test_same_family_error_does_not_suggest_self_review_when_family_switch_fixes_it():
    fams = {"a": "claude", "b": "claude", "c": "claude"}
    with pytest.raises(assign.AssignmentError) as exc:
        assign.plan(["a", "b", "c"], pool("a", "b", "c"), settings(), fams)
    assert "allow_same_family_review" in str(exc.value)
    assert "allow_self_review" not in str(exc.value)


def test_cli_unknown_author_is_reported_as_an_author(repo, write_ws):
    write_ws(repo, {"assignments": {"research.draft-authors": ["claude", "ghost"]}})
    result = CliRunner().invoke(review, ["assign", "run-1", "--round", "1"])
    assert result.exit_code != 0
    assert "draft author 'ghost'" in result.output
