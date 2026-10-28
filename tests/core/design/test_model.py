"""M-D1 t3: study design model (plan section 5.2): load, check, content hash, schema pins.

Synthetic fixtures only (tests/fixtures/design). Each mutation is a minimal change to a valid
design, so one test fails for exactly one rule. Messages must name a rule and a key path and
never echo a free-text value from the design file.
"""

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from scieflow.core.design import model
from scieflow.core.design import schema as design_schema
from scieflow.core.project import Project

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"
BASE = yaml.safe_load((FIX / "rct.yml").read_text(encoding="utf-8"))


def _ws(tmp_path):
    ws = tmp_path / "workspace" / "run1"
    (ws / "design" / "power").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "power-rct-ttest.json", ws / "design" / "power" / "rct-ttest.json")
    return ws


def _check(tmp_path, design):
    return model.check(Project(REPO), _ws(tmp_path), design)


def _rules(problems):
    return [p.rule for p in problems]


def _with(mutate):
    design = copy.deepcopy(BASE)
    mutate(design)
    return design


# --- loading (fail closed, plan 5.2) ---------------------------------------------------------

def test_absent_when_no_design_file(tmp_path):
    assert isinstance(model.load(_ws(tmp_path)), model.Absent)


def test_valid_file_loads_as_mapping(tmp_path):
    ws = _ws(tmp_path)
    (ws / "design" / "design.yml").write_text((FIX / "rct.yml").read_text(encoding="utf-8"))
    got = model.load(ws)
    assert isinstance(got, dict) and got["study_type"] == "rct"


@pytest.mark.parametrize("payload", ["", "- one\n- two\n", "key: [unclosed\n"])
def test_empty_non_mapping_and_yaml_errors_are_invalid(tmp_path, payload):
    ws = _ws(tmp_path)
    (ws / "design" / "design.yml").write_text(payload)
    assert isinstance(model.load(ws), model.Invalid)


def test_symlinked_design_file_is_invalid(tmp_path):
    ws = _ws(tmp_path)
    target = tmp_path / "elsewhere.yml"
    target.write_text((FIX / "rct.yml").read_text(encoding="utf-8"))
    (ws / "design" / "design.yml").symlink_to(target)
    assert isinstance(model.load(ws), model.Invalid)


def test_directory_named_design_file_is_invalid(tmp_path):
    ws = _ws(tmp_path)
    (ws / "design" / "design.yml").mkdir()
    assert isinstance(model.load(ws), model.Invalid)


# --- the shipped fixtures check clean; their warnings are exactly the expected rules --------

@pytest.mark.parametrize("name, expected", [
    ("rct", ["guideline-unverified"]),
    ("cohort", ["guideline-unverified", "level-undeclared"]),
    ("computational", []),
    ("diagnostic", ["guideline-unverified"]),
])
def test_shipped_fixture_has_no_errors_and_expected_warnings(tmp_path, name, expected):
    design = yaml.safe_load((FIX / f"{name}.yml").read_text(encoding="utf-8"))
    report = _check(tmp_path, design)
    assert report.errors == [], report.errors
    assert sorted(_rules(report.warnings)) == sorted(expected)


# --- one test per error rule (plan 5.2 table) -------------------------------------------------

def _outcome_copy(**changes):
    row = {"id": "second", "variable": "score", "priority": "secondary", "test": "t-test-ind",
           "metric": "second_metric"}
    row.update(changes)
    return row


