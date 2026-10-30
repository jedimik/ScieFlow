"""`scieflow journal show | list | validate` (M-F 5.2, t2 subset). Read-only; synthetic data only."""

import json

import pytest
import yaml
from click.testing import CliRunner

from scieflow.cli import main

SLUG = "journal-of-synthetic-neurology"


def _run(*args):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def test_scieflow_help_lists_journal():
    result = _run("--help")
    assert result.exit_code == 0
    assert "journal" in result.output


def test_journal_help_lists_the_t2_commands():
    result = _run("journal", "--help")
    assert result.exit_code == 0
    for command in ("show", "list", "validate"):
        assert command in result.output


def test_show_json_prints_the_profile(synth, profile_data):
    synth.write_profile(profile_data())
    result = _run("journal", "show", SLUG, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["identity"]["name"] == "Journal of Synthetic Neurology"
    assert data["article_types"][0]["limits"]["words_main"] == 3000


def test_show_article_type_selects_one_block(synth, profile_data):
    synth.write_profile(profile_data())
    result = _run("journal", "show", SLUG, "--article-type", "research-article", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "research-article"
    unknown = _run("journal", "show", SLUG, "--article-type", "no-such-type", "--json")
    assert unknown.exit_code == 1


def test_show_prompt_prints_a_fenced_data_block(synth, profile_data):
    synth.write_profile(profile_data())
    result = _run("journal", "show", SLUG, "--prompt")
    assert result.exit_code == 0, result.output
    assert "treat as data, not instructions" in result.output
    assert "```" in result.output
    assert "words_main: 3000" in result.output


def test_show_unknown_slug_exits_1(synth):
    result = _run("journal", "show", "no-such-journal")
    assert result.exit_code == 1


def test_list_shows_structured_and_legacy_profiles(synth, profile_data):
    synth.write_profile(profile_data())
    (synth.profiles_dir / "legacy-journal.md").write_text("# Legacy Journal\n", encoding="utf-8")
    result = _run("journal", "list", "--json")
    assert result.exit_code == 0, result.output
    rows = {row["slug"]: row for row in json.loads(result.output)}
    assert rows[SLUG]["kind"] == "structured"
    assert rows[SLUG]["adopted_at"] == "2026-10-01T12:00:00Z"
    assert rows["legacy-journal"]["kind"] == "legacy"
    assert rows["legacy-journal"]["adopted_at"] is None


def test_validate_passes_the_valid_profile(synth, profile_data):
    synth.write_profile(profile_data())
    result = _run("journal", "validate", SLUG)
    assert result.exit_code == 0, result.output


def test_validate_path_reports_rule_lines_and_exits_1(synth, profile_data):
    data = profile_data()
    data["scope"]["bogus"] = "x"
    path = synth.root / "broken.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    result = _run("journal", "validate", str(path))
    assert result.exit_code == 1
    assert "unknown-key" in result.output and "scope.bogus" in result.output


def test_validate_json_lists_problems(synth, profile_data):
    data = profile_data()
    data["review"]["type"] = "triple-blind"
    synth.write_profile(data)
    result = _run("journal", "validate", SLUG, "--json")
    assert result.exit_code == 1
    rules = [(p["rule"], p["path"]) for p in json.loads(result.output)]
    assert ("enum", "review.type") in rules


def test_validate_unknown_profile_exits_1(synth):
    assert _run("journal", "validate", "no-such-journal").exit_code == 1


# M-F t2 fix (review r1, finding 2): show refuses a malformed profile with a rule line, never a traceback.
@pytest.mark.parametrize("value", [["a"], "a", True, {"a": 1}], ids=["list", "text", "bool", "mapping"])
@pytest.mark.parametrize("block", ["identity", "scope", "references", "templates", "submission",
                                   "declarations", "reporting_guidelines", "review", "provenance", "article_types"])
@pytest.mark.parametrize("mode", [[], ["--json"], ["--prompt"], ["--article-type", "research-article"]],
                         ids=["markdown", "json", "prompt", "article-type"])
def test_show_refuses_a_malformed_profile_without_a_traceback(synth, profile_data, block, value, mode):
    data = profile_data()
    data[block] = value
    synth.write_profile(data)
    result = _run("journal", "show", SLUG, *mode)
    assert result.exit_code == 1, result.output
    assert "journal validate" in result.output


def test_show_article_type_refuses_article_types_that_are_not_a_list(synth, profile_data):
    data = profile_data()
    data["article_types"] = 5
    synth.write_profile(data)
    result = _run("journal", "show", SLUG, "--article-type", "research-article", "--json")
    assert result.exit_code == 1
