"""M-D1 t1: scieflow experiment power, command line."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("scipy")

from scieflow.experiments.cli import experiment as main  # noqa: E402

REF_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "power" / "reference.json"
REF = json.loads(REF_PATH.read_text(encoding="utf-8"))
CASES = REF["cases"]
TOL = REF["tolerance"]
DOCS = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "cli.md"

FLAGS = {
    "alpha": "--alpha",
    "power": "--power",
    "n": "--n",
    "events": "--events",
    "effect_size": "--effect-size",
    "p1": "--p1",
    "p2": "--p2",
    "r": "--r",
    "hazard_ratio": "--hazard-ratio",
    "groups": "--groups",
    "ratio": "--ratio",
    "allocation": "--allocation",
    "event_probability": "--event-probability",
    "alternative": "--alternative",
}

RESULT_KEY = {
    ("t-ind", "n"): "n1",
    ("t-paired", "n"): "n",
    ("t-one", "n"): "n",
    ("anova", "n"): "n_total",
    ("proportions", "n"): "n1",
    ("correlation", "n"): "n",
    ("survival", "n"): "n",
    ("survival", "events"): "events",
}


def _flags(case):
    args = []
    for key, flag in FLAGS.items():
        if key in case:
            args += [flag, str(case[key])]
    return args


def _result_key(case):
    if case["solve"] == "power":
        return "power"
    if case["solve"] == "effect":
        return "effect_size"
    return RESULT_KEY[(case["family"], case["solve"])]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_cli_json_reproduces_reference(case):
    result = CliRunner().invoke(main, ["power", case["family"], *_flags(case), "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    actual = doc["result"][_result_key(case)]
    if case["solve"] == "power":
        assert abs(actual - case["expected"]) <= TOL["power"]["absolute"]
    else:
        assert math.isclose(actual, case["expected"], rel_tol=TOL["n"]["relative"])


def test_power_is_listed_in_experiment_group_and_help_names_families():
    listed = CliRunner().invoke(main, ["--help"])
    assert listed.exit_code == 0, listed.output
    assert "power" in listed.output
    helped = CliRunner().invoke(main, ["power", "--help"])
    assert helped.exit_code == 0, helped.output
    for family in ("t-ind", "t-paired", "t-one", "anova", "proportions", "correlation", "survival"):
        assert family in helped.output
    assert "--out" in helped.output


def test_none_unknown_is_usage_error_exit_2():
    result = CliRunner().invoke(
        main,
        ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--n", "64", "--effect-size", "0.5"],
    )
    assert result.exit_code == 2, result.output
    assert "exactly one" in result.output


def test_two_unknown_is_usage_error_exit_2():
    result = CliRunner().invoke(main, ["power", "t-ind", "--effect-size", "0.5"])
    assert result.exit_code == 2, result.output
    assert "exactly one" in result.output


def test_smaller_alternative_is_refused_exit_1():
    result = CliRunner().invoke(
        main,
        ["power", "t-ind", "--n", "64", "--effect-size", "0.5", "--alternative", "smaller"],
    )
    assert result.exit_code == 1, result.output
    assert "positive size" in result.output
    assert "larger" in result.output


def test_domain_error_names_the_input_exit_1():
    result = CliRunner().invoke(
        main, ["power", "proportions", "--alpha", "0.05", "--power", "0.8", "--p1", "0.5", "--p2", "0.5"]
    )
    assert result.exit_code == 1, result.output
    assert "p1" in result.output


def test_json_output_is_byte_identical_across_calls():
    args = ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--effect-size", "0.5", "--json"]
    first = CliRunner().invoke(main, args)
    second = CliRunner().invoke(main, args)
    assert first.exit_code == 0 and second.exit_code == 0
    assert first.output == second.output


def test_text_output_shows_assumptions():
    result = CliRunner().invoke(
        main, ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--effect-size", "0.5"]
    )
    assert result.exit_code == 0, result.output
    assert "equal variances" in result.output


@pytest.mark.parametrize("extra", [[], ["--effect-size", "0.4"]])
def test_proportions_p1_alone_is_refused_exit_1(extra):
    result = CliRunner().invoke(
        main,
        ["power", "proportions", "--alpha", "0.05", "--power", "0.8", "--p1", "0.5", *extra],
    )
    assert result.exit_code == 1
    assert "p1" in result.output


def test_out_writes_the_same_bytes_as_json(tmp_path):
    args = ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--effect-size", "0.5"]
    stdout = CliRunner().invoke(main, [*args, "--json"]).output
    target = tmp_path / "power.json"
    result = CliRunner().invoke(main, [*args, "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == stdout.encode("utf-8")


def test_out_refuses_an_existing_file_without_force(tmp_path):
    target = tmp_path / "power.json"
    target.write_text("{}\n", encoding="utf-8")
    args = ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--effect-size", "0.5"]
    refused = CliRunner().invoke(main, [*args, "--out", str(target)])
    assert refused.exit_code == 1, refused.output
    assert target.read_text(encoding="utf-8") == "{}\n"
    forced = CliRunner().invoke(main, [*args, "--out", str(target), "--force"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(target.read_text(encoding="utf-8"))["family"] == "t-ind"


def test_out_refuses_a_symlink_even_with_force(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    args = ["power", "t-ind", "--alpha", "0.05", "--power", "0.8", "--effect-size", "0.5"]
    result = CliRunner().invoke(main, [*args, "--out", str(link), "--force"])
    assert result.exit_code == 1, result.output
    assert real.read_text(encoding="utf-8") == "{}\n"


def test_docs_have_a_power_section_naming_families_and_solve_modes():
    text = DOCS.read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert any("scieflow experiment power" in h for h in headings), headings
    section = text.split("scieflow experiment power", 1)[1]
    for family in ("t-ind", "t-paired", "t-one", "anova", "proportions", "correlation", "survival"):
        assert family in section
    for mode in ("solve", "--out"):
        assert mode in section
