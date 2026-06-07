"""
Per-agent/per-step execution tracing.

``trace_step`` is the single writer of ``agent_traces`` rows. It wraps one unit
of work in the per-page pipeline (Analyze → Gap → Recommend → Validate → Report):

  - binds ``agent`` / ``step`` / ``run_id`` / ``page_id`` into the structlog
    contextvars so *every* log line emitted inside the step carries them;
  - times the step (``duration_ms``);
  - on success writes one ``status='success'`` trace row;
  - on exception writes one ``status='failed'`` row (with the error text) and
    **re-raises** — the Error Sink decides whether to skip the page.

Two hard rules keep tracing safe:
  1. Persisting a trace must never break a run — a DB write failure here is
     logged and swallowed, never propagated.
  2. On the failure path the *original* exception is always the one that
     propagates, even if writing the failed-trace row itself errors.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import structlog

from ..logging import get_logger
from ..storage.repos import traces as traces_repo
from .otel import otel_span

log = get_logger(__name__)

_CTX_KEYS = ("agent", "step", "run_id", "page_id")


@dataclass
class StepHandle:
    """Mutable handle yielded by ``trace_step`` so the body can record runtime
    facts discovered mid-step (e.g. the LLM model used and token count)."""

    model: str | None = None
    tokens: int | None = None


def _safe_record(**kwargs) -> None:
    """Write a trace row, swallowing any failure (tracing never breaks a run)."""
    try:
        traces_repo.record(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("trace_record_failed", error=str(exc), agent=kwargs.get("agent"))


@contextmanager
def trace_step(
    agent: str,
    *,
    run_id: int | None = None,
    page_id: int | None = None,
    step: str | None = None,
    model: str | None = None,
) -> Iterator[StepHandle]:
    """Trace one pipeline step. Yields a :class:`StepHandle` for model/tokens."""
    handle = StepHandle(model=model)
    tokens = structlog.contextvars.bind_contextvars(
        agent=agent, step=step, run_id=run_id, page_id=page_id
    )
    start = time.perf_counter()
    log.info("step_started")
    # An OTLP span runs alongside the agent_traces row (no-op unless OTEL is enabled).
    with otel_span(
        f"aeo.{agent}", **{"aeo.agent": agent, "aeo.step": step or "", "aeo.run_id": run_id, "aeo.page_id": page_id}
    ) as span:
        try:
            yield handle
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            _safe_record(
                agent=agent, status="failed", run_id=run_id, page_id=page_id, step=step,
                duration_ms=duration_ms, model=handle.model, tokens=handle.tokens,
                error=str(exc),
            )
            log.warning("step_failed", duration_ms=duration_ms, error=str(exc))
            _annotate_span(span, "failed", duration_ms, handle)
            raise
        else:
            duration_ms = int((time.perf_counter() - start) * 1000)
            _safe_record(
                agent=agent, status="success", run_id=run_id, page_id=page_id, step=step,
                duration_ms=duration_ms, model=handle.model, tokens=handle.tokens,
            )
            log.info("step_succeeded", duration_ms=duration_ms)
            _annotate_span(span, "success", duration_ms, handle)
        finally:
            structlog.contextvars.reset_contextvars(**tokens)


def _annotate_span(span, status: str, duration_ms: int, handle: StepHandle) -> None:
    """Mirror the trace row's runtime facts onto the OTLP span (best-effort)."""
    if span is None:
        return
    try:
        span.set_attribute("aeo.status", status)
        span.set_attribute("aeo.duration_ms", duration_ms)
        if handle.model:
            span.set_attribute("llm.model", handle.model)
        if handle.tokens is not None:
            span.set_attribute("llm.tokens", handle.tokens)
    except Exception:  # pragma: no cover - defensive
        pass
