"""implementation_milestones + milestone_tasks — the persisted, trackable "Final Plan".

``sync_plan`` upserts a generated plan onto a client's milestones idempotently (stable
``task_key``s, so the owner's progress and any crawl-verified status survive a plan
regeneration). ``get_dashboard`` is the read the UI renders; ``set_task_status`` is the
owner's manual toggle; ``mark_verified`` is what the weekly crawl calls. Milestone status
is always *derived* from its tasks (``_recompute_statuses``) — never set directly — so the
progress bar and per-phase status can't drift from the task rows.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from ...report.milestones import MilestoneSpec
from ..db import transaction

PENDING = "pending"
IN_PROGRESS = "in_progress"
VERIFIED = "verified_completed"
_STATUSES = (PENDING, IN_PROGRESS, VERIFIED)

SOURCE_MANUAL = "manual"
SOURCE_CRAWL = "crawl"


def _recompute_statuses(cur, client_id: int) -> None:
    """Re-derive every milestone's status from its tasks (all verified → verified;
    any started → in_progress; else pending). One set-based pass, keyed to the client."""
    cur.execute(
        """
        UPDATE implementation_milestones m
           SET status = sub.new_status
          FROM (
            SELECT mt.milestone_id,
                   CASE
                     WHEN COUNT(*) FILTER (WHERE mt.status = 'verified_completed') = COUNT(*)
                          THEN 'verified_completed'
                     WHEN COUNT(*) FILTER (WHERE mt.status <> 'pending') > 0
                          THEN 'in_progress'
                     ELSE 'pending'
                   END AS new_status
              FROM milestone_tasks mt
             GROUP BY mt.milestone_id
          ) sub
         WHERE m.id = sub.milestone_id
           AND m.client_id = %s
           AND m.status <> sub.new_status
        """,
        (client_id,),
    )


def sync_plan(client_id: int, specs: list[MilestoneSpec]) -> dict[str, int]:
    """Upsert a plan's milestones + tasks for a client. Idempotent: existing rows keep
    their status / status_source / detection, only their descriptive fields refresh, and
    brand-new tasks are inserted as pending. Returns {milestones, tasks} counts touched."""
    milestones = tasks = 0
    with transaction() as conn, conn.cursor() as cur:
        for spec in specs:
            cur.execute(
                """
                INSERT INTO implementation_milestones
                    (client_id, milestone_key, title, blurb, position)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (client_id, milestone_key) DO UPDATE SET
                    title = EXCLUDED.title, blurb = EXCLUDED.blurb, position = EXCLUDED.position
                RETURNING id
                """,
                (client_id, spec.milestone_key, spec.title, spec.blurb, spec.position),
            )
            milestone_id = cur.fetchone()["id"]
            milestones += 1
            for t in spec.tasks:
                cur.execute(
                    """
                    INSERT INTO milestone_tasks
                        (milestone_id, task_key, label, action_required, how_to,
                         verify_kind, verify_target, position, current_state, prompts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (milestone_id, task_key) DO UPDATE SET
                        label = EXCLUDED.label,
                        action_required = EXCLUDED.action_required,
                        how_to = EXCLUDED.how_to,
                        verify_kind = EXCLUDED.verify_kind,
                        verify_target = EXCLUDED.verify_target,
                        position = EXCLUDED.position,
                        current_state = EXCLUDED.current_state,
                        prompts = EXCLUDED.prompts
                        -- status / status_source / detection are deliberately preserved.
                    """,
                    (
                        milestone_id, t.task_key, t.label, t.action_required, t.how_to,
                        t.verify_kind, t.verify_target, t.position,
                        t.current_state or None,
                        json.dumps(t.prompts) if t.prompts is not None else None,
                    ),
                )
                tasks += 1
        _recompute_statuses(cur, client_id)
    return {"milestones": milestones, "tasks": tasks}


def get_dashboard(client_id: int) -> dict[str, Any]:
    """The dashboard payload: ordered milestones (each with its tasks) + a progress roll-up
    (verified / total and a percentage) the UI renders as the headline progress bar."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM implementation_milestones WHERE client_id = %s ORDER BY position, id",
            (client_id,),
        )
        milestone_rows = [dict(r) for r in cur.fetchall()]
        ids = [m["id"] for m in milestone_rows]
        tasks_by_milestone: dict[int, list[dict]] = {i: [] for i in ids}
        if ids:
            cur.execute(
                "SELECT * FROM milestone_tasks WHERE milestone_id = ANY(%s) ORDER BY position, id",
                (ids,),
            )
            for row in cur.fetchall():
                tasks_by_milestone[row["milestone_id"]].append(_task_dict(row))

    milestones = []
    total = verified = in_progress = 0
    for m in milestone_rows:
        m_tasks = tasks_by_milestone.get(m["id"], [])
        total += len(m_tasks)
        verified += sum(1 for t in m_tasks if t["status"] == VERIFIED)
        in_progress += sum(1 for t in m_tasks if t["status"] == IN_PROGRESS)
        milestones.append({
            "milestone_key": m["milestone_key"],
            "title": m["title"],
            "blurb": m["blurb"],
            "status": m["status"],
            "position": m["position"],
            "tasks": m_tasks,
        })
    pct = round((verified / total) * 100) if total else 0
    return {
        "milestones": milestones,
        "progress": {"total": total, "verified": verified, "in_progress": in_progress, "pct": pct},
    }


