"""Live-DB guard: v5 P5 pack-ticket ownership, end to end against real SQL.

The offline suite (tests/unit/test_tickets.py) proves the ROUTES enforce ownership and that
the claim fires on the right paths, with every repo stubbed. It cannot prove the part that
actually failed in production: that ``owner_user_id`` ends up in the table at all, and that
the claim's concurrency guard behaves. Those are properties of the SQL, so they need SQL.

The bug this file pins down: boards are created by the pipeline
(``orchestrator._generate_tickets``), which passed no owner. The only stamping site was
inside ``GET /api/tickets/{run_id}`` on LAZY generation — a branch that runs only when the
board does not exist yet, which is never true for a board the pipeline just made. So
``pack_owner_of()`` returned None for every real board, ``_require_ticket_owner`` took its
"unowned → open" branch, and any logged-in user holding any entitlement on the domain could
close another user's tickets. Closing spends crawl budget and drives progressive unlock,
which opens PAID packs.
"""

from __future__ import annotations

import uuid

import pytest

from aeo.storage.db import health_check, transaction
from aeo.storage.repos import milestones as m

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")

DOMAIN = "ticket-ownership.example"


def _client_id() -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (name, domain, website_url) VALUES (%s, %s, %s) "
            "ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            ("Ticket Ownership Test", DOMAIN, f"https://{DOMAIN}/"),
        )
        return int(cur.fetchone()["id"])


def _user(email: str) -> str:
    """A real app_users row — owner_user_id is an FK, so a fabricated uuid would not stick."""
    uid = str(uuid.uuid4())
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (uid, email),
        )
    return uid


def _seed_board(client_id: int, *, packs: tuple[int, ...] = (1, 2), owner: str | None = None) -> None:
    """A pack board exactly as generate_tickets_from_run leaves it: one 'pack:N' milestone
    per pack, each with one pending ticket."""
    with transaction() as conn, conn.cursor() as cur:
        for n in packs:
            cur.execute(
                "INSERT INTO implementation_milestones "
                "  (client_id, milestone_key, title, blurb, position, owner_user_id) "
                "VALUES (%s, %s, %s, '', %s, %s) "
                "ON CONFLICT (client_id, milestone_key) DO UPDATE SET owner_user_id = %s "
                "RETURNING id",
                (client_id, f"pack:{n}", f"Pack {n}", n, owner, owner),
            )
            milestone_id = int(cur.fetchone()["id"])
            cur.execute(
                "INSERT INTO milestone_tasks (milestone_id, task_key, label, position, status) "
                "VALUES (%s, %s, 'Fix messaging', 1, 'pending') "
                "ON CONFLICT (milestone_id, task_key) DO UPDATE SET status = 'pending'",
                (milestone_id, f"skill:messaging@https://{DOMAIN}/p{n}"),
            )


def _owners(client_id: int) -> list[str | None]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT owner_user_id FROM implementation_milestones "
            " WHERE client_id = %s AND milestone_key LIKE 'pack:%%' ORDER BY position",
            (client_id,),
        )
        return [None if r["owner_user_id"] is None else str(r["owner_user_id"])
                for r in cur.fetchall()]


def _cleanup(client_id: int, user_ids: tuple[str, ...] = ()) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM milestone_tasks WHERE milestone_id IN "
            "(SELECT id FROM implementation_milestones WHERE client_id = %s)",
            (client_id,),
        )
        cur.execute("DELETE FROM implementation_milestones WHERE client_id = %s", (client_id,))
        cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
        for uid in user_ids:
            cur.execute("DELETE FROM entitlements WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM app_users WHERE id = %s", (uid,))


# ── claim_pack_owner ──────────────────────────────────────────────────────────────


def test_claim_stamps_every_pack_row_of_an_unowned_board() -> None:
    client_id = _client_id()
    user_a = _user("a@example.com")
    try:
        _seed_board(client_id, packs=(1, 2, 3))
        assert _owners(client_id) == [None, None, None]
        assert m.claim_pack_owner(client_id, user_a) == user_a
        assert _owners(client_id) == [user_a, user_a, user_a]
        assert m.pack_owner_of(client_id) == user_a
    finally:
        _cleanup(client_id, (user_a,))


def test_claim_never_displaces_an_existing_owner() -> None:
    """Ownership does not transfer. Pack 1 is readable by everyone, so if a second reader
    could re-claim, any stranger would take a paying customer's board with a single GET."""
    client_id = _client_id()
    user_a, user_b = _user("a@example.com"), _user("b@example.com")
    try:
        _seed_board(client_id, owner=user_a)
        assert m.claim_pack_owner(client_id, user_b) == user_a
        assert _owners(client_id) == [user_a, user_a]
    finally:
        _cleanup(client_id, (user_a, user_b))


def test_a_partly_owned_board_is_left_alone() -> None:
    """The NOT EXISTS guard. A re-gen can add a 'pack:N' row after the board was claimed,
    leaving one row NULL. Without the guard the next reader stamps THAT row with their own
    id, splitting one board across two owners — and pack_owner_of() returns whichever row
    it finds first, so the gate would then admit or refuse the true owner at random."""
    client_id = _client_id()
    user_a, user_b = _user("a@example.com"), _user("b@example.com")
    try:
        _seed_board(client_id, packs=(1,), owner=user_a)
        _seed_board(client_id, packs=(2,))  # a later re-gen: unowned
        assert sorted(_owners(client_id), key=str) == sorted([user_a, None], key=str)
        assert m.claim_pack_owner(client_id, user_b) == user_a
        assert user_b not in _owners(client_id), "a partial board must never be co-claimed"
    finally:
        _cleanup(client_id, (user_a, user_b))


