"""Tiny timing context manager."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def stopwatch() -> Iterator[dict]:
    """
    with stopwatch() as t:
        ...
    print(t["elapsed_ms"])
    """
    state: dict[str, float] = {"elapsed_ms": 0.0}
    start = time.perf_counter()
    try:
        yield state
    finally:
        state["elapsed_ms"] = (time.perf_counter() - start) * 1000.0
