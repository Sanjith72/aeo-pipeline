"""
In-process async job registry for long-running API operations (SP-4b v3).

A deep audit (full crawl → score → analyze → site report) is far too slow for a
request/response cycle, so ``POST /api/audit`` starts it as a background job and returns a
job id the client polls via ``GET /api/audit/{id}``. This is a single-process, in-memory
registry — right for the internal-tool scale; the DB-backed ``pipeline/worker.py`` queue is
the path for heavy / multi-worker scale.

The audit runner is **injectable** (``default_audit_runner`` does the real DB + crawl work),
so the job lifecycle is unit-tested with a fake runner — no DB, no network.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger

log = get_logger(__name__)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

# Bound the in-memory registry so finished jobs can't accumulate for the process lifetime.
# Active jobs are never evicted; oldest finished ones are dropped first.
_MAX_JOBS = 500

# Progressive results (#7): the orchestrator reports stage boundaries to this sink so
# a polling client sees per-stage updates as the audit runs.
ProgressFn = Callable[[str, dict[str, Any]], None]
# (domain, name, progress) -> result dict. `progress` is optional (None disables it).
AuditRunner = Callable[[str, str, ProgressFn | None], Awaitable[dict[str, Any]]]


def _json_safe(v: Any) -> Any:
    """Coerce a progress-count value to a JSON-serializable form: primitives pass through,
    anything else (a method, an object, a datetime…) becomes its ``str()``. Keeps a buggy
    stage emit from breaking the polling endpoint that serializes the whole job."""
    return v if isinstance(v, (str, int, float, bool)) or v is None else str(v)


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    # Dedupe key (e.g. the audit domain) — lets the registry collapse duplicate in-flight
    # requests for the same work. Empty = not deduped.
    key: str = ""
    status: str = JOB_QUEUED
    progress: str = ""
    # #7 — per-stage updates the client polls (stage name + counts), appended as the
    # run progresses so findings render incrementally instead of one long spinner.
    stages: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    # R2-2 drop-off safety: a cooperative cancel flag the running audit polls between
    # pages. Set via JobRegistry.request_cancel / POST /api/audit/{id}/cancel.
    cancelled: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "stages": self.stages,
            "result": self.result,
            "error": self.error,
            "cancelled": self.cancelled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobRegistry:
    """Thread/async-safe enough for a single-process app: plain dict, last-write-wins."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str, key: str = "") -> Job:
        now = time.time()
        self._evict()
        job = Job(id=uuid.uuid4().hex, kind=kind, key=key, created_at=now, updated_at=now)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active_for(self, kind: str, key: str) -> Job | None:
        """The newest in-flight (queued/running) job matching kind+key, or None — for
        request dedupe so the same domain isn't audited twice concurrently."""
        matches = [
            j for j in self._jobs.values()
            if j.kind == kind and j.key == key and j.status in (JOB_QUEUED, JOB_RUNNING)
        ]
        return max(matches, key=lambda j: j.created_at) if matches else None

    def active_count(self, kind: str) -> int:
        """How many jobs of this kind are queued/running — for the concurrency cap."""
        return sum(
            1 for j in self._jobs.values()
            if j.kind == kind and j.status in (JOB_QUEUED, JOB_RUNNING)
        )

    def _evict(self) -> None:
        """Make room before an insert: drop the oldest finished jobs so the registry stays
        at or below ``_MAX_JOBS`` after the caller adds one. Active jobs are never evicted,
        so the cap is best-effort under heavy concurrent load. ``cancelled`` is terminal too,
        so cancelled jobs are evictable (they don't pin slots)."""
        if len(self._jobs) < _MAX_JOBS:
            return
        terminal = (JOB_SUCCEEDED, JOB_FAILED)
        finished = sorted(
            (j for j in self._jobs.values()
             if j.status in terminal or j.cancelled),
            key=lambda j: j.updated_at,
        )
        for job in finished:
            if len(self._jobs) < _MAX_JOBS:
                break
            self._jobs.pop(job.id, None)

    def update(self, job_id: str, **changes: Any) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        return job

    def request_cancel(self, job_id: str) -> Job | None:
        """Flag a running audit for cooperative cancellation (R2-2). The audit thread
        polls ``Job.cancelled`` between pages and early-exits. Unknown id → None."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.cancelled = True
        job.updated_at = time.time()
        return job

    def record_stage(self, job_id: str, stage: str, counts: dict[str, Any]) -> Job | None:
        """Append a per-stage progress event (#7) and surface its name as the live
        ``progress`` string. Called from the audit thread as each stage completes. Counts are
        coerced JSON-safe so a stray non-primitive (e.g. a stage emitting an uncalled method)
        degrades to its repr instead of 500ing the ``/api/audit/{id}`` poller that serializes
        the job."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        safe = {k: _json_safe(v) for k, v in counts.items()}
        job.stages.append({"stage": stage, "counts": safe, "at": time.time()})
        job.progress = stage
        job.updated_at = time.time()
        return job


# Module-level registry shared by the API process.
JOBS = JobRegistry()


async def execute_audit(
    registry: JobRegistry,
    job_id: str,
    *,
    domain: str,
    name: str,
    runner: AuditRunner,
) -> None:
    """Run an audit job to completion, recording status/result/error on the registry.
    Never raises — any failure is captured on the job so the poller sees ``failed``."""
    registry.update(job_id, status=JOB_RUNNING, progress=f"auditing {domain}…")

    def _sink(stage: str, counts: dict[str, Any]) -> None:
        registry.record_stage(job_id, stage, counts)

    try:
        result = await runner(domain, name, _sink)
        registry.update(job_id, status=JOB_SUCCEEDED, progress="complete", result=result)
    except Exception as exc:  # the job records the failure; the endpoint never 500s
        log.warning("audit_job_failed", job_id=job_id, error=str(exc))
        registry.update(job_id, status=JOB_FAILED, progress="failed", error=str(exc))


def spawn_audit(
    job_id: str, *, domain: str, name: str, force_recrawl: bool = False
) -> threading.Thread:
    """Run an audit on a dedicated daemon thread (with its own event loop) so a long
    crawl/score/analyze never blocks the API's event loop — keeping ``/api/audit/{id}``
    polling and the rest of the server responsive while it runs. Reads the module-level
    ``default_audit_runner`` at call time so it stays monkeypatchable in tests.

    ``force_recrawl`` bypasses the fingerprint skip gate for this run; the audit also
    polls ``Job.cancelled`` between pages so the client can abandon it (R2-2)."""
    base_runner = default_audit_runner

    def _should_cancel() -> bool:
        job = JOBS.get(job_id)
        return bool(job and job.cancelled)

    async def runner(d: str, n: str, progress: ProgressFn | None) -> dict[str, Any]:
        return await base_runner(
            d, n, progress, force_recrawl=force_recrawl, should_cancel=_should_cancel
        )

    def _run() -> None:
        asyncio.run(execute_audit(JOBS, job_id, domain=domain, name=name, runner=runner))

    thread = threading.Thread(target=_run, name=f"audit-{job_id}", daemon=True)
    thread.start()
    return thread


async def default_audit_runner(
    domain: str,
    name: str,
    progress: ProgressFn | None = None,
    *,
    force_recrawl: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """The real deep audit: register the client target, then run the v4 weekly audit cycle
    (discover → blueprint → coverage → crawl → score → analyze → site report). Needs a live
    DB + network — hence the injectable seam so tests use a fake. ``progress`` (optional)
    is threaded into the orchestrator for per-stage updates; ``force_recrawl`` /
    ``should_cancel`` carry the R2-2 re-crawl + drop-off-safety controls."""
    from ..pipeline import Orchestrator
    from ..storage.repos import targets as targets_repo

    target = targets_repo.upsert(name or domain, domain, "client")
    return await Orchestrator().audit_cycle(
        domain, target=target, progress=progress,
        force_recrawl=force_recrawl, should_cancel=should_cancel,
    )
