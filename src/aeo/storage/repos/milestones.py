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
# Found already live the first time we looked (migration 0033). Real, but not work done
# this session — the plan is generated crawl-free, so it recommends pages a site may
# already have, and crediting those as "you published this" was a lie.
SOURCE_BASELINE = "baseline"


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


def owner_of(client_id: int) -> str | None:
    """The user who owns this client's implementation plan, or None if it was created
    anonymously. Ownership is claimed by the first LOGGED-IN sync (see ``sync_plan``) and
    never transfers; an anonymous plan stays unowned, preserving the signed-out flow.

    Callers use this to gate mutations — the endpoints previously took a bare ``{domain}``
    and the shared service key, so any visitor could mutate any known customer's plan."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT owner_user_id FROM implementation_milestones "
            " WHERE client_id = %s AND milestone_key NOT LIKE 'pack:%%' "
            "   AND owner_user_id IS NOT NULL LIMIT 1",
            (client_id,),
        )
        row = cur.fetchone()
        return str(row["owner_user_id"]) if row else None


def sync_plan(
    client_id: int, specs: list[MilestoneSpec], *, owner_user_id: str | None = None
) -> dict[str, int]:
    """Upsert a plan's milestones + tasks for a client. Idempotent: existing rows keep
    their status / status_source / detection, only their descriptive fields refresh, and
    brand-new tasks are inserted as pending.

    Tasks the new plan no longer contains are PRUNED. Without this a re-plan left every
    superseded recommendation on the dashboard forever, inflating ``progress.total`` and
    telling the owner to build pages the current plan no longer asks for. Only milestones
    present in ``specs`` are touched, so the disjoint v5 ``pack:`` tickets are unaffected.

    Returns {milestones, tasks, pruned} counts touched."""
    milestones = tasks = pruned = 0
    with transaction() as conn, conn.cursor() as cur:
        for spec in specs:
            cur.execute(
                """
                INSERT INTO implementation_milestones
                    (client_id, milestone_key, title, blurb, position, owner_user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, milestone_key) DO UPDATE SET
                    title = EXCLUDED.title, blurb = EXCLUDED.blurb, position = EXCLUDED.position,
                    -- First logged-in sync claims ownership; it never transfers afterwards,
                    -- so a later visitor (anonymous or not) cannot take over the plan.
                    owner_user_id = COALESCE(implementation_milestones.owner_user_id,
                                             EXCLUDED.owner_user_id)
                RETURNING id
                """,
                (client_id, spec.milestone_key, spec.title, spec.blurb, spec.position, owner_user_id),
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
            # Drop tasks this milestone no longer plans. Scoped to the milestone we just
            # synced, so nothing outside this plan can be collected.
            cur.execute(
                "DELETE FROM milestone_tasks "
                " WHERE milestone_id = %s AND NOT (task_key = ANY(%s))",
                (milestone_id, [t.task_key for t in spec.tasks]),
            )
            pruned += cur.rowcount
        _recompute_statuses(cur, client_id)
    return {"milestones": milestones, "tasks": tasks, "pruned": pruned}


def get_dashboard(client_id: int) -> dict[str, Any]:
    """The dashboard payload: ordered milestones (each with its tasks) + a progress roll-up
    (verified / total and a percentage) the UI renders as the headline progress bar."""
    with transaction() as conn, conn.cursor() as cur:
        # Do-no-harm: v5 skill tickets live in 'pack:N' milestones on the same client; the
        # agency dashboard/roll-up must never see them (disjoint product surfaces).
        cur.execute(
            "SELECT * FROM implementation_milestones "
            "WHERE client_id = %s AND milestone_key NOT LIKE 'pack:%%' ORDER BY position, id",
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
    milestone roll-up and returns the updated dashboard, or None if the task isn't found.

    Moving a task OUT of verified pins it (``owner_pinned``), so the next verification run
    can't silently re-flip it — the owner has seen the crawl's verdict and disagreed.
    Manually verifying it again clears the pin: they now agree, so let the crawl resume
    maintaining it."""
    if status not in _STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = %s, status_source = 'manual',
                   detected_run_id = NULL,
                   detected_at = CASE WHEN %s = 'verified_completed' THEN NOW() ELSE NULL END,
                   owner_pinned = CASE
                       WHEN %s = 'verified_completed' THEN FALSE
                       WHEN t.status = 'verified_completed' THEN TRUE
                       ELSE t.owner_pinned
                   END
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s AND t.task_key = %s
            RETURNING t.id
            """,
            (status, status, status, client_id, task_key),
        )
        if cur.fetchone() is None:
            return None
        _recompute_statuses(cur, client_id)
    return get_dashboard(client_id)


def pending_verifiable(client_id: int) -> list[dict[str, Any]]:
    """Tasks the weekly crawl can still try to auto-verify: not yet verified, with an
    on-site signal (verify_kind != 'manual'), and not pinned by the owner.

    ``owner_pinned`` (0033) is the fix for a silent override: a task the owner un-verified
    used to reappear as verified on the very next run, because this query could not tell an
    untouched row from a deliberate reversal — every fresh task also defaults to
    status_source='manual'. The input to milestone_verify.evaluate."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.task_key, t.verify_kind, t.verify_target
              FROM milestone_tasks t
              JOIN implementation_milestones m ON m.id = t.milestone_id
             WHERE m.client_id = %s
               AND m.milestone_key NOT LIKE 'pack:%%'
               AND t.status <> 'verified_completed'
               AND t.verify_kind <> 'manual'
               AND NOT t.owner_pinned
            """,
            (client_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def is_baselined(client_id: int) -> bool:
    """Whether this client's site has already been snapshotted by a verification run.
    False → the next run is the baseline and may not claim credit for what it finds."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT milestones_baselined_at FROM clients WHERE id = %s", (client_id,))
        row = cur.fetchone()
        return bool(row and row["milestones_baselined_at"])


