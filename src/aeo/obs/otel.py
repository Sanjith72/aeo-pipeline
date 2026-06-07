"""
Optional OpenTelemetry export — runs ALONGSIDE the ``agent_traces`` table.

The custom trace store (``obs.tracing`` → ``agent_traces`` + ``aeo trace``) is the
queryable per-page journey and is always on. This module adds standards-aligned OTLP
spans on top, for teams that run a collector (Tempo / Jaeger / Honeycomb). It is:

  * **off by default** (``AEO__OBS__OTEL_ENABLED=false``);
  * a **hard no-op** when the SDK isn't installed or no endpoint is set — so
    OpenTelemetry is never a required dependency;
  * **exception-proof** — any failure in the OTEL machinery degrades to a bare span
    with no effect on the run (the deterministic-first / "tracing never breaks a run"
    contract the rest of obs/ holds to).

``otel_span`` is the single entry point; ``obs.tracing.trace_step`` wraps each pipeline
step in one.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)


def _coerce(value: Any) -> Any:
    """OTEL attribute values must be str/bool/int/float (or homogeneous sequences)."""
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


@lru_cache(maxsize=1)
def _tracer() -> Any | None:
    """Build (once) an OTEL tracer, or return ``None`` to signal the no-op path.

    Returns None when OTEL is disabled, the SDK is missing, or init fails — every
    caller treats None as 'no tracing', so nothing downstream has to care."""
    s = get_settings().obs
    if not s.otel_enabled:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: s.otel_service_name}))
        if s.otel_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otel_endpoint)))
        trace.set_tracer_provider(provider)
        log.info("otel_enabled", endpoint=s.otel_endpoint or "(no exporter)", service=s.otel_service_name)
        return trace.get_tracer("aeo")
    except Exception as exc:  # SDK missing or init failure → no-op, never raise
        log.warning("otel_init_failed", error=str(exc))
        return None


def otel_enabled() -> bool:
    """True only when a real tracer is active (SDK present + configured)."""
    return _tracer() is not None


@contextmanager
def otel_span(name: str, **attributes: Any) -> Iterator[Any | None]:
    """Run a block inside an OTEL span (or a no-op). Yields the span or ``None``.

    Manually drives the span context manager so the *original* body exception always
    propagates and the span is recorded+closed even on the error path. Any failure in
    the OTEL layer itself falls back to a bare ``yield None``."""
    tracer = _tracer()
    if tracer is None:
        yield None
        return

    try:
        span_cm = tracer.start_as_current_span(name)
        span = span_cm.__enter__()
        for k, v in attributes.items():
            if v is not None:
                with contextlib.suppress(Exception):  # pragma: no cover - defensive
                    span.set_attribute(k, _coerce(v))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("otel_span_start_failed", error=str(exc))
        yield None
        return

    try:
        yield span
    except Exception as exc:
        try:
            span.record_exception(exc)
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, str(exc)))
        except Exception:  # pragma: no cover - defensive
            pass
        with contextlib.suppress(Exception):  # pragma: no cover - defensive
            span_cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with contextlib.suppress(Exception):  # pragma: no cover - defensive
            span_cm.__exit__(None, None, None)
