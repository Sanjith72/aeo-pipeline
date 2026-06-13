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

# Progressive results (#7): the orchestrator reports stage boundaries to this sink so
# a polling client sees per-stage updates as the audit runs.
ProgressFn = Callable[[str, dict[str, Any]], None]
# (domain, name, progress) -> result dict. `progress` is optional (None disables it).
AuditRunner = Callable[[str, str, ProgressFn | None], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str = JOB_QUEUED
    progress: str = ""
    # #7 — per-stage updates the client polls (stage name + counts), appended as the
    # run progresses so findings render incrementally instead of one long spinner.
    stages: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobRegistry:
    """Thread/async-safe enough for a single-process app: plain dict, last-write-wins."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str) -> Job:
        now = time.time()
        job = Job(id=uuid.uuid4().hex, kind=kind, created_at=now, updated_at=now)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        return job

    def record_stage(self, job_id: str, stage: str, counts: dict[str, Any]) -> Job | None:
        """Append a per-stage progress event (#7) and surface its name as the live
        ``progress`` string. Called from the audit thread as each stage completes."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.stages.append({"stage": stage, "counts": dict(counts), "at": time.time()})
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


def spawn_audit(job_id: str, *, domain: str, name: str) -> threading.Thread:
    """Run an audit on a dedicated daemon thread (with its own event loop) so a long
    crawl/score/analyze never blocks the API's event loop — keeping ``/api/audit/{id}``
    polling and the rest of the server responsive while it runs. Reads the module-level
    ``default_audit_runner`` at call time so it stays monkeypatchable in tests."""
    runner = default_audit_runner

    def _run() -> None:
        asyncio.run(execute_audit(JOBS, job_id, domain=domain, name=name, runner=runner))

    thread = threading.Thread(target=_run, name=f"audit-{job_id}", daemon=True)
    thread.start()
    return thread


async def default_audit_runner(
    domain: str, name: str, progress: ProgressFn | None = None
) -> dict[str, Any]:
    """The real deep audit: register the client target, then run the v4 weekly audit cycle
    (discover → blueprint → coverage → crawl → score → analyze → site report). Needs a live
    DB + network — hence the injectable seam so tests use a fake. ``progress`` (optional)
    is threaded into the orchestrator for per-stage updates."""
    from ..pipeline import Orchestrator
    from ..storage.repos import targets as targets_repo

    target = targets_repo.upsert(name or domain, domain, "client")
    return await Orchestrator().audit_cycle(domain, target=target, progress=progress)
