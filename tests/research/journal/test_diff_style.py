"""M-F t4 (plan 5.6): `scieflow journal diff` and `scieflow journal style`. Synthetic data only."""

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from scieflow.cli import main

RUN = "synth-run"
SLUG = "journal-of-synthetic-neurology"
REPO = Path(__file__).resolve().parents[3]
FIGURES = {"single_column_mm": 89, "double_column_mm": 183, "font_pt": 8, "dpi": 300,
           "formats": ["png", "svg"]}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _diff(*args):
    return CliRunner().invoke(main, ["journal", "diff", SLUG, RUN, *args])


def _changed_draft(profile_data) -> dict:
    draft = profile_data()
    draft["article_types"][0]["limits"]["words_main"] = 3500      # changed
    draft["review"]["criteria"] = ["Clarity"]                       # added (list item)
    del draft["references"]["natbib"]                               # removed
    return draft


def test_diff_names_the_changed_added_and_removed_fields(synth, profile_data):
    _write(synth.profiles_dir / f"{SLUG}.yml", profile_data())
    _write(synth.journal_dir / "profile.draft.yml", _changed_draft(profile_data))
    result = _diff()
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "~ article_types[0].limits.words_main: 3000 -> 3500" in lines
    assert "+ review.criteria[0]: \"Clarity\"" in lines
    assert "- references.natbib" in lines


def test_diff_json_is_deterministic(synth, profile_data):
    _write(synth.profiles_dir / f"{SLUG}.yml", profile_data())
    _write(synth.journal_dir / "profile.draft.yml", _changed_draft(profile_data))
    first = _diff("--json")
    second = _diff("--json")
    assert first.exit_code == 0 and second.exit_code == 0
    assert first.output == second.output
    rows = json.loads(first.output)
    ops = {(row["op"], row["path"]) for row in rows}
    assert ("~", "article_types[0].limits.words_main") in ops
    assert ("+", "review.criteria[0]") in ops
    assert ("-", "references.natbib") in ops


def test_diff_of_identical_profiles_is_empty(synth, profile_data):
    _write(synth.profiles_dir / f"{SLUG}.yml", profile_data())
    _write(synth.journal_dir / "profile.draft.yml", profile_data())
    result = _diff()
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_style_writes_the_figure_style_file(synth, profile_data):
    data = profile_data()
    data["figures"] = dict(FIGURES)
    _write(synth.journal_dir / "profile.yml", data)
    result = CliRunner().invoke(main, ["journal", "style", RUN])
    assert result.exit_code == 0, result.output
    written = yaml.safe_load((synth.journal_dir / "figure-style.yml").read_text(encoding="utf-8"))
    schema = yaml.safe_load((REPO / "schemas" / "figure-style.yml").read_text(encoding="utf-8"))
    assert set(written["figures"]) == set(schema["figures"])
    assert written["figures"]["dpi"] == 300


def test_style_refuses_a_profile_without_figures(synth, profile_data):
    _write(synth.journal_dir / "profile.yml", profile_data())
    result = CliRunner().invoke(main, ["journal", "style", RUN])
    assert result.exit_code == 1
    assert not (synth.journal_dir / "figure-style.yml").exists()