def mark_baselined(client_id: int) -> None:
    """Stamp the baseline moment. Set once and never cleared — after this, any newly
    detected artifact is genuinely new work. Stamped even when the baseline run matched
    nothing, because the site has still been looked at."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE clients SET milestones_baselined_at = NOW() "
            "WHERE id = %s AND milestones_baselined_at IS NULL",
            (client_id,),
        )


def mark_verified(
    client_id: int, task_keys: list[str], run_id: int | None, *, source: str = SOURCE_CRAWL
) -> int:
    """Flip the given tasks to verified_completed, stamp the detecting run + time, and
    recompute milestone status. Returns how many flipped.

    ``source`` is ``'crawl'`` (detected after the baseline — real, credited work) or
    ``'baseline'`` (already live the first time we looked — shown as "already in place"
    and never counted as newly verified).

    Only ever ADVANCES a task to verified (never un-verifies), never overwrites a row
    already verified — so a manual verification isn't relabeled — and never touches a row
    the owner pinned."""
    if not task_keys:
        return 0
    if source not in (SOURCE_CRAWL, SOURCE_BASELINE):
        raise ValueError(f"invalid verification source {source!r}")
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = 'verified_completed', status_source = %s,
                   detected_run_id = %s, detected_at = NOW()
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s
               AND t.task_key = ANY(%s)
               AND t.status <> 'verified_completed'
               AND NOT t.owner_pinned
            """,
            (source, run_id, client_id, task_keys),
        )
        flipped = cur.rowcount
        if flipped:
            _recompute_statuses(cur, client_id)
    return flipped


# ── v5 tickets (CH-08/CH-15) — pack:N milestones, one ticket per (page, skill) ──────
# Reuses the clients→milestones→tasks chain: one milestone_key='pack:<N>' per pack, one
# ticket per (page, skill) from the skill_scores findings. Disjoint from the agency
# 'week_*' milestones (guarded above), so the shipped dashboard is untouched. A pack is
# complete iff every ticket in its milestone is verified_completed (_recompute_statuses).

CLOSED_PENDING = "closed_pending_verify"
_PACK_PREFIX = "pack:"

# Human labels for the five skills (the ticket label + card heading).
_SKILL_LABEL = {
    "messaging": "Messaging",
    "conversion": "Conversion",
    "discovery_visibility": "Discovery & Visibility",
    "proof_trust": "Proof & Trust",
    "structure_ux": "Structure & UX",
}

