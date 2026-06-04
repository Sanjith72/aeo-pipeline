"""Error Sink — page-level failure isolation (offline; trace repo monkeypatched).

The contract every block relies on: when a page's work raises, log it, write one
``agent='error_sink'`` failed trace, and **swallow** so the run continues — one
bad page never kills a run. Successful work writes nothing here (inner
``trace_step`` calls already record successes).
"""

from __future__ import annotations

from typing import Any

import pytest

from aeo.obs import error_sink as sink_mod
from aeo.obs.error_sink import page_guard, record_failure


class _Recorder:
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
    monkeypatch.setattr(sink_mod.traces_repo, "record", rec)
    return rec


class TestPageGuardIsolatesFailure:
    def test_swallows_and_marks_outcome(self, recorder):
        with page_guard("recommender", run_id=2, page_id=11) as outcome:
            raise ValueError("kaboom")
        # No exception escaped the guard.
        assert outcome.failed is True
        assert "kaboom" in outcome.error

    def test_writes_one_error_sink_trace(self, recorder):
        with page_guard("recommender", run_id=2, page_id=11, step="content"):
            raise ValueError("kaboom")
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["agent"] == "error_sink"
        assert call["status"] == "failed"
        assert call["page_id"] == 11
        assert call["run_id"] == 2
        assert "recommender" in call["error"]
        assert "kaboom" in call["error"]


class TestPageGuardSuccessPath:
    def test_success_writes_nothing_and_outcome_clean(self, recorder):
        with page_guard("analyzer", page_id=1) as outcome:
            pass
        assert outcome.failed is False
        assert outcome.error is None
        assert recorder.calls == []


class TestReraiseOption:
    def test_reraise_propagates_after_recording(self, recorder):
        with pytest.raises(ValueError, match="boom"), \
                page_guard("validator", page_id=3, reraise=True):
            raise ValueError("boom")
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["status"] == "failed"


class TestRecordFailureHelper:
    def test_writes_failed_trace(self, recorder):
        record_failure("gap", "something broke", run_id=5, page_id=8, step="competitor")
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["agent"] == "error_sink"
        assert call["status"] == "failed"
        assert "gap" in call["error"]
        assert "something broke" in call["error"]


class TestSinkNeverBreaksTheRun:
    def test_trace_write_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(sink_mod.traces_repo, "record", _Recorder(explode=True))
        # Even if persisting the failed trace itself errors, the guard must still
        # swallow the ORIGINAL error and continue.
        with page_guard("reporter", page_id=1) as outcome:
            raise ValueError("original")
        assert outcome.failed is True
        assert "original" in outcome.error