def test_claim_is_idempotent() -> None:
    client_id = _client_id()
    user_a = _user("a@example.com")
    try:
        _seed_board(client_id)
        assert m.claim_pack_owner(client_id, user_a) == user_a
        assert m.claim_pack_owner(client_id, user_a) == user_a
        assert _owners(client_id) == [user_a, user_a]
    finally:
        _cleanup(client_id, (user_a,))


# ── the gate itself, with two distinct users ──────────────────────────────────────


def test_user_b_cannot_mutate_user_as_board() -> None:
    """The headline regression. Both users hold a live entitlement on the SAME domain — the
    exact situation the old code waved through, because an entitlement was all it took to
    pass ``_require_unlocked_pack`` and ``_require_ticket_owner`` never had an owner to
    compare against."""
    from fastapi import HTTPException

    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a, user_b = _user("a@example.com"), _user("b@example.com")
    try:
        _seed_board(client_id, owner=user_a)
        ent_repo.grant(user_a, DOMAIN, scope="pack", pack_index=2, source="manual")
        ent_repo.grant(user_b, DOMAIN, scope="pack", pack_index=2, source="manual")

        class _U:
            def __init__(self, uid): self.id = uid

        # The owner passes.
        app_mod._require_ticket_owner(_U(user_a), 0, client_id)
        # A different entitled user on the same domain does not.
        with pytest.raises(HTTPException) as exc:
            app_mod._require_ticket_owner(_U(user_b), 0, client_id)
        assert exc.value.status_code == 403
        # Neither does an anonymous caller.
        with pytest.raises(HTTPException):
            app_mod._require_ticket_owner(None, 0, client_id)
    finally:
        _cleanup(client_id, (user_a, user_b))


def test_all_packs_holder_keeps_the_agency_override() -> None:
    """§9.2's explicit override must survive the tightening — blocking it here would defeat
    the whole point of the agency/advanced scope."""
    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a, agency = _user("a@example.com"), _user("agency@example.com")
    try:
        _seed_board(client_id, owner=user_a)
        ent_repo.grant(agency, DOMAIN, scope="all_packs", source="manual")

        class _U:
            def __init__(self, uid): self.id = uid

        app_mod._require_ticket_owner(_U(agency), 0, client_id)  # must not raise
    finally:
        _cleanup(client_id, (user_a, agency))


def test_an_anonymous_board_stays_open() -> None:
    """The free signed-out tier depends on this and must not regress."""
    from aeo.api import app as app_mod

    client_id = _client_id()
    try:
        _seed_board(client_id)  # no owner
        app_mod._require_ticket_owner(None, 0, client_id)  # must not raise
    finally:
        _cleanup(client_id)


def test_a_signed_in_mutation_claims_an_unowned_board() -> None:
    """Ownership is settled on first authenticated touch, so the board does not stay open
    forever. Before this, an unowned board was open to every logged-in user for its whole
    life — which was every board in production."""
    from aeo.api import app as app_mod

    client_id = _client_id()
    user_a = _user("a@example.com")
    try:
        _seed_board(client_id)

        class _U:
            def __init__(self, uid): self.id = uid

        app_mod._require_ticket_owner(_U(user_a), 0, client_id)
        assert m.pack_owner_of(client_id) == user_a, "the first authenticated touch must claim"
    finally:
        _cleanup(client_id, (user_a,))


# ── migration 0034: the backfill rule ─────────────────────────────────────────────


def _run_backfill() -> None:
    from pathlib import Path

    import aeo.storage.migrate as migrate_mod

    sql = (migrate_mod.MIGRATIONS_DIR / "0034_backfill_pack_owner.sql").read_text(encoding="utf-8")
    assert Path(migrate_mod.MIGRATIONS_DIR / "0034_backfill_pack_owner.sql").exists()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(sql)


def test_backfill_claims_a_board_with_exactly_one_entitled_user() -> None:
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a = _user("a@example.com")
    try:
        _seed_board(client_id)
        ent_repo.grant(user_a, DOMAIN, scope="pack", pack_index=2, source="manual")
        _run_backfill()
        assert _owners(client_id) == [user_a, user_a]
    finally:
        _cleanup(client_id, (user_a,))


def test_backfill_leaves_an_ambiguous_board_unowned() -> None:
    """Two entitled users (an agency seat, a shared domain, a refund-and-repurchase) is not
    evidence of ownership. Guessing wrong locks the real owner out of their own board, which
    is worse than leaving it as it is."""
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a, user_b = _user("a@example.com"), _user("b@example.com")
    try:
        _seed_board(client_id)
        ent_repo.grant(user_a, DOMAIN, scope="pack", pack_index=2, source="manual")
        ent_repo.grant(user_b, DOMAIN, scope="pack", pack_index=3, source="manual")
        _run_backfill()
        assert _owners(client_id) == [None, None]
    finally:
        _cleanup(client_id, (user_a, user_b))


def test_backfill_ignores_free_overview_grants() -> None:
    """'free_overview' marks the anonymous tier, not ownership of the deep-value board."""
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a = _user("a@example.com")
    try:
        _seed_board(client_id)
        ent_repo.grant(user_a, DOMAIN, scope="free_overview", source="manual")
        _run_backfill()
        assert _owners(client_id) == [None, None]
    finally:
        _cleanup(client_id, (user_a,))


def test_backfill_never_overwrites_an_existing_owner() -> None:
    from aeo.storage.repos import entitlements as ent_repo

    client_id = _client_id()
    user_a, user_b = _user("a@example.com"), _user("b@example.com")
    try:
        _seed_board(client_id, owner=user_a)
        ent_repo.grant(user_b, DOMAIN, scope="all_packs", source="manual")
        _run_backfill()
        assert _owners(client_id) == [user_a, user_a]
    finally:
        _cleanup(client_id, (user_a, user_b))
