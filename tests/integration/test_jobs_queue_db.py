"""Live-DB regression for the durable jobs queue's kind-filtered claim.

The DB queue is what the agent runtime (Plan 2A) reuses to schedule AGENT_RUN jobs.
``claim(worker_id, kinds)`` must bind ``kinds`` to the ``kind = ANY(%s)`` filter and
``worker_id`` to ``locked_by`` — the placeholders fill in textual order, so the params
must be ordered to match. Skips cleanly when no DB is reachable.
"""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check, transaction
from aeo.storage.repos import jobs as jobs_repo

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")


def _cleanup(job_ids: list[int]) -> None:
    if not job_ids:
        return
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = ANY(%s)", (job_ids,))


def test_claim_with_kind_filter_returns_only_matching_kind() -> None:
    a = jobs_repo.enqueue("zz_test_kind_a", {"n": 1})
    b = jobs_repo.enqueue("zz_test_kind_b", {"n": 2})
    try:
        # A worker restricted to kind_b must skip the kind_a job and claim the kind_b one.
        claimed = jobs_repo.claim("worker-test:1", ["zz_test_kind_b"])
        assert claimed is not None
        assert claimed["id"] == b
        assert claimed["kind"] == "zz_test_kind_b"
        assert claimed["locked_by"] == "worker-test:1"
    finally:
        _cleanup([a, b])
