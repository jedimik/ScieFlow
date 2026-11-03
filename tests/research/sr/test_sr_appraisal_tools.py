"""M-H t7 (plan 5.10, Part B t7 items 1, 3, 5): tool definitions and the pure appraisal checks.

The expected verdicts are the pinned examples of plan 5.10, written by hand here, never derived from the
code under test. Synthetic worksheets only; no network, no agent, no patient data.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from scieflow.research.sr import appraisal
from scieflow.research.sr.errors import SRError

REPO = Path(__file__).resolve().parents[3]
SR_CONFIG = REPO / "config" / "sr"
ROB_TOOLS = ("rob2", "robins-i", "quadas-2")

ROB2_DOMAINS = ["D1", "D2", "D3", "D4", "D5"]
ROBINS_DOMAINS = ["confounding", "selection-of-participants", "classification-of-interventions",
                  "deviations-from-intended-interventions", "missing-data", "measurement-of-outcomes",
                  "selection-of-the-reported-result"]
QUADAS_DOMAINS = ["patient-selection", "index-test", "reference-standard", "flow-and-timing"]
QUADAS_APPLICABLE = ["patient-selection", "index-test", "reference-standard"]
GRADE_DOWN = ["risk-of-bias", "inconsistency", "indirectness", "imprecision", "publication-bias"]
GRADE_UP = ["large-effect", "dose-response", "plausible-confounding"]


def _tool(name: str) -> dict:
    return appraisal.load_rob_tool(SR_CONFIG / f"{name}.yml")


def _grade_tool() -> dict:
    return appraisal.load_grade_tool(SR_CONFIG / "grade.yml")


def _worksheet(tool: dict, judgements: dict, overall, rationale: str = "", support: str = "synthetic support"
               ) -> dict:
    """A worksheet for the tool: every domain judged with the given judgement (a dict per domain id)."""
    domains = []
    for domain in tool["domains"]:
        row = {"id": domain["id"], "judgement": judgements[domain["id"]], "support": support}
        if tool.get("applicability") and domain["id"] in tool["applicability"]["domains"]:
            row["applicability"] = {"judgement": "low", "support": support}
        domains.append(row)
    return {"format": 1, "record_id": "R00002", "tool": tool["id"], "domains": domains,
            "overall": None if overall is None else {"judgement": overall, "rationale": rationale}}


def _all(tool: dict, value: str) -> dict:
    return {d["id"]: value for d in tool["domains"]}


# --- 1. Tool files load strictly and are unverified --------------------------------------------------


@pytest.mark.parametrize("name", ROB_TOOLS)
def test_rob_tool_file_loads_and_is_unverified(name):
    tool = _tool(name)
    assert tool["id"] == name
    assert tool["verification"]["status"] == "unverified"
    raw = yaml.safe_load((SR_CONFIG / f"{name}.yml").read_text(encoding="utf-8"))
    assert raw["verification"]["verified_by"] is None and raw["verification"]["verified_at"] is None


def test_grade_tool_file_loads_and_is_unverified():
    tool = _grade_tool()
    assert tool["id"] == "grade"
    assert tool["verification"]["status"] == "unverified"
    assert [d["id"] for d in tool["downgrade"]] == GRADE_DOWN
    assert [d["id"] for d in tool["upgrade"]] == GRADE_UP


def test_domains_and_judgements_match_plan_table():
    assert [d["id"] for d in _tool("rob2")["domains"]] == ROB2_DOMAINS
    assert _tool("rob2")["judgements"] == ["low", "some-concerns", "high"]
    assert [d["id"] for d in _tool("robins-i")["domains"]] == ROBINS_DOMAINS
    assert _tool("robins-i")["judgements"] == ["low", "moderate", "serious", "critical"]
    assert _tool("robins-i")["overall"]["extra"] == ["no-information"]
    assert [d["id"] for d in _tool("quadas-2")["domains"]] == QUADAS_DOMAINS
    assert _tool("quadas-2")["judgements"] == ["low", "high", "unclear"]
    assert _tool("quadas-2")["overall"]["rule"] == "none"
    assert _tool("quadas-2")["applicability"]["domains"] == QUADAS_APPLICABLE


def _write_tool(tmp_path: Path, name: str, mutate) -> Path:
    raw = yaml.safe_load((SR_CONFIG / f"{name}.yml").read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / f"{name}.yml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_unknown_key_in_a_tool_file_is_refused(tmp_path):
    path = _write_tool(tmp_path, "rob2", lambda raw: raw.update(score=7))
    with pytest.raises(SRError, match="score"):
        appraisal.load_rob_tool(path)


def test_duplicate_domain_in_a_tool_file_is_refused(tmp_path):
    path = _write_tool(tmp_path, "rob2", lambda raw: raw["domains"].append(dict(raw["domains"][0])))
    with pytest.raises(SRError, match="duplicate"):
        appraisal.load_rob_tool(path)


def test_empty_judgements_in_a_tool_file_are_refused(tmp_path):
    path = _write_tool(tmp_path, "rob2", lambda raw: raw.update(judgements=[]))
    with pytest.raises(SRError, match="judgements"):
        appraisal.load_rob_tool(path)


def test_a_grade_entry_may_not_carry_a_stored_certainty():
    entry = _entry("randomised")
    entry["certainty"] = "high"
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert any("certainty" in e for e in result["errors"])


# --- 2 and 3. Pinned rob examples (plan 5.10) ---------------------------------------------------------


def test_rob2_overall_milder_than_the_worst_domain_is_an_error():
    tool = _tool("rob2")
    judgements = {"D1": "low", "D2": "some-concerns", "D3": "low", "D4": "low", "D5": "low"}
    errors = appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "low"), "sr/rob/R00002.yml")
    assert errors and any("overall" in e and "worst" in e for e in errors)


def test_rob2_overall_equal_to_the_worst_domain_is_fine():
    tool = _tool("rob2")
    judgements = {"D1": "low", "D2": "some-concerns", "D3": "low", "D4": "low", "D5": "low"}
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "some-concerns"), "x") == []


def test_rob2_overall_higher_than_the_worst_domain_needs_a_rationale():
    tool = _tool("rob2")
    judgements = {"D1": "low", "D2": "some-concerns", "D3": "low", "D4": "low", "D5": "low"}
    without = appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "high"), "x")
    assert any("rationale" in e for e in without)
    with_reason = appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "high", "synthetic reason"), "x")
    assert with_reason == []


def test_robins_i_no_information_overall_needs_a_no_information_domain_and_no_serious_or_critical():
    tool = _tool("robins-i")
    ok = _all(tool, "low")
    ok["confounding"] = "no-information"
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, ok, "no-information"), "x") == []
    serious = dict(ok, **{"missing-data": "serious"})
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, serious, "no-information"), "x")
    nothing = _all(tool, "low")
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, nothing, "no-information"), "x")


def test_robins_i_overall_is_never_milder_than_its_worst_domain():
    tool = _tool("robins-i")
    judgements = _all(tool, "low")
    judgements["confounding"] = "serious"
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "low"), "x")
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "serious"), "x") == []


def test_quadas2_low_overall_only_if_every_domain_is_low():
    tool = _tool("quadas-2")
    judgements = _all(tool, "low")
    judgements["reference-standard"] = "high"
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "low"), "x")
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "high"), "x") == []
    assert appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, None), "x") == []


def test_quadas2_applicability_is_required_for_the_first_three_domains():
    tool = _tool("quadas-2")
    doc = _worksheet(tool, _all(tool, "low"), "low")
    doc["domains"][0].pop("applicability")
    assert any("applicab" in e for e in appraisal.check_rob_worksheet(tool, doc, "x"))


def test_a_judgement_outside_the_tool_list_is_an_error_naming_domain_and_rule():
    tool = _tool("rob2")
    judgements = _all(tool, "low")
    judgements["D3"] = "maybe"
    errors = appraisal.check_rob_worksheet(tool, _worksheet(tool, judgements, "low"), "sr/rob/R00003.yml")
    assert any("D3" in e and "judgement" in e for e in errors)
    assert all("sr/rob/R00003.yml" in e for e in errors)


def test_error_messages_never_contain_support_text():
    tool = _tool("rob2")
    secret = "SECRET-SUPPORT-TEXT-7F3A"
    judgements = _all(tool, "low")
    judgements["D2"] = "maybe"
    doc = _worksheet(tool, judgements, "low", support=secret)
    doc["domains"][0]["support"] = ""
    errors = appraisal.check_rob_worksheet(tool, doc, "sr/rob/R00002.yml")
    assert errors
    assert all(secret not in e for e in errors)


def test_a_worksheet_with_an_unknown_or_missing_domain_is_an_error():
    tool = _tool("rob2")
    doc = _worksheet(tool, _all(tool, "low"), "low")
    doc["domains"].pop()
    assert appraisal.check_rob_worksheet(tool, doc, "x")
    doc = _worksheet(tool, _all(tool, "low"), "low")
    doc["domains"].append({"id": "D9", "judgement": "low", "support": "synthetic"})
    assert appraisal.check_rob_worksheet(tool, doc, "x")


def test_a_worksheet_for_another_tool_is_an_error():
    tool = _tool("rob2")
    doc = _worksheet(tool, _all(tool, "low"), "low")
    doc["tool"] = "robins-i"
    assert appraisal.check_rob_worksheet(tool, doc, "x")


# --- 5. GRADE pinned examples (plan 5.10) -------------------------------------------------------------


def _entry(design: str | None, down: dict | None = None, up: dict | None = None) -> dict:
    def block(names, given):
        out = {name: {"level": "none", "reason": ""} for name in names}
        for name, (level, reason) in (given or {}).items():
            out[name] = {"level": level, "reason": reason}
        return out

    return {"outcome": "primary_outcome", "design": design, "studies": ["R00002", "R00003"],
            "downgrade": block(GRADE_DOWN, down), "upgrade": block(GRADE_UP, up), "effect_text": ""}


def test_randomised_with_serious_bias_and_serious_imprecision_is_low():
    entry = _entry("randomised", {"risk-of-bias": ("serious", "synthetic"), "imprecision": ("serious", "synthetic")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"] == [] and result["certainty"] == "low"


def test_non_randomised_with_large_effect_is_moderate():
    entry = _entry("non-randomised", up={"large-effect": ("large", "synthetic")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"] == [] and result["certainty"] == "moderate"


def test_randomised_with_three_very_serious_downgrades_is_clamped_to_very_low():
    entry = _entry("randomised", {"risk-of-bias": ("very-serious", "s"), "inconsistency": ("very-serious", "s"),
                                  "imprecision": ("very-serious", "s")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"] == [] and result["certainty"] == "very low"


def test_an_upgrade_on_randomised_evidence_is_an_error():
    entry = _entry("randomised", up={"dose-response": ("present", "synthetic")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"]


def test_a_non_none_level_without_a_reason_is_an_error():
    entry = _entry("randomised", {"imprecision": ("serious", "")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"]


def test_grade_studies_must_be_included_studies():
    entry = _entry("randomised", {"imprecision": ("serious", "synthetic")})
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002"})
    assert any("R00003" in e for e in result["errors"])


def test_grade_entry_without_a_design_is_an_error():
    entry = _entry(None)
    result = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    assert result["errors"]


def test_grade_certainty_is_computed_from_levels_not_read_back():
    entry = _entry("randomised", {"risk-of-bias": ("serious", "synthetic"), "imprecision": ("serious", "synthetic")})
    first = appraisal.grade_result(_grade_tool(), entry, included={"R00002", "R00003"})
    entry2 = copy.deepcopy(entry)
    for name in ("risk-of-bias", "imprecision"):
        entry2["downgrade"][name]["level"] = "none"
        entry2["downgrade"][name]["reason"] = ""
    second = appraisal.grade_result(_grade_tool(), entry2, included={"R00002", "R00003"})
    assert first["certainty"] == "low" and second["certainty"] == "high"