# UNSET sentinel so set_ticket_fields can distinguish "clear to NULL" from "leave alone".
_UNSET = object()


def _ticket_dict(row: dict) -> dict[str, Any]:
    """The v5 ticket shape (distinct from _task_dict, which the agency dashboard + /share
    depend on)."""
    return {
        "id": row["id"],
        "task_key": row["task_key"],
        "label": row["label"],
        "action_required": row.get("action_required"),
        "how_to": row.get("how_to"),
        "page_url": row.get("page_url"),
        "skill": row.get("skill"),
        "status": row["status"],
        "status_source": row["status_source"],
        "assignee": row.get("assignee"),
        "target_date": row["target_date"].isoformat() if row.get("target_date") else None,
        "baseline_score": row.get("baseline_score"),
        "current_score": row.get("current_score"),
        "closed_at": row["closed_at"].isoformat() if row.get("closed_at") else None,
        "detected_at": row["detected_at"].isoformat() if row.get("detected_at") else None,
        "pack_index": row.get("pack_index"),
    }


def _client_for_run(cur, run_id: int) -> int | None:
    """The clients.id for a run's domain, creating/reusing the row (idempotent). None when
    the run has no resolvable domain (dry-run-only)."""
    from ...storage.repos import runs as runs_repo
    from ...storage.repos import targets as targets_repo

    domain = runs_repo.domain_for_run(run_id)
    if not domain:
        return None
    return targets_repo.upsert(domain, domain, "client").id


def generate_tickets_from_run(run_id: int, *, owner_user_id: str | None = None) -> dict[str, int]:
    """Turn a run's per-page skill findings into tickets: one milestone per pack
    ('pack:<N>'), one ticket per (page, skill) that has findings. Idempotent + regeneration-
    stable (task_key = 'skill:<skill>@<url_normalized>'): owner/verification fields
    (status, assignee, target_date, current_score, closed_at) survive a re-gen, and
    baseline_score is COALESCE-preserved so a re-gen after work starts never destroys the
    before/after delta. Stale tickets (page left the pack) are pruned. Returns counts."""
    from ...storage.repos import packs as packs_repo
    from ...storage.repos import skill_scores as skill_scores_repo
    from ...utils.url import normalize

    packs = 0
    tickets = 0
    with transaction() as conn, conn.cursor() as cur:
        client_id = _client_for_run(cur, run_id)
        if client_id is None:
            return {"tickets": 0, "packs": 0}
        for pack in packs_repo.by_run(run_id):
            pack_index = pack["pack_index"]
            cur.execute(
                """
                INSERT INTO implementation_milestones
                    (client_id, milestone_key, title, blurb, position, owner_user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id, milestone_key) DO UPDATE SET
                    title = EXCLUDED.title, position = EXCLUDED.position,
                    owner_user_id = COALESCE(EXCLUDED.owner_user_id,
                                             implementation_milestones.owner_user_id)
                RETURNING id
                """,
                (client_id, f"{_PACK_PREFIX}{pack_index}", pack["title"], None, pack_index, owner_user_id),
            )
            milestone_id = cur.fetchone()["id"]
            packs += 1

            live_keys: list[str] = []
            for page in skill_scores_repo.detail_for_pack(run_id, pack_index):
                detail = page.get("detail")
                if not detail:  # unscored page → no baseline → skip (a later re-gen creates it)
                    continue
                page_url = page["url"]
                url_norm = normalize(page_url)
                path = _url_path(page_url)
                skills = detail.get("skills") or {}
                for skill in _skills_with_findings(detail):
                    task_key = f"skill:{skill}@{url_norm}"
                    live_keys.append(task_key)
                    suggestions = [s.get("text", "") for s in (skills.get(skill, {}).get("suggestions") or [])]
                    body = " ".join(t for t in suggestions if t) or "Improve this skill on the page."
                    baseline = int(skills.get(skill, {}).get("score", 0))
                    cur.execute(
                        """
                        INSERT INTO milestone_tasks
                            (milestone_id, task_key, label, action_required, how_to,
                             verify_kind, verify_target, position, page_url, skill, baseline_score)
                        VALUES (%s, %s, %s, %s, %s, 'manual', %s, %s, %s, %s, %s)
                        ON CONFLICT (milestone_id, task_key) DO UPDATE SET
                            label = EXCLUDED.label,
                            action_required = EXCLUDED.action_required,
                            how_to = EXCLUDED.how_to,
                            verify_target = EXCLUDED.verify_target,
                            position = EXCLUDED.position,
                            page_url = EXCLUDED.page_url,
                            skill = EXCLUDED.skill,
                            baseline_score = COALESCE(milestone_tasks.baseline_score,
                                                      EXCLUDED.baseline_score)
                            -- status/status_source/assignee/target_date/current_score/
                            -- closed_at/detected_* are owner/verification-owned: preserved.
                        """,
                        (
                            milestone_id, task_key, f"{_SKILL_LABEL.get(skill, skill)} — {path}",
                            body, body, path, len(live_keys), page_url, skill, baseline,
                        ),
                    )
                    tickets += 1
            # Prune-on-regen: drop ONLY phantom pending/in_progress tickets no longer among
            # the current findings (a page left the pack, or a finding was fixed but never
            # marked done). NEVER delete closed_pending_verify or verified_completed tickets
            # — they hold the pinned baseline→current before/after record and the pack's
            # completion signal; deleting a verified ticket would destroy the CH-15 delta and
            # re-lock an earned pack on the next audit. (An empty live_keys correctly clears
            # all remaining phantom pending work while preserving verified history.)
            cur.execute(
                "DELETE FROM milestone_tasks WHERE milestone_id = %s "
                "AND status IN ('pending', 'in_progress') AND NOT (task_key = ANY(%s))",
                (milestone_id, live_keys),
            )
        _recompute_statuses(cur, client_id)
    return {"tickets": tickets, "packs": packs}


