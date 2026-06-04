"""Observability + failure-isolation utilities (cross-cutting).

- ``trace_step`` / ``StepHandle`` — per-agent/per-step tracing (writes one
  ``agent_traces`` row, binds structlog contextvars, re-raises on error).
- the Error Sink (``error_sink`` module) builds on this to isolate page failures.
"""

from __future__ import annotations

from .error_sink import PageOutcome, page_guard, record_failure
from .tracing import StepHandle, trace_step

__all__ = [
    "PageOutcome",
    "StepHandle",
    "page_guard",
    "record_failure",
    "trace_step",
]
