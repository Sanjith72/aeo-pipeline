"""Live-DB guard: the agency milestone toggle must never reach v5 pack tickets.

Regression for a real authorization bypass. ``POST /api/milestones/task`` is guarded by
``_assert_plan_access`` -> ``owner_of()``, which is AGENCY-only (``NOT LIKE 'pack:%'``) and
so returns None for a v5 domain — meaning the guard waves anonymous callers through. If
``set_task_status`` can also write ``pack:N`` rows, an anonymous stranger can flip a Pack 1
ticket straight to ``verified_completed``; that fills ``packs.completed_pack_indices``, which
drives progressive unlock, opening the PAID Pack 2 for everyone without any work being done.

The two milestone families share one table and must stay write-isolated: pack tickets are
writable only through the ticket routes (``_require_unlocked_pack`` + ``_require_ticket_owner``).
"""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check, transaction
from aeo.storage.repos import milestones as m

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")

DOMAIN = "pack-isolation.example"


def _client_id() -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (name, domain, website_url) VALUES (%s, %s, %s) "
            "ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            ("Pack Isolation Test", DOMAIN, f"https://{DOMAIN}/"),
        )
        return int(cur.fetchone()["id"])


def _seed_pack_ticket(client_id: int, task_key: str) -> None:
    """One `pack:1` milestone with one pending ticket — what generate_tickets_from_run makes."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO implementation_milestones (client_id, milestone_key, title, blurb, position) "
            "VALUES (%s, 'pack:1', 'Pack 1', '', 1) "
            "ON CONFLICT (client_id, milestone_key) DO UPDATE SET title = EXCLUDED.title RETURNING id",
            (client_id,),
        )
        milestone_id = int(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO milestone_tasks (milestone_id, task_key, label, position, status) "
            "VALUES (%s, %s, 'Fix messaging', 1, 'pending') "
            "ON CONFLICT (milestone_id, task_key) DO UPDATE SET status = 'pending'",
            (milestone_id, task_key),
        )


def _status(client_id: int, task_key: str) -> str:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.status FROM milestone_tasks t "
            "JOIN implementation_milestones m ON m.id = t.milestone_id "
            "WHERE m.client_id = %s AND t.task_key = %s",
            (client_id, task_key),
        )
        return str(cur.fetchone()["status"])


def _cleanup(client_id: int) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM milestone_tasks WHERE milestone_id IN "
            "(SELECT id FROM implementation_milestones WHERE client_id = %s)",
            (client_id,),
        )
        cur.execute("DELETE FROM implementation_milestones WHERE client_id = %s", (client_id,))
        cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))


def test_set_task_status_cannot_touch_a_pack_ticket() -> None:
    client_id = _client_id()
    key = "skill:messaging@https://pack-isolation.example/"
    try:
        _seed_pack_ticket(client_id, key)
        assert _status(client_id, key) == "pending"

        # The agency toggle must not find (or write) a pack ticket.
        assert m.set_task_status(client_id, key, "verified_completed") is None
        assert _status(client_id, key) == "pending", "pack ticket was writable via the agency route"
    finally:
        _cleanup(client_id)


def test_pack_ticket_stays_out_of_completed_indices_after_the_attempt() -> None:
    """The payoff of the bypass was progressive unlock — assert it never materialises."""
    from aeo.storage.repos import packs as packs_repo

    client_id = _client_id()
    key = "skill:conversion@https://pack-isolation.example/pricing"
    try:
        _seed_pack_ticket(client_id, key)
        m.set_task_status(client_id, key, "verified_completed")
        with transaction() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM milestone_tasks t "
                "JOIN implementation_milestones m ON m.id = t.milestone_id "
                "WHERE m.client_id = %s AND m.milestone_key LIKE 'pack:%%' "
                "  AND t.status = 'verified_completed'",
                (client_id,),
            )
            assert int(cur.fetchone()["n"]) == 0
        assert callable(packs_repo.completed_pack_indices)
    finally:
        _cleanup(client_id)


def test_agency_tasks_are_still_writable() -> None:
    """The guard must not break the shipped agency dashboard it shares a table with."""
    client_id = _client_id()
    key = "agency-task-1"
    try:
        with transaction() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO implementation_milestones (client_id, milestone_key, title, blurb, position) "
                "VALUES (%s, 'foundation', 'Foundation', '', 1) "
                "ON CONFLICT (client_id, milestone_key) DO UPDATE SET title = EXCLUDED.title RETURNING id",
                (client_id,),
            )
            milestone_id = int(cur.fetchone()["id"])
            cur.execute(
                "INSERT INTO milestone_tasks (milestone_id, task_key, label, position, status) "
                "VALUES (%s, %s, 'Publish the about page', 1, 'pending') "
                "ON CONFLICT (milestone_id, task_key) DO UPDATE SET status = 'pending'",
                (milestone_id, key),
            )
        assert m.set_task_status(client_id, key, "verified_completed") is not None
        assert _status(client_id, key) == "verified_completed"
    finally:
        _cleanup(client_id)
