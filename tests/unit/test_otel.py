"""Optional OpenTelemetry export (obs/otel.py). The default/no-SDK path must be a
hard no-op; the enabled path must produce a real span without ever breaking the run."""

from __future__ import annotations

import pytest

from aeo.obs import otel
from aeo.settings import get_settings


def _reset_tracer():
    otel._tracer.cache_clear()


def test_disabled_by_default_is_noop():
    _reset_tracer()
    assert otel.otel_enabled() is False
    with otel.otel_span("aeo.test", **{"aeo.agent": "x", "aeo.run_id": 1}) as span:
        assert span is None  # no-op yields None
    _reset_tracer()


def test_body_exception_propagates_when_disabled():
    _reset_tracer()
    with pytest.raises(ValueError, match="boom"), otel.otel_span("aeo.test"):
        raise ValueError("boom")
    _reset_tracer()


def test_enabled_creates_real_span(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    s = get_settings()
    monkeypatch.setattr(s.obs, "otel_enabled", True)
    _reset_tracer()
    try:
        assert otel.otel_enabled() is True
        with otel.otel_span("aeo.test", **{"aeo.agent": "analyze", "aeo.run_id": 7}) as span:
            assert span is not None
            span.set_attribute("extra", "v")  # must not raise
    finally:
        _reset_tracer()


def test_enabled_records_exception_and_reraises(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    s = get_settings()
    monkeypatch.setattr(s.obs, "otel_enabled", True)
    _reset_tracer()
    try:
        with pytest.raises(RuntimeError, match="kaboom"), otel.otel_span("aeo.test") as span:
            assert span is not None
            raise RuntimeError("kaboom")
    finally:
        _reset_tracer()


def test_trace_step_still_works_with_otel_enabled(monkeypatch):
    """The agent_traces path is unchanged when OTEL is on (trace persistence is
    mocked; the point is that wrapping in a span doesn't alter control flow)."""
    pytest.importorskip("opentelemetry.sdk.trace")
    from aeo.obs import tracing

    recorded: list[dict] = []
    monkeypatch.setattr(tracing, "_safe_record", lambda **kw: recorded.append(kw))
    s = get_settings()
    monkeypatch.setattr(s.obs, "otel_enabled", True)
    _reset_tracer()
    try:
        with tracing.trace_step("analyze", run_id=1, page_id=2, step="gap") as h:
            h.model = "gemini-2.0-flash"
            h.tokens = 42
        assert recorded and recorded[-1]["status"] == "success"
        assert recorded[-1]["model"] == "gemini-2.0-flash"
    finally:
        _reset_tracer()
