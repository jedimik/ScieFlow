"""The similarity shim that stands in for rapidfuzz (M-C1-D2).

The vendored dedupe module imports ``fuzz`` from the shim, not from rapidfuzz. These tests hold
the shim to rapidfuzz 3.14.6's recorded answers (300 cases, exact float equality), prove that the
vendored code imports without rapidfuzz, and prove that no ScieFlow module imports it. Offline.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core._fuzz import fuzz

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kernel" / "token_sort_ratio_parity.json"


def _cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_all_recorded_rapidfuzz_cases_are_reproduced_exactly() -> None:
    cases = _cases()
    assert len(cases) == 300
    mismatches = [
        (c["a"], c["b"], c["score"], fuzz.token_sort_ratio(c["a"], c["b"]))
        for c in cases
        if fuzz.token_sort_ratio(c["a"], c["b"]) != c["score"]
    ]
    assert mismatches == []


def test_edge_cases_match_rapidfuzz_conventions() -> None:
    assert fuzz.token_sort_ratio("", "") == 100.0
    assert fuzz.token_sort_ratio("a", "") == 0.0
    assert fuzz.token_sort_ratio("b c", "c b") == 100.0


def test_keyword_arguments_are_refused() -> None:
    with pytest.raises(TypeError):
        fuzz.token_sort_ratio("a", "b", processor=None)
    with pytest.raises(TypeError):
        fuzz.token_sort_ratio("a", "b", score_cutoff=50)


def test_a_long_title_pair_scores_well_under_ten_milliseconds() -> None:
    long_a = "idiopathic rem sleep behaviour disorder and the risk of parkinson disease " * 3
    long_b = long_a[::-1]
    fuzz.token_sort_ratio(long_a, long_b)  # warm up
    started = time.perf_counter()
    for _ in range(50):
        fuzz.token_sort_ratio(long_a, long_b)
    per_call = (time.perf_counter() - started) / 50
    assert per_call < 0.010, f"{per_call * 1000:.2f} ms per call"


def test_vendored_dedupe_imports_and_runs_without_rapidfuzz() -> None:
    code = (
        "import sys\n"
        "sys.modules['rapidfuzz'] = None  # any import of rapidfuzz now raises ImportError\n"
        "from scieflow._vendor.researcher_core import dedupe\n"
        "from scieflow._vendor.researcher_core._fuzz import fuzz\n"
        "assert dedupe.fuzz is fuzz\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    done = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_no_scieflow_module_imports_rapidfuzz() -> None:
    offenders: list[str] = []
    for path in (REPO / "src" / "scieflow").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "rapidfuzz" for name in names):
                offenders.append(path.relative_to(REPO).as_posix())
    assert offenders == []