ERROR_CASES = [
    ("keys-unknown-top-level", lambda d: d.__setitem__("bogus", 1), "keys"),
    ("keys-missing-question", lambda d: d.pop("question"), "keys"),
    ("keys-version-two", lambda d: d.__setitem__("version", 2), "keys"),
    ("enum-study-type", lambda d: d.__setitem__("study_type", "trial"), "enum"),
    ("enum-variable-role", lambda d: d["variables"][0].__setitem__("role", "bogus"), "enum"),
    ("enum-outcome-test", lambda d: d["sap"]["outcomes"][0].__setitem__("test", "bogus-test"), "enum"),
    ("todo-left-in-question", lambda d: d.__setitem__("question", "TODO: fill in"), "todo"),
    ("id-not-matching", lambda d: d["variables"][0].__setitem__("id", "Bad Id"), "id"),
    ("id-duplicated-in-list", lambda d: d["groups"][1].__setitem__("id", "control"), "id"),
    ("ref-outcome-variable", lambda d: d["sap"]["outcomes"][0].__setitem__("variable", "nope"), "ref"),
    ("ref-covariate", lambda d: d["sap"]["outcomes"][0].__setitem__("covariates", ["nope"]), "ref"),
    ("ref-estimand", lambda d: d["sap"]["outcomes"][0].__setitem__("estimand", "nope"), "ref"),
    ("ref-randomisation-stratified", lambda d: d["randomisation"].__setitem__("stratified_by", ["nope"]), "ref"),
    ("ref-estimand-outcome", lambda d: d["sap"]["estimands"][0].__setitem__("outcome", "nope"), "ref"),
    ("ref-split-stratify-by", lambda d: d["sap"].__setitem__("exploratory", {"split": {"fraction": 0.3, "seed": 7, "stratify_by": ["nope"]}, "fdr": "bh"}), "ref"),
    ("sample-size-method-outside-forms", lambda d: d.__setitem__("sample_size", {"method": "bogus"}),
     "sample-size"),
    ("primary-missing", lambda d: d["sap"]["outcomes"][0].__setitem__("priority", "secondary"), "primary"),
    ("test-missing", lambda d: d["sap"]["outcomes"][0].pop("test"), "test"),
    ("rct-one-group", lambda d: d.__setitem__("groups", d["groups"][:1]), "rct"),
    ("rct-no-randomisation", lambda d: d.pop("randomisation"), "rct"),
    ("sample-size-zero-n", lambda d: d.__setitem__("sample_size", {"method": "fixed", "n": 0, "rationale": "x"}),
     "sample-size"),
    ("sample-size-dotdot-path", lambda d: d["sample_size"].__setitem__("power_file", "design/../x.json"),
     "sample-size"),
    ("sample-size-missing-file", lambda d: d["sample_size"].__setitem__("power_file", "design/power/none.json"),
     "sample-size"),
    ("sample-size-outside-design", lambda d: d["sample_size"].__setitem__("power_file", "power/x.json"),
     "sample-size"),
    ("metric-repeated", lambda d: d["sap"]["outcomes"].append(_outcome_copy(metric="score_mse")), "metric"),
    ("alpha-out-of-range", lambda d: d["sap"]["multiplicity"].__setitem__("alpha", 1.5), "alpha"),
    ("exploratory-without-split", lambda d: d["sap"]["outcomes"].append(
        _outcome_copy(id="expl", priority="exploratory", metric="expl_metric")), "exploratory"),
    ("guideline-unknown-id", lambda d: d.__setitem__("reporting_guidelines", ["not-a-guideline"]), "guideline"),
    ("levels-unknown-level", lambda d: d["data"].__setitem__("levels_needed", ["L9"]), "levels"),
    ("levels-absolute-source", lambda d: d.__setitem__(
        "data", {"levels_needed": ["L0"], "sources": [{"path": "/abs/*.csv", "level": "L0"}]}), "levels"),
]


