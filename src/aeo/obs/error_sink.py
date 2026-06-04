"""
Error Sink — page-level failure isolation.

The principle the shipped crawler already follows (``run_all`` floors a failed
criterion; a crashed crawl marks ``crawl_status='failed'``) generalized into one
helper every pipeline block calls: **one bad page never kills a run.**

``page_guard`` wraps a unit of per-page work. On any exception it:
  - logs ``page_skipped`` with the failing agent + error;
  - writes one ``agent='error_sink'`` failed trace (so ``aeo trace`` shows the
    page was abandoned and why);
  - records the failure in the yielded :class:`PageOutcome`;
  - swallows the exception (unless ``reraise=True``) so the loop continues.

Successful work writes nothing here — the inner ``trace_step`` calls already
record per-step successes. Persisting the failed trace can never itself break a
run (write errors are swallowed).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..logging import get_logger
from ..storage.repos import traces as traces_repo

log = get_logger(__name__)


@dataclass
class PageOutcome:
    """Result handle yielded by ``page_guard`` — lets the caller skip downstream
    stages for a page that failed an earlier one."""

    failed: bool = False
    error: str | None = None


def _safe_record(**kwargs) -> None:
    """Write a trace row, swallowing any failure (the sink never breaks a run)."""
    try:
        traces_repo.record(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("error_sink_record_failed", error=str(exc))


def record_failure(
    agent: str,
    error: str,
    *,
    run_id: int | None = None,
    page_id: int | None = None,
    step: str | None = None,
) -> None:
    """Note a failure that the caller already caught: log + one failed trace.

    Use inside ``except`` blocks that handle their own control flow but still
    want the failure on the page's journey. ``page_guard`` is preferred when the
    goal is "skip this page, keep going."
    """
    _safe_record(
        agent="error_sink", status="failed", run_id=run_id, page_id=page_id,
        step=step or agent, error=f"{agent}: {error}",
    )
    log.warning("page_skipped", agent=agent, page_id=page_id, run_id=run_id, error=error)


@contextmanager
def page_guard(
    agent: str,
    *,
    run_id: int | None = None,
    page_id: int | None = None,
    step: str | None = None,
    reraise: bool = False,
) -> Iterator[PageOutcome]:
    """Isolate a page's work. Swallows exceptions (continue the run) by default."""
    outcome = PageOutcome()
    try:
        yield outcome
    except Exception as exc:
        outcome.failed = True
        outcome.error = str(exc)
        record_failure(agent, str(exc), run_id=run_id, page_id=page_id, step=step)
        if reraise:
            raise
