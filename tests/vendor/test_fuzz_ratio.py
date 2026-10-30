"""The ratio shim (M-C2-D1): ``fuzz.ratio`` reproduces rapidfuzz 3.14.6's ``ratio`` exactly.

The 300 recorded cases are a byte copy of the planning probe's ``ratio_parity.json`` (sha256
1dfeaa71...). Offline. The vendored verify module uses ``fuzz.ratio`` for the title-prefix rule and
the search-fallback rule (verify.py), so the shim must answer the same floats.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core._fuzz import fuzz

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kernel2" / "ratio_parity.json"


def _cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_fixture_is_the_recorded_three_hundred_cases() -> None:
    cases = _cases()
    assert len(cases) == 300
    assert all({"a", "b", "score"} <= set(c) for c in cases)


def test_all_recorded_rapidfuzz_ratio_cases_are_reproduced_exactly() -> None:
    mismatches = [
        (c["a"], c["b"], c["score"], fuzz.ratio(c["a"], c["b"]))
        for c in _cases()
        if fuzz.ratio(c["a"], c["b"]) != c["score"]
    ]
    assert mismatches == []


def test_ratio_does_not_sort_tokens() -> None:
    # token_sort_ratio would call these equal; ratio, like rapidfuzz's, must not.
    assert fuzz.ratio("b c", "c b") < 100.0
    assert fuzz.token_sort_ratio("b c", "c b") == 100.0


def test_edge_cases_match_rapidfuzz_conventions() -> None:
    assert fuzz.ratio("", "") == 100.0
    assert fuzz.ratio("a", "") == 0.0


def test_keyword_arguments_are_refused() -> None:
    with pytest.raises(TypeError):
        fuzz.ratio("a", "b", processor=None)
    with pytest.raises(TypeError):
        fuzz.ratio("a", "b", score_cutoff=50)


def test_namespace_exposes_both_scorers() -> None:
    assert callable(fuzz.ratio)
    assert callable(fuzz.token_sort_ratio)
