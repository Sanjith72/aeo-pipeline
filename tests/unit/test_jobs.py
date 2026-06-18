"""SP-4b v3 — async audit job registry + lifecycle (injectable runner, no DB/network)."""

from __future__ import annotations

import asyncio
from typing import Any

from aeo.api.jobs import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_SUCCEEDED,
    JobRegistry,
    execute_audit,
)


def test_registry_create_get_update() -> None:
    reg = JobRegistry()
    job = reg.create("audit")
    assert job.status == JOB_QUEUED
    assert reg.get(job.id) is job
    assert reg.get("missing") is None
    reg.update(job.id, status="running", progress="x")
    assert reg.get(job.id).status == "running"
    assert reg.update("missing", status="x") is None


def test_execute_audit_success() -> None:
    reg = JobRegistry()
    job = reg.create("audit")

    async def fake_runner(domain: str, name: str, progress=None) -> dict[str, Any]:
        # exercise the per-stage progress sink the runner is now handed
        if progress is not None:
            progress("discover", {"discovered": 3})
            progress("crawl", {"total": 3})
        return {"run": {"run_id": 42}, "domain": domain, "name": name}

    asyncio.run(execute_audit(reg, job.id, domain="acme.com", name="Acme", runner=fake_runner))
    done = reg.get(job.id)
    assert done is not None
    assert done.status == JOB_SUCCEEDED
    assert done.result == {"run": {"run_id": 42}, "domain": "acme.com", "name": "Acme"}
    assert done.error is None
    # #7: each stage the runner emitted is recorded on the job and visible to the poller
    assert [s["stage"] for s in done.stages] == ["discover", "crawl"]
    assert done.to_dict()["stages"][0]["counts"] == {"discovered": 3}


def test_execute_audit_captures_failure() -> None:
    reg = JobRegistry()
    job = reg.create("audit")

    async def boom(domain: str, name: str, progress=None) -> dict[str, Any]:
        raise RuntimeError("crawl exploded")

    # never raises — the failure is recorded on the job
    asyncio.run(execute_audit(reg, job.id, domain="acme.com", name="Acme", runner=boom))
    done = reg.get(job.id)
    assert done is not None
    assert done.status == JOB_FAILED
    assert done.result is None
    assert "crawl exploded" in (done.error or "")


# ── registry hardening: dedupe / concurrency cap / eviction ─────────────────────


def test_active_for_dedupes_in_flight_by_key() -> None:
    reg = JobRegistry()
    a = reg.create("audit", key="acme.com")
    # a second request for the same domain finds the in-flight job (dedupe)
    assert reg.active_for("audit", "acme.com") is a
    # a different domain has none in flight
    assert reg.active_for("audit", "other.com") is None
    # once it finishes it is no longer "active" → a fresh re-check spawns anew
    reg.update(a.id, status=JOB_SUCCEEDED)
    assert reg.active_for("audit", "acme.com") is None


def test_active_count_tracks_queued_and_running_only() -> None:
    reg = JobRegistry()
    reg.create("audit", key="a.com")
    running = reg.create("audit", key="b.com")
    reg.update(running.id, status="running")
    done = reg.create("audit", key="c.com")
    reg.update(done.id, status=JOB_SUCCEEDED)
    assert reg.active_count("audit") == 2  # queued + running, not the finished one


def test_evict_drops_oldest_finished_over_cap() -> None:
    from aeo.api import jobs as jobs_mod

    reg = JobRegistry()
    # fill past the cap with finished jobs, then create one more to trigger eviction
    for _ in range(jobs_mod._MAX_JOBS + 5):
        j = reg.create("audit")
        reg.update(j.id, status=JOB_SUCCEEDED)
    active = reg.create("audit")  # the create() call evicts oldest finished first
    assert len(reg._jobs) <= jobs_mod._MAX_JOBS
    assert reg.get(active.id) is active  # the just-created job survives


def test_evict_reclaims_cancelled_jobs_too() -> None:
    # main keeps `cancelled` as a flag (status stays succeeded/failed), but a cancel can also
    # be the terminal signal — _evict treats cancelled jobs as droppable so they can't pin slots.
    from aeo.api import jobs as jobs_mod

    reg = JobRegistry()
    for _ in range(jobs_mod._MAX_JOBS + 5):
        j = reg.create("audit")
        reg.update(j.id, status=JOB_SUCCEEDED, cancelled=True)
    active = reg.create("audit")
    assert len(reg._jobs) <= jobs_mod._MAX_JOBS
    assert reg.get(active.id) is active
