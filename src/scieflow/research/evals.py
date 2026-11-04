"""Evaluation primitives for the M-H systematic-review harness (plan 5.11): Wilson score intervals,
binary confusion metrics and Cohen's kappa from a 2x2 table.

Pure functions only: no clock, no network, no file reads, no new dependency (`statistics` is stdlib).
Wilson 1927, J Am Stat Assoc 22:209-212; Cohen 1960, Educ Psychol Meas 20:37-46.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Iterable, Optional

Z95 = statistics.NormalDist().inv_cdf(0.975)

INCLUDE, EXCLUDE = "include", "exclude"


@dataclass(frozen=True)
class Interval:
    """A proportion k/n with its Wilson score interval."""

    k: int
    n: int
    point: float
    low: float
    high: float

    def to_json_dict(self) -> dict:
        return asdict(self)


def wilson(k: int, n: int, z: float = Z95) -> Interval:
    """Wilson score interval for k successes in n trials. Refuses n == 0 and k outside [0, n]."""
    if n == 0:
        raise ValueError("a Wilson interval needs n > 0")
    if not 0 <= k <= n:
        raise ValueError("k must satisfy 0 <= k <= n")
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    low = 0.0 if k == 0 else min(max(0.0, centre - half), 1.0)
    high = 1.0 if k == n else min(max(0.0, centre + half), 1.0)
    return Interval(k=k, n=n, point=p, low=low, high=high)


def format_interval(interval: Interval) -> str:
    """The printed form of plan 5.11: ``9/10 = 0.900000 [0.595850, 0.982124]``."""
    return f"{interval.k}/{interval.n} = {interval.point:.6f} [{interval.low:.6f}, {interval.high:.6f}]"


def format_metric(name: str, interval: Optional[Interval]) -> str:
    if interval is None:
        return f"{name} undefined (no cases)"
    return f"{name} {format_interval(interval)}"


@dataclass(frozen=True)
class Confusion:
    """Binary confusion counts with the label `include` as positive."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> "Confusion":
        """Count (gold, predicted) pairs; anything other than `include` is a negative."""
        tp = fp = fn = tn = 0
        for gold, predicted in pairs:
            positive_gold = gold == INCLUDE
            positive_pred = predicted == INCLUDE
            if positive_gold and positive_pred:
                tp += 1
            elif positive_pred:
                fp += 1
            elif positive_gold:
                fn += 1
            else:
                tn += 1
        return cls(tp=tp, fp=fp, fn=fn, tn=tn)

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    def _metric(self, k: int, n: int) -> Optional[Interval]:
        return None if n == 0 else wilson(k, n)

    @property
    def sensitivity(self) -> Optional[Interval]:
        return self._metric(self.tp, self.tp + self.fn)

    @property
    def specificity(self) -> Optional[Interval]:
        return self._metric(self.tn, self.tn + self.fp)

    @property
    def accuracy(self) -> Optional[Interval]:
        return self._metric(self.tp + self.tn, self.total)

    @property
    def ppv(self) -> Optional[Interval]:
        return self._metric(self.tp, self.tp + self.fp)


def cohens_kappa_2x2(a: int, b: int, c: int, d: int) -> Optional[Fraction]:
    """Cohen's kappa of a 2x2 table, exact: a both include, b A include/B exclude, c A exclude/B include,
    d both exclude. ``kappa = (po - pe) / (1 - pe)``; None when pe == 1 (undefined) or n == 0."""
    n = a + b + c + d
    if n == 0:
        return None
    po = Fraction(a + d, n)
    pe = Fraction((a + b) * (a + c) + (c + d) * (b + d), n * n)
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)