@pytest.mark.parametrize("label, mutate, rule", ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_each_error_rule_fires_alone_for_its_mutation(tmp_path, label, mutate, rule):
    report = _check(tmp_path, _with(mutate))
    assert set(_rules(report.errors)) == {rule}, (label, report.errors)


def test_power_mismatch_when_the_power_file_is_edited(tmp_path):
    ws = _ws(tmp_path)
    path = ws / "design" / "power" / "rct-ttest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["result"]["n1_required"] = doc["result"]["n1_required"] + 3
    path.write_text(json.dumps(doc), encoding="utf-8")
    report = model.check(Project(REPO), ws, copy.deepcopy(BASE))
    assert "power-mismatch" in _rules(report.errors)


def test_unedited_power_file_does_not_mismatch(tmp_path):
    report = _check(tmp_path, copy.deepcopy(BASE))
    assert "power-mismatch" not in _rules(report.errors)


def test_json_value_rule_names_a_yaml_date_and_a_nan(tmp_path):
    date_design = _with(lambda d: d.__setitem__("notes", yaml.safe_load("2026-10-01")))
    nan_design = _with(lambda d: d.__setitem__("notes", float("nan")))
    assert "json-value" in _rules(_check(tmp_path, date_design).errors)
    assert "json-value" in _rules(_check(tmp_path, nan_design).errors)


def test_error_messages_never_echo_a_free_text_value(tmp_path):
    secret = "Jan Novak 720101/1234"
    report = _check(tmp_path, _with(lambda d: d.__setitem__("study_type", secret)))
    text = " ".join(f"{p.rule} {p.where} {p.message}" for p in report.errors)
    assert "enum" in _rules(report.errors)
    assert secret not in text


# --- one test per warning rule ----------------------------------------------------------------

WARNING_CASES = [
    ("no-sample-size", lambda d: d.pop("sample_size"), "no-sample-size"),
    ("guideline-fit", lambda d: d.__setitem__("reporting_guidelines", ["strobe"]), "guideline-fit"),
    ("no-guideline", lambda d: d.pop("reporting_guidelines"), "no-guideline"),
    ("missing-data", lambda d: d["sap"].pop("missing_data"), "missing-data"),
    ("multiplicity-two-primaries", lambda d: (d["sap"].pop("multiplicity"),
                                              d["sap"]["outcomes"].append(_outcome_copy(priority="primary"))),
     "multiplicity"),
    ("outcome-role", lambda d: d["variables"][1].__setitem__("role", "covariate"), "outcome-role"),
]


@pytest.mark.parametrize("label, mutate, rule", WARNING_CASES, ids=[c[0] for c in WARNING_CASES])
def test_each_warning_rule_fires_without_an_error(tmp_path, label, mutate, rule):
    report = _check(tmp_path, _with(mutate))
    assert rule in _rules(report.warnings), (label, report.warnings)
    assert report.errors == [], (label, report.errors)


def test_level_undeclared_warns_only_when_the_run_has_not_declared_l2(tmp_path):
    design = _with(lambda d: d["data"].__setitem__("levels_needed", ["L0", "L2"]))
    assert "level-undeclared" in _rules(_check(tmp_path, design).warnings)


def test_power_unverified_when_the_power_module_cannot_be_imported(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "scieflow.experiments.power", None)
    report = _check(tmp_path, copy.deepcopy(BASE))
    assert "power-unverified" in _rules(report.warnings)
    assert "power-mismatch" not in _rules(report.errors)


# --- content hash (plan 5.2, M-D1-D2) ---------------------------------------------------------

def test_content_hash_is_canonical_json_sha256():
    obj = {"b": [1.5, "x"], "a": 1}
    expected = hashlib.sha256(b'{"a":1,"b":[1.5,"x"]}').hexdigest()
    assert model.content_hash(obj) == expected


def test_content_hash_ignores_key_order_and_yaml_layout():
    one = yaml.safe_load("a: 1\nb: {x: 2, y: 3}\n")
    two = yaml.safe_load("# comment\nb:\n  y: 3\n  x: 2\na:   1\n")
    assert model.content_hash(one) == model.content_hash(two)


def test_content_hash_changes_with_any_value_change():
    base = yaml.safe_load("a: 1\nb: [x]\n")
    assert model.content_hash(base) != model.content_hash(yaml.safe_load("a: 2\nb: [x]\n"))
    assert model.content_hash(base) != model.content_hash(yaml.safe_load("a: 1\nb: [y]\n"))


# --- schema pins and the reporting tie-in (plan 5.1, t3 item 9) -------------------------------

def test_schema_enumerations_are_pinned():
    enums = design_schema.load(REPO)["enums"]
    assert enums["study_types"] == [
        "rct", "cohort", "case-control", "cross-sectional", "diagnostic-accuracy",
        "prediction-model", "systematic-review", "animal", "economic-evaluation", "computational"]
    assert enums["variable_roles"] == [
        "exposure", "intervention", "outcome", "covariate", "confounder", "mediator",
        "effect-modifier", "stratifier", "predictor", "index-test", "reference-standard"]
    assert enums["variable_types"] == ["continuous", "binary", "categorical", "ordinal", "count",
                                       "time-to-event"]
    assert enums["outcome_priorities"] == ["primary", "secondary", "exploratory"]
    assert enums["missing_data"] == ["complete-case", "multiple-imputation", "mixed-model",
                                     "inverse-probability-weighting", "worst-case",
                                     "not-applicable", "other"]
    assert enums["multiplicity"] == ["none", "bonferroni", "holm", "hochberg", "bh-fdr",
                                     "hierarchical", "gatekeeping"]
    assert enums["sampling"] == ["consecutive", "random", "stratified-random", "convenience",
                                 "census", "other"]


def test_reporting_reads_study_types_from_the_schema(tmp_path):
    from scieflow.core.design import reporting

    root = tmp_path / "root"
    shutil.copytree(REPO / "config" / "reporting", root / "config" / "reporting")
    (root / "schemas").mkdir()
    for name in ("reporting-checklist.yml", "design.yml"):
        shutil.copy(REPO / "schemas" / name, root / "schemas" / name)
    assert reporting.validate_all(root) == []
    schema = yaml.safe_load((root / "schemas" / "design.yml").read_text(encoding="utf-8"))
    schema["enums"]["study_types"] = [t for t in schema["enums"]["study_types"] if t != "rct"]
    (root / "schemas" / "design.yml").write_text(yaml.safe_dump(schema), encoding="utf-8")
    rules = [p.rule for p in reporting.validate_all(root)]
    assert "applies-to" in rules


def test_summary_metrics_are_the_named_outcome_metrics():
    assert model.sap_metrics(copy.deepcopy(BASE)) == ["score_mse"]


# --- M-D1 t3 fix round: gaps of review r1 (findings 3 and 6) -------------------------------------

SECRET_KEY = "Jan_Novak_720101"


@pytest.mark.parametrize("mutate", [
    lambda d: d.__setitem__(SECRET_KEY, yaml.safe_load("2026-10-01")),
    lambda d: d.__setitem__(SECRET_KEY, "TODO"),
    lambda d: d["variables"][0].__setitem__(SECRET_KEY, yaml.safe_load("2026-10-01")),
    lambda d: d["pico"].__setitem__(SECRET_KEY, "TODO"),
    lambda d: d["sap"]["outcomes"][0].__setitem__(SECRET_KEY, "TODO"),
], ids=["top-date", "top-todo", "variable-date", "pico-todo", "outcome-todo"])
def test_unknown_key_names_never_reach_a_message(tmp_path, mutate):
    report = _check(tmp_path, _with(mutate))
    assert report.errors, "the mutation must be an error"
    text = json.dumps(report.to_dict(), ensure_ascii=False) + " ".join(
        str(p) for p in report.errors + report.warnings)
    assert SECRET_KEY not in text


def test_known_key_names_are_still_named_in_messages(tmp_path):
    report = _check(tmp_path, _with(lambda d: d["variables"][0].__setitem__("role", "bogus")))
    assert [p.where for p in report.errors if p.rule == "enum"] == ["variables[0].role"]


def test_level_undeclared_is_silent_when_the_run_declares_an_l2_path(tmp_path):
    ws = _ws(tmp_path)
    (ws / "data.yml").write_text(yaml.safe_dump({"version": 1, "levels": [{
        "path": "data/raw.csv", "level": "L2", "kind": "other",
        "reason": "synthetic test declaration", "declared_by": "human"}]}), encoding="utf-8")
    design = _with(lambda d: d["data"].__setitem__("levels_needed", ["L0", "L2"]))
    report = model.check(Project(REPO), ws, design)
    assert "level-undeclared" not in _rules(report.warnings)


def test_power_mismatch_for_an_unknown_family(tmp_path):
    ws = _ws(tmp_path)
    path = ws / "design" / "power" / "rct-ttest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["family"] = "no-such-family"
    path.write_text(json.dumps(doc), encoding="utf-8")
    report = model.check(Project(REPO), ws, copy.deepcopy(BASE))
    assert "power-mismatch" in _rules(report.errors)


def test_sample_size_refuses_a_symlinked_power_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "design" / "power" / "linked.json").symlink_to(ws / "design" / "power" / "rct-ttest.json")
    design = _with(lambda d: d["sample_size"].__setitem__("power_file", "design/power/linked.json"))
    report = model.check(Project(REPO), ws, design)
    assert set(_rules(report.errors)) == {"sample-size"}


RCT_FIXTURE_SHA256 = "2aca77ec6f3a00ce9a8c4d39c2e052c58f9119a28059a1fa24a2e82510e76627"


def test_content_hash_of_the_rct_fixture_is_pinned():
    assert model.content_hash(BASE) == RCT_FIXTURE_SHA256
