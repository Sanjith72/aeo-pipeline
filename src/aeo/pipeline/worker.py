"""
Queue worker — drains crawl jobs from the Postgres-backed queue.

Horizontal scaling story: run N of these against the same database. Each
claims work with ``FOR UPDATE SKIP LOCKED`` so they never collide, and no
external broker (Redis/RabbitMQ) is required. A job is one crawl batch for one
target, so the browser is reused across the batch's URLs.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Any

from ..logging import get_logger
from ..settings import get_settings
from ..storage.repos import jobs as jobs_repo
from ..storage.repos import targets as targets_repo
from .orchestrator import Orchestrator

log = get_logger(__name__)

CRAWL_BATCH = "crawl_batch"
ANALYZE_RUN = "analyze_run"
AGENT_RUN = "agent_run"

# Lease on a claimed job. Sized ABOVE the worst legitimate job with shipped defaults —
# a react agent run can take react_max_steps × (LLM decision + tool) ≈ 48 min plus the
# ladder fallback, and a big crawl batch runs long too — so a live job is never reaped
# as dead. succeed()/fail() are fenced on locked_by besides, so even a mis-sized lease
# cannot let two executions both write.
JOB_LEASE_SEC = 7200
_REAP_INTERVAL_SEC = 60.0  # how often a worker sweeps for expired leases


def enqueue_batch(
    urls: list[str],
    target_name: str,
    label: str | None = None,
    max_attempts: int = 4,
    force_recrawl: bool = False,
) -> int:
    """Enqueue a crawl batch. Returns the job id. ``force_recrawl`` bypasses the
    fingerprint skip gate so an unchanged page is re-read + re-scored — required for the
    v5 CH-15 close→verify re-crawl (the edit may not be live yet)."""
    return jobs_repo.enqueue(
        CRAWL_BATCH,
        {"urls": urls, "target": target_name, "label": label, "force_recrawl": force_recrawl},
        max_attempts=max_attempts,
    )


def enqueue_analysis(run_id: int, max_attempts: int = 4) -> int:
    """Enqueue the back-half analysis (Gap -> Validate -> Report) for a run."""
    return jobs_repo.enqueue(ANALYZE_RUN, {"run_id": run_id}, max_attempts=max_attempts)


def enqueue_agent_run(run_id: str, max_attempts: int = 3) -> int:
    """Enqueue an assistive agent run (AgentRunController.run) for a worker to drive."""
    return jobs_repo.enqueue(AGENT_RUN, {"run_id": run_id}, max_attempts=max_attempts)


class Worker:
    def __init__(
        self,
        worker_id: str | None = None,
        kinds: list[str] | None = None,
        idle_sleep: float = 5.0,
    ) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.kinds = kinds or [CRAWL_BATCH, ANALYZE_RUN, AGENT_RUN]
        self.idle_sleep = idle_sleep
        self._orch = Orchestrator()
        self._last_reap = 0.0

    def run_forever(self, max_jobs: int | None = None) -> int:
        """Claim and run jobs until ``max_jobs`` processed (None = forever).
        ``max_jobs`` exists mainly so tests/CI can run a bounded worker."""
        processed = 0
        log.info("worker_start", worker_id=self.worker_id, kinds=self.kinds)
        while max_jobs is None or processed < max_jobs:
            self._reap_stale()
            job = jobs_repo.claim(self.worker_id, self.kinds)
            if not job:
                time.sleep(self.idle_sleep)
                continue
            self._run_job(job)
            processed += 1
        return processed

    def _reap_stale(self) -> None:
        """Recover jobs whose worker died mid-run (expired lease). Requeued jobs resume via
        the runtime's at-least-once path; a job that is out of attempts goes 'dead', and a
        dead AGENT_RUN also fails its run row so the UI stops polling a corpse. Throttled —
        the sweep is one UPDATE, but there is no reason to run it every claim."""
        now = time.monotonic()
        if now - self._last_reap < _REAP_INTERVAL_SEC:
            return
        self._last_reap = now
        try:
            reaped = jobs_repo.reap_stale(JOB_LEASE_SEC)
        except Exception as exc:  # the sweep must never take the worker down
            log.error("reap_failed", error=str(exc))
            return
        for job in reaped:
            log.info("job_reaped", job_id=job["id"], kind=job["kind"], status=job["status"])
            if job["status"] == "dead" and job["kind"] == AGENT_RUN:
                from ..storage.repos import agent_runs as agent_runs_repo  # lazy: import cycle

                run_id = str((job.get("payload") or {}).get("run_id") or "")
                if run_id:
                    # only_from: a run that already STAGED before its worker died must
                    # stay staged (the work is done) — only in-flight runs are failed.
                    agent_runs_repo.set_status(
                        run_id, "failed",
                        error=f"worker lost mid-run (lease expired after {JOB_LEASE_SEC}s)",
                        only_from=("queued", "planning"),
                    )

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        try:
            self._dispatch(job)
            # Fenced on our claim: if the lease expired mid-run and the job was reaped
            # (maybe re-claimed elsewhere), this late write is discarded, not a stomp.
            if not jobs_repo.succeed(job_id, worker_id=self.worker_id):
                log.warning("job_done_after_lease_lost", job_id=job_id, kind=job["kind"])
                return
            log.info("job_done", job_id=job_id, kind=job["kind"])
        except Exception as exc:  # failures are requeued, not fatal
            backoff = _backoff(int(job.get("attempts", 1)))
            jobs_repo.fail(job_id, str(exc), backoff_sec=backoff, worker_id=self.worker_id)
            log.error("job_failed", job_id=job_id, kind=job.get("kind"), error=str(exc), backoff=backoff)

    def _dispatch(self, job: dict[str, Any]) -> None:
        kind = job["kind"]
        payload = job.get("payload") or {}
        if kind == CRAWL_BATCH:
            target = targets_repo.find(payload["target"])
            if target is None:
                raise ValueError(f"unknown target: {payload['target']!r}")
            # payload.get keeps pre-v5 queued jobs (no key) at force_recrawl=False.
            asyncio.run(self._orch.run_urls(
                payload["urls"], target=target, label=payload.get("label"),
                force_recrawl=bool(payload.get("force_recrawl")),
            ))
        elif kind == ANALYZE_RUN:
            self._orch.analyze_run(int(payload["run_id"]))
        elif kind == AGENT_RUN:
            from ..agents.runtime import AgentRunController  # lazy: avoid import cycle

            AgentRunController().run(str(payload["run_id"]))
        else:
            raise ValueError(f"unhandled job kind: {kind!r}")


def _backoff(attempts: int) -> int:
    cfg = get_settings().crawler.retry
    return int(min(cfg.max_backoff_sec, cfg.initial_backoff_sec * (2 ** max(0, attempts - 1))))
