"""entitlements — v5 CH-02b data layer (migration 0030). Which packs a user has unlocked
for a domain. Payments are stubbed (§9.2): grants arrive via source='manual'/'promo'.
Binding these to an authenticated user_id is P4; this layer takes an explicit user_id.
"""

from __future__ import annotations

from typing import Any

from ..db import transaction

# scope values that carry a pack_index; every other scope stores NULL (the DB has no
# CHECK enforcing this, so the repo normalizes it — a stray pack_index on all_packs would
# defeat the NULLS-NOT-DISTINCT dedup and create duplicate grants).
_PACK_SCOPE = "pack"
VALID_SCOPES = ("free_overview", "pack", "all_packs", "tickets")


def ensure_user(user_id: str, *, email: str | None = None, session_id: str | None = None) -> None:
    """Upsert the app_users row a grant's FK requires. Idempotent; never overwrites an
    existing user's email/session with NULL."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_users (id, email, session_id) VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                email      = COALESCE(EXCLUDED.email, app_users.email),
                session_id = COALESCE(EXCLUDED.session_id, app_users.session_id)
            """,
            (user_id, email, session_id),
        )


def grant(
    user_id: str,
    domain: str,
    *,
    scope: str,
    pack_index: int | None = None,
    source: str = "manual",
    expires_at: Any = None,
) -> dict[str, Any]:
    """Grant (or refresh) an entitlement. Upserts app_users first (FK), normalizes
    pack_index → NULL for non-'pack' scopes, and dedups on
    (user_id, domain, scope, pack_index) via the NULLS-NOT-DISTINCT unique index."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    if scope != _PACK_SCOPE:
        pack_index = None  # keep the dedup key clean
    elif pack_index is None:
        raise ValueError("scope='pack' requires a pack_index")

    ensure_user(user_id)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entitlements (user_id, domain, scope, pack_index, source, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, domain, scope, pack_index) DO UPDATE SET
                granted_at = NOW(),
                source     = EXCLUDED.source,
                expires_at = EXCLUDED.expires_at
            RETURNING *
            """,
            (user_id, domain, scope, pack_index, source, expires_at),
        )
        return dict(cur.fetchone())


def list_for_user_domain(user_id: str, domain: str) -> list[dict[str, Any]]:
    """Currently-valid (non-expired) entitlement rows for a user + domain."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM entitlements
            WHERE user_id = %s AND domain = %s
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY granted_at
            """,
            (user_id, domain),
        )
        return [dict(row) for row in cur.fetchall()]


def has_access(user_id: str, domain: str, scope: str, pack_index: int | None = None) -> bool:
    """Whether a user currently holds a specific (non-expired) grant — used by P4 read
    routes. ``all_packs`` is checked separately by the caller when relevant."""
    if scope == _PACK_SCOPE and pack_index is None:
        raise ValueError("scope='pack' requires a pack_index")
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM entitlements
                WHERE user_id = %s AND domain = %s AND scope = %s
                  AND (%s::int IS NULL OR pack_index = %s)
                  AND (expires_at IS NULL OR expires_at > NOW())
            ) AS ok
            """,
            (user_id, domain, scope, pack_index, pack_index),
        )
        return bool(cur.fetchone()["ok"])
