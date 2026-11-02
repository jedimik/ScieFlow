"""M-H t2 (plan 5.2): the `review:` block of a systematic-review design, its checks, the template
skeleton, the design hash and the rendered "Review protocol" section.

Synthetic fixture only (tests/fixtures/design/systematic-review.yml). Each mutation changes one
value of a valid design, so one test fails for exactly one rule.
"""

import copy
from pathlib import Path

import pytest
import yaml

from scieflow.core.design import model, render, templates
from scieflow.core.project import Project

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "fixtures" / "design"
BASE = yaml.safe_load((FIX / "systematic-review.yml").read_text(encoding="utf-8"))


def _ws(tmp_path):
    ws = tmp_path / "workspace" / "sr1"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _check(tmp_path, design):
    return model.check(Project(REPO), _ws(tmp_path), design)


def _rules(problems):
    return [p.rule for p in problems]


def _mutated(mutate):
    design = copy.deepcopy(BASE)
    mutate(design)
    return design


def test_the_fixture_checks_clean(tmp_path):
    report = _check(tmp_path, copy.deepcopy(BASE))
    assert report.errors == []
    assert model.is_plain_json(report)


def test_a_systematic_review_needs_a_review_block(tmp_path):
    design = _mutated(lambda d: d.pop("review"))
    assert "review" in _rules(_check(tmp_path, design).errors)


def test_a_review_block_is_refused_for_other_study_types(tmp_path):
    design = _mutated(lambda d: d.update(study_type="cohort"))
    assert "review" in _rules(_check(tmp_path, design).errors)


@pytest.mark.parametrize(
    "mutate, rule",
    [
        # plan 5.2 `ids`: criterion ids match the id pattern and are unique across both lists
        (lambda d: d["review"]["eligibility"]["exclude"].__setitem__(0, {"id": "i1", "text": "x"}), "ids"),
        (lambda d: d["review"]["eligibility"]["include"].__setitem__(0, {"id": "I1", "text": "x"}), "ids"),
        (lambda d: d["review"]["strategies"].append(dict(d["review"]["strategies"][0])), "ids"),
        # plan 5.2 criteria: non-empty lists, text non-empty and at most 500 characters
        (lambda d: d["review"]["eligibility"].update(include=[]), "criteria"),
        (lambda d: d["review"]["eligibility"]["include"][0].update(text="x" * 501), "criteria"),
        # plan 5.2 strategies: query 1-1000 characters, sources distinct from the enum, years, limit
        (lambda d: d["review"]["strategies"][0].update(sources=["openalex", "bing"]), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(sources=[]), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(sources=["openalex", "openalex"]), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(query="q" * 1001), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(from_year=1899), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(limit=0), "strategies"),
        (lambda d: d["review"]["strategies"][0].update(limit=201), "strategies"),
        # plan 5.2 screening: batch_size 1-500, prioritise a boolean
        (lambda d: d["review"]["screening"].update(batch_size=0), "screening"),
        (lambda d: d["review"]["screening"].update(prioritise="yes"), "screening"),
        # plan 5.2 appraisal, synthesis, registration, living, framework enums
        (lambda d: d["review"]["appraisal"].update(rob_tool="cochrane"), "appraisal"),
        (lambda d: d["review"]["appraisal"].update(grade="yes"), "appraisal"),
        (lambda d: d["review"]["synthesis"].update(method="bayes"), "synthesis"),
        (lambda d: d["review"]["registration"].update(registry="figshare"), "registration"),
        (lambda d: d["review"].update(living="no"), "living"),
        (lambda d: d["review"].update(framework="xyz"), "review"),
        # an unknown key inside the block is refused by name only
        (lambda d: d["review"].update(unexpected="x"), "keys"),
    ],
)
def test_each_review_rule_has_a_failing_case(tmp_path, mutate, rule):
    report = _check(tmp_path, _mutated(mutate))
    assert rule in _rules(report.errors), report.errors


def test_meta_analysis_is_a_warning_because_m_h_pools_nothing(tmp_path):
    design = _mutated(lambda d: d["review"]["synthesis"].update(method="meta-analysis"))
    report = _check(tmp_path, design)
    assert "synthesis" in _rules(report.warnings)
    assert "synthesis" not in _rules(report.errors)


def test_a_systematic_review_without_prisma_is_a_warning(tmp_path):
    design = _mutated(lambda d: d.update(reporting_guidelines=["strobe"]))
    assert "guideline-sr" in _rules(_check(tmp_path, design).warnings)


def test_the_template_skeleton_has_the_review_keys_and_prisma(tmp_path):
    design = yaml.safe_load(templates.template("systematic-review", "Synthetic title"))
    review = design["review"]
    for key in ("eligibility", "strategies", "screening", "appraisal", "synthesis"):
        assert key in review
    assert design["reporting_guidelines"] == ["prisma-2020"]


def test_the_template_reports_only_todo_errors(tmp_path):
    design = yaml.safe_load(templates.template("systematic-review", "Synthetic title"))
    report = _check(tmp_path, design)
    assert report.errors, "a fresh template must still hold TODO placeholders"
    assert set(_rules(report.errors)) == {"todo"}


def test_the_design_hash_changes_when_a_criterion_text_changes():
    changed = _mutated(lambda d: d["review"]["eligibility"]["include"][0].update(text="Changed"))
    assert model.content_hash(changed) != model.content_hash(BASE)


def test_the_rendered_design_has_a_review_protocol_section():
    text = render.render(copy.deepcopy(BASE), model.Report(), "digest")
    assert "## Review protocol" in text
    assert "Synthetic inclusion criterion" in text
    assert "synthetic protocol query" in text


def test_the_model_does_not_import_the_research_module():
    source = (REPO / "src" / "scieflow" / "core" / "design" / "model.py").read_text(encoding="utf-8")
    assert "scieflow.research" not in source