def _skills_with_findings(detail: dict) -> list[str]:
    """The skills to make tickets for on a page: those in the impact-ranked priorities,
    falling back to any skill with non-empty suggestions. Deterministic order."""
    priorities = detail.get("priorities") or []
    ordered = list(dict.fromkeys(p.get("skill") for p in priorities if p.get("skill")))
    if ordered:
        return ordered
    skills = detail.get("skills") or {}
    return [s for s, v in skills.items() if (v or {}).get("suggestions")]


def _url_path(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        return parts.path if parts.path and parts.path != "/" else (parts.netloc or url)
    except Exception:
        return url


def list_tickets_for_run(client_id: int, pack_index: int | None = None) -> list[dict[str, Any]]:
    """The v5 tickets for a client's packs (all, or one pack), with their pack_index."""
    where = "m.client_id = %s AND m.milestone_key LIKE 'pack:%%'"
    params: list[Any] = [client_id]
    if pack_index is not None:
        where += " AND m.milestone_key = %s"
        params.append(f"{_PACK_PREFIX}{pack_index}")
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.*, CAST(SUBSTRING(m.milestone_key FROM 6) AS INTEGER) AS pack_index
              FROM milestone_tasks t
              JOIN implementation_milestones m ON m.id = t.milestone_id
             WHERE {where}
             ORDER BY pack_index, t.position, t.id
            """,
            tuple(params),
        )
        return [_ticket_dict(dict(r)) for r in cur.fetchall()]


def set_ticket_fields(
    client_id: int, task_key: str, *, assignee: Any = _UNSET, target_date: Any = _UNSET
) -> dict[str, Any] | None:
    """Set a ticket's assignee and/or target_date (CH-08 async board). UNSET fields are
    left alone; passing None clears. Returns the updated ticket, or None if not found."""
    sets: list[str] = []
    params: list[Any] = []
    if assignee is not _UNSET:
        sets.append("assignee = %s")
        params.append(assignee)
    if target_date is not _UNSET:
        sets.append("target_date = %s")
        params.append(target_date)
    if not sets:
        return get_ticket(client_id, task_key)
    params += [client_id, task_key]
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE milestone_tasks t SET {", ".join(sets)}
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s AND t.task_key = %s
               AND m.milestone_key LIKE 'pack:%%'
            RETURNING t.id
            """,
            tuple(params),
        )
        if cur.fetchone() is None:
            return None
    return get_ticket(client_id, task_key)


