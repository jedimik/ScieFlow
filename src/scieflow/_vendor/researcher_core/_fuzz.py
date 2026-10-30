"""Exact stand-ins for rapidfuzz's fuzz.token_sort_ratio (M-C1-D2) and fuzz.ratio (M-C2-D1).
ScieFlow-authored, not vendored.

rapidfuzz is not a ScieFlow dependency, so the vendored lines that import it (dedupe.py, verify.py)
are patched to import this shim. ``ratio`` is the same Indel similarity without the token sort.
The score is rapidfuzz's: whitespace tokens are sorted and joined
with one space; the longest common subsequence comes from the bit-parallel algorithm (Hyyro);
the score is (1 - indel distance / total length) * 100, and 100.0 for two empty strings. The
shim is checked against 300 cases recorded from rapidfuzz 3.14.6 (tests/vendor/test_fuzz_parity.py).

Keyword arguments (processor, score_cutoff) are refused: the function takes exactly two strings.
There is no fallback to any other library.
"""

from __future__ import annotations

from types import SimpleNamespace


def _lcs_length(a: str, b: str) -> int:
    if not a or not b:
        return 0
    masks: dict[str, int] = {}
    for index, char in enumerate(b):
        masks[char] = masks.get(char, 0) | (1 << index)
    full = (1 << len(b)) - 1
    v = full
    for char in a:
        u = v & masks.get(char, 0)
        v = ((v + u) | (v - u)) & full
    return len(b) - bin(v).count("1")


def token_sort_ratio(s1: str, s2: str) -> float:
    a = " ".join(sorted(s1.split()))
    b = " ".join(sorted(s2.split()))
    total = len(a) + len(b)
    if total == 0:
        return 100.0
    distance = total - 2 * _lcs_length(a, b)
    return (1.0 - distance / total) * 100.0


def ratio(s1: str, s2: str) -> float:
    total = len(s1) + len(s2)
    if total == 0:
        return 100.0
    distance = total - 2 * _lcs_length(s1, s2)
    return (1.0 - distance / total) * 100.0


fuzz = SimpleNamespace(token_sort_ratio=token_sort_ratio, ratio=ratio)
