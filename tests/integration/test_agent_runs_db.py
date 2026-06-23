"""Live-DB round-trip for the agent_runs repo. Skips cleanly when no DB is reachable."""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check
from aeo.storage.repos import agent_runs

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")


def test_create_step_status_round_trip() -> None:
    row = agent_runs.create(domain="acme.example", brief={"name": "Acme", "domain": "acme.example"})
    rid = row["id"]
    assert row["status"] == "queued"

    agent_runs.set_status(rid, "planning", current_step="plan")
    step_id = agent_runs.append_step(rid, seq=1, agent="planner", tool="plan_from_brief",
                                     detail={"task_count": 3})
    assert step_id > 0

    agent_runs.set_status(rid, "staged", current_step="review", result={"tasks": [1, 2, 3]})
    fetched = agent_runs.get(rid)
    assert fetched["status"] == "staged"
    assert fetched["result"] == {"tasks": [1, 2, 3]}

    steps = agent_runs.steps_for(rid)
    assert [s["agent"] for s in steps] == ["planner"]
    assert any(r["id"] == rid for r in agent_runs.list_by_status("staged"))


def test_idempotency_key_dedupes() -> None:
    a = agent_runs.create(idempotency_key="dedupe-key-xyz", brief={"name": "A"})
    b = agent_runs.create(idempotency_key="dedupe-key-xyz", brief={"name": "A"})
    assert a["id"] == b["id"]  # same run returned, not a second row
