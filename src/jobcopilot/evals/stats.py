"""Small-sample statistics, so that noise is not read as progress: bootstrap intervals, seeded to be repeatable."""

from __future__ import annotations

import random

RESAMPLES = 1000


def _interval(values: list[float], seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(RESAMPLES))
    return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def mean_ci(values: list[float], seed: int = 0) -> tuple[float, float, float]:
    """(mean, low, high): the mean with its 95% bootstrap interval."""
    if not values:
        return 0.0, 0.0, 0.0
    mean = sum(values) / len(values)
    low, high = _interval(list(values), seed)
    return mean, low, high


def paired_change(new: dict[str, float], base: dict[str, float], seed: int = 0) -> tuple[float, float, float, str]:
    """(change, low, high, verdict) on the cases both runs share. "worse" or "better" only when the whole
    95% interval of the per-case differences is on one side of zero; otherwise "same"; "new" with no shared case."""
    shared = sorted(set(new) & set(base))
    if not shared:
        return 0.0, 0.0, 0.0, "new"
    diffs = [new[k] - base[k] for k in shared]
    change = sum(diffs) / len(diffs)
    low, high = _interval(diffs, seed)
    verdict = "worse" if high < 0 else "better" if low > 0 else "same"
    return change, low, high, verdict
