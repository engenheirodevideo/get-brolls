"""Wall-clock helper for the cost tests: the best of a few samples, never a single one.

A cost test asks "can this run fast", not "did this run fast once": a loaded machine (the suite on two Pythons plus
the coverage gate at the same time) can stall one sample, but not all of them. A real regression (a quadratic
pattern, a lost cache) is slow in every sample, so the minimum still fails it.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

SAMPLES = 5


def best_of(call: Callable[[], T], limit: float, samples: int = SAMPLES) -> tuple[float, T]:
    """Run `call()` up to `samples` times, stopping early at the first sample under `limit`.

    Returns the smallest elapsed time and the result of the last run. Assert `best < limit` on the first value."""
    best = float("inf")
    result = None
    for _ in range(samples):
        started = time.perf_counter()
        result = call()
        best = min(best, time.perf_counter() - started)
        if best < limit:
            break
    return best, result  # type: ignore[return-value]