def _task_dict(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_key": row["task_key"],
        "label": row["label"],
        "action_required": row["action_required"],
        "how_to": row["how_to"],
        "verify_kind": row["verify_kind"],
        "verify_target": row["verify_target"],
        "status": row["status"],
        "status_source": row["status_source"],
        "detected_at": row["detected_at"].isoformat() if row.get("detected_at") else None,
        # 0024 — carried from build_plan so the shared TaskHowTo expander can render the
        # "Where you are now" context and the "Doing it with AI" prompt on milestone tasks
        # too. JSONB reads back as a dict via psycopg2's default typecaster (no json.loads).
        "current_state": row.get("current_state"),
        "prompts": row.get("prompts"),
    }


def set_task_status(client_id: int, task_key: str, status: str) -> dict[str, Any] | None:
    """Owner's manual status toggle for one task (status_source='manual'). Recomputes the
    milestone roll-up and returns the updated dashboard, or None if the task isn't found."""
    if status not in _STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = %s, status_source = 'manual',
                   detected_run_id = NULL,
                   detected_at = CASE WHEN %s = 'verified_completed' THEN NOW() ELSE NULL END
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s AND t.task_key = %s
            RETURNING t.id
            """,
            (status, status, client_id, task_key),
        )
        if cur.fetchone() is None:
            return None
        _recompute_statuses(cur, client_id)
    return get_dashboard(client_id)


def pending_verifiable(client_id: int) -> list[dict[str, Any]]:
    """Tasks the weekly crawl can still try to auto-verify: not yet verified, and with an
    on-site signal (verify_kind != 'manual'). The input to milestone_verify.evaluate."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.task_key, t.verify_kind, t.verify_target
              FROM milestone_tasks t
              JOIN implementation_milestones m ON m.id = t.milestone_id
             WHERE m.client_id = %s
               AND t.status <> 'verified_completed'
               AND t.verify_kind <> 'manual'
            """,
            (client_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def mark_verified(client_id: int, task_keys: list[str], run_id: int | None) -> int:
    """Flip the given tasks to verified_completed (status_source='crawl'), stamp the
    detecting run + time, and recompute milestone status. Returns how many flipped.

    Only ever ADVANCES a task to verified (never un-verifies), and never overwrites a row
    already verified — so a manual verification isn't relabeled 'crawl'."""
    if not task_keys:
        return 0
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = 'verified_completed', status_source = 'crawl',
                   detected_run_id = %s, detected_at = NOW()
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s
               AND t.task_key = ANY(%s)
               AND t.status <> 'verified_completed'
            """,
            (run_id, client_id, task_keys),
        )
        flipped = cur.rowcount
        if flipped:
            _recompute_statuses(cur, client_id)
    return flipped


def has_milestones(client_id: int) -> bool:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM implementation_milestones WHERE client_id = %s LIMIT 1", (client_id,)
        )
        return cur.fetchone() is not None


# ── developer-handoff share links ────────────────────────────────────────────


def _new_token() -> str:
    """A fresh, URL-safe, unguessable share token (~192 bits)."""
    return secrets.token_urlsafe(24)


def ensure_share_token(client_id: int) -> str:
    """The client's current ACTIVE read-only share token, minting one on first request.

    Idempotent and non-rotating: the no-op ``DO UPDATE`` lets the existing active row's
    token be RETURNED on conflict, so re-requesting a link never invalidates one already
    shared. The conflict target is the partial unique index over active rows (migration
    0014), so revoked rows don't collide here. To intentionally kill + reissue a link use
    :func:`rotate_share_token`."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plan_shares (client_id, share_token)
            VALUES (%s, %s)
            ON CONFLICT (client_id) WHERE revoked_at IS NULL
                DO UPDATE SET client_id = EXCLUDED.client_id
            RETURNING share_token
            """,
            (client_id, _new_token()),
        )
        return cur.fetchone()["share_token"]


def rotate_share_token(client_id: int) -> str:
    """Revoke the client's current active link and immediately issue a fresh one.

    Atomic (one transaction): the active row's ``revoked_at`` is stamped — so the old
    ``/share/<token>`` link stops resolving (``client_for_token`` filters on
    ``revoked_at IS NULL``) — and a new active row is inserted. The revoked row is kept as
    an audit trail. Returns the new token. Safe to call with no existing link (the revoke
    affects 0 rows, the insert mints the first one)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE plan_shares SET revoked_at = NOW() "
            "WHERE client_id = %s AND revoked_at IS NULL",
            (client_id,),
        )
        cur.execute(
            "INSERT INTO plan_shares (client_id, share_token) VALUES (%s, %s) RETURNING share_token",
            (client_id, _new_token()),
        )
        return cur.fetchone()["share_token"]


def client_for_token(token: str) -> dict[str, Any] | None:
    """Resolve an active share token to its client {id, name, domain, cms_type}, or None if
    the token is unknown or revoked. This is the read-only view's only entry point — no auth,
    the token IS the credential."""
    if not token:
        return None
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.domain, c.cms_type
              FROM plan_shares s
              JOIN clients c ON c.id = s.client_id
             WHERE s.share_token = %s AND s.revoked_at IS NULL
            """,
            (token,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
