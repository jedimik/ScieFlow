"""M-D1 t1: compare scieflow experiment power with statsmodels, when installed.

statsmodels is NOT a dependency (M-D1-D1). This module is skipped on hosts
without it; it turns on with M-D2, which adds statsmodels. Correlation and
survival have no statsmodels counterpart and are not compared here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

pytest.importorskip("scipy")
pytest.importorskip("statsmodels")

from statsmodels.stats.power import (  # noqa: E402
    FTestAnovaPower,
    NormalIndPower,
    TTestIndPower,
    TTestPower,
)

from scieflow.experiments import power as P  # noqa: E402

REF_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "power" / "reference.json"
REF = json.loads(REF_PATH.read_text(encoding="utf-8"))
COMPARED = [c for c in REF["cases"] if c["family"] in ("t-ind", "t-paired", "t-one", "anova", "proportions")]


@pytest.mark.parametrize("case", COMPARED, ids=[c["id"] for c in COMPARED])
def test_matches_statsmodels(case):
    family = case["family"]
    alpha = case["alpha"]
    alt = case.get("alternative", "two-sided")
    kwargs = {k: case[k] for k in ("alpha", "power", "n", "effect_size", "p1", "p2", "ratio", "groups")
              if k in case}
    doc = P.solve(family, **kwargs)
    if family == "anova":
        sm = FTestAnovaPower()
        if case["solve"] == "n":
            expected = sm.solve_power(effect_size=case["effect_size"], nobs=None, alpha=alpha,
                                      power=case["power"], k_groups=case["groups"])
            actual = doc["result"]["n_total"]
        elif case["solve"] == "power":
            expected = sm.power(effect_size=case["effect_size"], nobs=case["n"], alpha=alpha,
                                k_groups=case["groups"])
            actual = doc["result"]["power"]
        else:
            expected = sm.solve_power(effect_size=None, nobs=case["n"], alpha=alpha,
                                      power=case["power"], k_groups=case["groups"])
            actual = doc["result"]["effect_size"]
        assert math.isclose(actual, expected, rel_tol=1e-6)
        return
    if family == "proportions":
        sm = NormalIndPower()
        h = doc["inputs"]["effect_size"]
        if case["solve"] == "n":
            expected = sm.solve_power(effect_size=h, nobs1=None, alpha=alpha, power=case["power"],
                                      ratio=case.get("ratio", 1), alternative=alt)
            assert math.isclose(doc["result"]["n1"], expected, rel_tol=1e-6)
        else:
            expected = sm.power(effect_size=h, nobs1=case["n"], alpha=alpha,
                                ratio=case.get("ratio", 1), alternative=alt)
            assert abs(doc["result"]["power"] - expected) <= 1e-8
        return
    sm = TTestIndPower() if family == "t-ind" else TTestPower()
    if case["solve"] == "n":
        expected = sm.solve_power(effect_size=case["effect_size"], nobs=None, alpha=alpha,
                                  power=case["power"], ratio=case.get("ratio", 1), alternative=alt) \
            if family == "t-ind" else \
            sm.solve_power(effect_size=case["effect_size"], nobs=None, alpha=alpha,
                           power=case["power"], alternative=alt)
        key = "n1" if family == "t-ind" else "n"
        assert math.isclose(doc["result"][key], expected, rel_tol=1e-6)
    elif case["solve"] == "power":
        expected = sm.power(effect_size=case["effect_size"], nobs=case["n"], alpha=alpha,
                            ratio=case.get("ratio", 1), alternative=alt) \
            if family == "t-ind" else \
            sm.power(effect_size=case["effect_size"], nobs=case["n"], alpha=alpha, alternative=alt)
        assert abs(doc["result"]["power"] - expected) <= 1e-8
    else:
        expected = sm.solve_power(effect_size=None, nobs=case["n"], alpha=alpha,
                                  power=case["power"], alternative=alt) \
            if family != "t-ind" else \
            sm.solve_power(effect_size=None, nobs1=case["n"], alpha=alpha, power=case["power"],
                           ratio=case.get("ratio", 1), alternative=alt)
        assert math.isclose(doc["result"]["effect_size"], expected, rel_tol=1e-6)
