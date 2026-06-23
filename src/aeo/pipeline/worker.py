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


def enqueue_batch(
    urls: list[str],
    target_name: str,
    label: str | None = None,
    max_attempts: int = 4,
) -> int:
    """Enqueue a crawl batch. Returns the job id."""
    return jobs_repo.enqueue(
        CRAWL_BATCH,
        {"urls": urls, "target": target_name, "label": label},
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

    def run_forever(self, max_jobs: int | None = None) -> int:
        """Claim and run jobs until ``max_jobs`` processed (None = forever).
        ``max_jobs`` exists mainly so tests/CI can run a bounded worker."""
        processed = 0
        log.info("worker_start", worker_id=self.worker_id, kinds=self.kinds)
        while max_jobs is None or processed < max_jobs:
            job = jobs_repo.claim(self.worker_id, self.kinds)
            if not job:
                time.sleep(self.idle_sleep)
                continue
            self._run_job(job)
            processed += 1
        return processed

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        try:
            self._dispatch(job)
            jobs_repo.succeed(job_id)
            log.info("job_done", job_id=job_id, kind=job["kind"])
        except Exception as exc:  # failures are requeued, not fatal
            backoff = _backoff(int(job.get("attempts", 1)))
            jobs_repo.fail(job_id, str(exc), backoff_sec=backoff)
            log.error("job_failed", job_id=job_id, kind=job.get("kind"), error=str(exc), backoff=backoff)

    def _dispatch(self, job: dict[str, Any]) -> None:
        kind = job["kind"]
        payload = job.get("payload") or {}
        if kind == CRAWL_BATCH:
            target = targets_repo.find(payload["target"])
            if target is None:
                raise ValueError(f"unknown target: {payload['target']!r}")
            asyncio.run(self._orch.run_urls(payload["urls"], target=target, label=payload.get("label")))
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
