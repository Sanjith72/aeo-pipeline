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

    async def fake_runner(domain: str, name: str) -> dict[str, Any]:
        return {"run": {"run_id": 42}, "domain": domain, "name": name}

    asyncio.run(execute_audit(reg, job.id, domain="acme.com", name="Acme", runner=fake_runner))
    done = reg.get(job.id)
    assert done is not None
    assert done.status == JOB_SUCCEEDED
    assert done.result == {"run": {"run_id": 42}, "domain": "acme.com", "name": "Acme"}
    assert done.error is None


def test_execute_audit_captures_failure() -> None:
    reg = JobRegistry()
    job = reg.create("audit")

    async def boom(domain: str, name: str) -> dict[str, Any]:
        raise RuntimeError("crawl exploded")

    # never raises — the failure is recorded on the job
    asyncio.run(execute_audit(reg, job.id, domain="acme.com", name="Acme", runner=boom))
    done = reg.get(job.id)
    assert done is not None
    assert done.status == JOB_FAILED
    assert done.result is None
    assert "crawl exploded" in (done.error or "")
