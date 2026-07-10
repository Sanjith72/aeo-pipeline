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
    assert agent_runs.by_idempotency_key("dedupe-key-xyz")["id"] == a["id"]
    assert agent_runs.by_idempotency_key("no-such-key-ever") is None


def test_settled_status_is_immutable() -> None:
    rid = agent_runs.create(brief={"name": "Guard"})["id"]
    assert agent_runs.set_status(rid, "cancelled") is True
    # a worker finishing after the cancel must not resurrect the run
    assert agent_runs.set_status(rid, "staged", result={"tasks": []}) is False
    assert agent_runs.get(rid)["status"] == "cancelled"


def test_staged_can_still_be_decided() -> None:
    rid = agent_runs.create(brief={"name": "Decide"})["id"]
    assert agent_runs.set_status(rid, "staged") is True
    assert agent_runs.set_status(rid, "approved") is True
    assert agent_runs.get(rid)["status"] == "approved"


def test_list_by_status_accepts_a_list_and_count_active_counts() -> None:
    before = agent_runs.count_active()
    rid = agent_runs.create(brief={"name": "Multi"})["id"]
    assert agent_runs.count_active() == before + 1
    assert rid in {r["id"] for r in agent_runs.list_by_status(["queued", "planning"], limit=200)}
    agent_runs.set_status(rid, "cancelled")  # leave the dev DB tidy
    assert agent_runs.count_active() == before


def test_jobs_reap_stale_requeues_then_kills() -> None:
    from aeo.storage.db import transaction
    from aeo.storage.repos import jobs

    keep = jobs.enqueue("agent_run", {"run_id": "reap-keep"}, max_attempts=5)
    dead = jobs.enqueue("agent_run", {"run_id": "reap-dead"}, max_attempts=1)
    # simulate a worker that claimed both and died two hours ago
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='running', attempts=1, locked_by='ghost', "
            "locked_at=NOW() - INTERVAL '2 hours' WHERE id IN (%s, %s)",
            (keep, dead),
        )
    reaped = {r["id"]: r for r in jobs.reap_stale(1800)}
    assert reaped[keep]["status"] == "pending"  # attempts remain → requeued
    assert reaped[dead]["status"] == "dead"     # out of attempts → dead
    with transaction() as conn, conn.cursor() as cur:  # tidy: nothing left claimable
        cur.execute("UPDATE jobs SET status='dead', last_error='test cleanup' WHERE id IN (%s, %s)",
                    (keep, dead))


def test_jobs_cancel_pending_kills_only_the_matching_run() -> None:
    from aeo.storage.repos import jobs

    jobs.enqueue("agent_run", {"run_id": "cancel-me"}, max_attempts=3)
    assert jobs.cancel_pending("agent_run", "cancel-me") == 1
    assert jobs.cancel_pending("agent_run", "cancel-me") == 0  # already dead — nothing pending


def test_set_status_only_from_is_a_compare_and_set() -> None:
    rid = agent_runs.create(brief={"name": "CAS"})["id"]
    # cancel is only valid from queued/planning …
    assert agent_runs.set_status(rid, "cancelled", only_from=("queued", "planning")) is True
    # … and a second writer racing on the same run loses cleanly
    assert agent_runs.set_status(rid, "approved", only_from=("staged",)) is False
    assert agent_runs.get(rid)["status"] == "cancelled"


def test_create_marks_whether_it_inserted() -> None:
    a = agent_runs.create(idempotency_key="inserted-flag-key", brief={"name": "A"})
    b = agent_runs.create(idempotency_key="inserted-flag-key", brief={"name": "A"})
    assert a["_inserted"] is True or a["_inserted"] is False  # first call may replay an old test row
    assert b["_inserted"] is False  # the second call NEVER inserted — must not enqueue
    agent_runs.set_status(a["id"], "cancelled")  # tidy


def test_jobs_succeed_is_fenced_on_the_claiming_worker() -> None:
    from aeo.storage.db import transaction
    from aeo.storage.repos import jobs

    job_id = jobs.enqueue("agent_run", {"run_id": "fence-me"}, max_attempts=3)
    with transaction() as conn, conn.cursor() as cur:  # simulate worker-a holding the claim
        cur.execute("UPDATE jobs SET status='running', locked_by='worker-a', locked_at=NOW() WHERE id=%s",
                    (job_id,))
    assert jobs.succeed(job_id, worker_id="worker-b") is False  # not the claim holder — discarded
    assert jobs.succeed(job_id, worker_id="worker-a") is True   # the holder's write applies