def get_ticket(client_id: int, task_key: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.*, CAST(SUBSTRING(m.milestone_key FROM 6) AS INTEGER) AS pack_index
              FROM milestone_tasks t
              JOIN implementation_milestones m ON m.id = t.milestone_id
             WHERE m.client_id = %s AND t.task_key = %s AND m.milestone_key LIKE 'pack:%%'
            """,
            (client_id, task_key),
        )
        row = cur.fetchone()
        return _ticket_dict(dict(row)) if row else None


def close_ticket(client_id: int, task_key: str, *, baseline_score: int | None = None) -> dict[str, Any] | None:
    """Owner marks a ticket done → closed_pending_verify (CH-15). Pins baseline_score (if
    not already pinned) and stamps closed_at (the re-crawl trigger). Only pending/
    in_progress tickets can close. Returns the ticket, or None if not found/closeable."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = 'closed_pending_verify', status_source = 'manual',
                   baseline_score = COALESCE(t.baseline_score, %s),
                   closed_at = NOW()
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s AND t.task_key = %s
               AND m.milestone_key LIKE 'pack:%%'
               AND t.status IN ('pending', 'in_progress')
            RETURNING t.id
            """,
            (baseline_score, client_id, task_key),
        )
        if cur.fetchone() is None:
            return None
        _recompute_statuses(cur, client_id)
    return get_ticket(client_id, task_key)


def reopen_ticket(client_id: int, task_key: str) -> dict[str, Any] | None:
    """Reopen a ticket (closed_pending_verify → in_progress), clearing closed_at."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE milestone_tasks t
               SET status = 'in_progress', status_source = 'manual', closed_at = NULL
              FROM implementation_milestones m
             WHERE t.milestone_id = m.id AND m.client_id = %s AND t.task_key = %s
               AND m.milestone_key LIKE 'pack:%%'
               AND t.status = 'closed_pending_verify'
            RETURNING t.id
            """,
            (client_id, task_key),
        )
        if cur.fetchone() is None:
            return None
        _recompute_statuses(cur, client_id)
    return get_ticket(client_id, task_key)


def verify_tickets_by_recrawl(url_normalized: str, run_id: int, skills: dict[str, int]) -> int:
    """CH-15 completion hook: for tickets the owner closed on this page, record the
    re-scored current_score and — when the lift gate passes (current >= baseline, or the
    gate is relaxed) — flip to verified_completed (status_source='crawl'). Advance-only
    (only touches closed_pending_verify), so repeated crawls are idempotent. Returns the
    number flipped to verified."""
    from ...settings import get_settings
    from ...utils.url import normalize

    require_lift = get_settings().milestones.verify_require_lift
    flipped = 0
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.skill, t.page_url, t.baseline_score, m.client_id
              FROM milestone_tasks t
              JOIN implementation_milestones m ON m.id = t.milestone_id
             WHERE t.status = 'closed_pending_verify' AND t.skill IS NOT NULL
               AND m.milestone_key LIKE 'pack:%%'
            """,
        )
        candidates = [dict(r) for r in cur.fetchall()]
        clients: set[int] = set()
        for row in candidates:
            if normalize(row["page_url"] or "") != url_normalized:
                continue
            current = skills.get(row["skill"])
            if current is None:
                continue
            baseline = row.get("baseline_score")
            proven = (baseline is None) or (current >= baseline) or (not require_lift)
            if proven:
                cur.execute(
                    """
                    UPDATE milestone_tasks
                       SET status = 'verified_completed', status_source = 'crawl',
                           current_score = %s, detected_run_id = %s, detected_at = NOW(),
                           closed_at = COALESCE(closed_at, NOW())
                     WHERE id = %s AND status = 'closed_pending_verify'
                    """,
                    (current, run_id, row["id"]),
                )
                if cur.rowcount:
                    flipped += 1
                    clients.add(row["client_id"])
            else:
                # Record the (regressed) score but leave it closed_pending_verify — the UI
                # shows "not proven yet" + a recheck affordance.
                cur.execute(
                    "UPDATE milestone_tasks SET current_score = %s WHERE id = %s "
                    "AND status = 'closed_pending_verify'",
                    (current, row["id"]),
                )
        for cid in clients:
            _recompute_statuses(cur, cid)
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
