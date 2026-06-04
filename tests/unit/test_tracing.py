"""Observability tracing — offline (the trace repo is monkeypatched).

``trace_step`` is the single per-step trace writer: it binds structlog
contextvars for the step, times it, writes exactly one ``agent_traces`` row
(success or failed), and re-raises on error so the Error Sink can isolate the
page. Persisting a trace must NEVER itself break a run.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from aeo.obs import tracing as tracing_mod
from aeo.obs.tracing import trace_step


class _Recorder:
    """Captures trace_repo.record(...) calls instead of hitting Postgres."""

    def __init__(self, *, explode: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._explode = explode

    def __call__(self, agent: str, status: str, **kwargs: Any) -> int:
        if self._explode:
            raise RuntimeError("db down")
        self.calls.append({"agent": agent, "status": status, **kwargs})
        return len(self.calls)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(tracing_mod.traces_repo, "record", rec)
    return rec


class TestTraceStepSuccess:
    def test_writes_one_success_row(self, recorder):
        with trace_step("analyzer", run_id=7, page_id=42, step="score"):
            pass
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["agent"] == "analyzer"
        assert call["status"] == "success"
        assert call["run_id"] == 7
        assert call["page_id"] == 42
        assert call["step"] == "score"
        assert isinstance(call["duration_ms"], int)
        assert call["duration_ms"] >= 0

    def test_handle_propagates_model_and_tokens(self, recorder):
        with trace_step("recommender", page_id=1) as step:
            step.model = "gpt-4o-mini"
            step.tokens = 350
        call = recorder.calls[0]
        assert call["model"] == "gpt-4o-mini"
        assert call["tokens"] == 350


class TestTraceStepFailure:
    def test_writes_failed_row_and_reraises(self, recorder):
        with pytest.raises(ValueError, match="boom"), \
                trace_step("validator", page_id=9, step="rescore"):
            raise ValueError("boom")
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["status"] == "failed"
        assert "boom" in call["error"]
        assert call["page_id"] == 9


class TestContextvarBinding:
    def test_bound_during_unbound_after(self, recorder):
        structlog.contextvars.clear_contextvars()
        with trace_step("gap", run_id=3, page_id=5):
            ctx = structlog.contextvars.get_contextvars()
            assert ctx.get("agent") == "gap"
            assert ctx.get("run_id") == 3
            assert ctx.get("page_id") == 5
        after = structlog.contextvars.get_contextvars()
        assert "agent" not in after
        assert "page_id" not in after


class TestTracingNeverBreaksTheRun:
    def test_record_failure_is_swallowed_on_success_path(self, monkeypatch):
        monkeypatch.setattr(tracing_mod.traces_repo, "record", _Recorder(explode=True))
        # Body succeeds; a DB write failure inside trace_step must not propagate.
        with trace_step("analyzer", page_id=1):
            pass  # no exception should escape

    def test_original_error_propagates_not_the_record_error(self, monkeypatch):
        monkeypatch.setattr(tracing_mod.traces_repo, "record", _Recorder(explode=True))
        # Body raises ValueError; even though the failed-trace write also raises,
        # the caller must see the ORIGINAL ValueError, not the DB RuntimeError.
        with pytest.raises(ValueError, match="real error"), \
                trace_step("analyzer", page_id=1):
            raise ValueError("real error")
