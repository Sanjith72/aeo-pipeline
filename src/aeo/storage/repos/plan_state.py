"""plan_states — a server home for the interactive plan (B1, retention foundation).

The plan used to live only in React state + the browser's localStorage, so progress was
device-locked, lost on a cache clear, and invisible to the return-visit metric. A
``plan_state`` is the durable, resumable artifact behind the ``/plan/<id>`` link: it
stores the rendered plan, a profile snapshot, the canonical score at issue, and the set
of completed task ids.

Identity is an unguessable minted token (:func:`new_id`), so the link works for the
no-website path too (which has no ``run_id``) and is safe to share. The optional
``session_id`` powers same-browser auto-resume via :func:`latest_for_session`.

Like the other repos, every function only touches the DB at call time (via
``transaction()``), so importing this module needs no connection.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from ..db import transaction

# ~12 url-safe chars — unguessable, still short enough to bookmark or read aloud.
_TOKEN_BYTES = 9


def new_id() -> str:
    """A fresh, unguessable plan-state id for the ``/plan/<id>`` URL."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def create(
    *,
    plan: dict[str, Any],
    profile: dict[str, Any] | None,
    session_id: str | None,
    run_id: int | None,
    business_name: str | None,
    domain: str | None,
    score_snapshot: int | None,
    done_task_ids: list[str] | None = None,
) -> str:
    """Persist a new plan state and return its minted id."""
    pid = new_id()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plan_states (
                id, session_id, run_id, business_name, domain,
                plan, profile, score_snapshot, done_task_ids
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s, %s::jsonb
            )
            """,
            (
                pid, session_id, run_id, business_name, domain,
                json.dumps(plan, default=str),
                json.dumps(profile, default=str) if profile is not None else None,
                score_snapshot,
                json.dumps(done_task_ids or [], default=str),
            ),
        )
    return pid


def get(plan_id: str) -> dict[str, Any] | None:
    """The full plan state behind a link, or ``None`` if the id is unknown."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM plan_states WHERE id = %s", (plan_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_progress(
    plan_id: str,
    done_task_ids: list[str],
    score_snapshot: int | None = None,
) -> bool:
    """Save the completed-task set (and optionally a refreshed score). Returns whether a
    row matched. ``score_snapshot`` is written only when provided, so a progress save
    never clobbers the issue-time score with ``NULL``. ``updated_at`` is bumped by the
    table trigger, which keeps :func:`latest_for_session` ordering correct."""
    sets = ["done_task_ids = %s::jsonb"]
    params: list[Any] = [json.dumps(done_task_ids, default=str)]
    if score_snapshot is not None:
        sets.append("score_snapshot = %s")
        params.append(score_snapshot)
    params.append(plan_id)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE plan_states SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return cur.rowcount > 0


def latest_for_session(session_id: str) -> dict[str, Any] | None:
    """The most recently updated plan for a returning session (auto-resume), or ``None``."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM plan_states WHERE session_id = %s ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
