"""Live-DB guard: re-running an audit must not delete the customer's open fixes.

The bug. ``generate_tickets_from_run`` prunes "phantom" pending tickets — ones no longer
among the current findings — by testing ``task_key NOT IN live_keys``. ``live_keys`` is built
only from pages that came back with skill DETAIL. But re-auditing an UNCHANGED site
fingerprint-skips its pages, so they return with ``detail = None``, contribute nothing to
``live_keys``, and every open ticket on them looked like a phantom and was deleted.

Observed on a real re-run before the fix: run N scored the homepage and produced three
tickets; run N+1 skipped it (unchanged) and all three pending tickets vanished. A customer
who re-ran their audit lost the work they had not finished yet — and the weekly audit does
this on its own.

The fix makes absence evidence only when the page was actually re-scored. These tests pin
all four cases, because the naive fix (never prune) would leave stale work on the board
forever, which is the failure the prune existed to prevent.
"""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check, transaction
from aeo.storage.repos import milestones as m

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")

DOMAIN = "ticket-prune.example"
PAGE = f"https://{DOMAIN}/"
OTHER_PAGE = f"https://{DOMAIN}/pricing"


def _client_id() -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (name, domain, website_url) VALUES (%s, %s, %s) "
            "ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            ("Ticket Prune Test", DOMAIN, PAGE),
        )
        return int(cur.fetchone()["id"])


def _milestone_id(client_id: int) -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO implementation_milestones (client_id, milestone_key, title, blurb, position) "
            "VALUES (%s, 'pack:1', 'Pack 1', '', 1) "
            "ON CONFLICT (client_id, milestone_key) DO UPDATE SET title = EXCLUDED.title RETURNING id",
            (client_id,),
        )
        return int(cur.fetchone()["id"])


def _seed_ticket(milestone_id: int, task_key: str, page_url: str | None, status: str = "pending") -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO milestone_tasks (milestone_id, task_key, label, position, status, page_url, skill) "
            "VALUES (%s, %s, 'Fix a thing', 1, %s, %s, 'messaging') "
            "ON CONFLICT (milestone_id, task_key) DO UPDATE SET status = EXCLUDED.status, "
            "  page_url = EXCLUDED.page_url",
            (milestone_id, task_key, status, page_url),
        )


def _keys(milestone_id: int) -> set[str]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT task_key FROM milestone_tasks WHERE milestone_id = %s", (milestone_id,))
        return {r["task_key"] for r in cur.fetchall()}


def _prune(milestone_id: int, *, live_keys: list[str], scored: list[str], in_pack: list[str]) -> None:
    """Run the exact prune statement generate_tickets_from_run issues.

    Executed directly rather than through generate_tickets_from_run because reaching that
    function's prune requires a full run + packs + skill_scores fixture; the statement IS the
    behaviour under test, and driving it directly lets each case be stated in one line."""
    if not scored:
        return  # the guard itself: a run that re-scored nothing learned nothing
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM milestone_tasks WHERE milestone_id = %s "
            "AND status IN ('pending', 'in_progress') "
            "AND NOT (task_key = ANY(%s)) "
            "AND (page_url = ANY(%s) OR NOT (page_url = ANY(%s)))",
            (milestone_id, live_keys, scored, in_pack),
        )


def _cleanup(client_id: int) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM milestone_tasks WHERE milestone_id IN "
            "(SELECT id FROM implementation_milestones WHERE client_id = %s)",
            (client_id,),
        )
        cur.execute("DELETE FROM implementation_milestones WHERE client_id = %s", (client_id,))
        cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))


def test_an_unchanged_re_audit_keeps_open_fixes() -> None:
    """THE REGRESSION. The page is still in the pack but was not re-scored (fingerprint
    skip), so it contributes no live_keys. Its open tickets must survive."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:messaging@x", PAGE)
        _seed_ticket(mid, "skill:conversion@x", PAGE)
        # A run where the page is in the pack, another page WAS scored, but this page was not.
        _prune(mid, live_keys=["skill:other@y"], scored=[OTHER_PAGE], in_pack=[PAGE, OTHER_PAGE])
        assert _keys(mid) == {"skill:messaging@x", "skill:conversion@x"}, (
            "a page that was not re-scored must keep its open fixes"
        )
    finally:
        _cleanup(client_id)


def test_a_run_that_scored_nothing_prunes_nothing() -> None:
    """The whole-pack version of the same thing: every page skipped. The run learned
    nothing, so it may not conclude anything."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:messaging@x", PAGE)
        _prune(mid, live_keys=[], scored=[], in_pack=[PAGE])
        assert _keys(mid) == {"skill:messaging@x"}
    finally:
        _cleanup(client_id)


def test_a_finding_that_is_genuinely_gone_is_still_pruned() -> None:
    """The prune must keep working, or stale work piles up on the board forever. The page
    WAS re-scored this run and no longer carries this finding."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:messaging@x", PAGE)
        _seed_ticket(mid, "skill:conversion@x", PAGE)
        _prune(mid, live_keys=["skill:conversion@x"], scored=[PAGE], in_pack=[PAGE])
        assert _keys(mid) == {"skill:conversion@x"}
    finally:
        _cleanup(client_id)


def test_a_page_that_left_the_pack_is_pruned() -> None:
    """Its fixes are no longer ours to track, even though we did not re-score it."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:messaging@gone", OTHER_PAGE)
        _prune(mid, live_keys=["skill:messaging@x"], scored=[PAGE], in_pack=[PAGE])
        assert _keys(mid) == set()
    finally:
        _cleanup(client_id)


def test_verified_and_closed_work_is_never_pruned() -> None:
    """Unchanged contract: these hold the pinned baseline->current record and the pack's
    completion signal. Deleting one would destroy the CH-15 delta and re-lock an earned
    pack on the next audit."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:done@x", PAGE, status="verified_completed")
        _seed_ticket(mid, "skill:verifying@x", PAGE, status="closed_pending_verify")
        _prune(mid, live_keys=[], scored=[PAGE], in_pack=[PAGE])
        assert _keys(mid) == {"skill:done@x", "skill:verifying@x"}
    finally:
        _cleanup(client_id)


def test_a_ticket_with_no_page_url_is_kept() -> None:
    """Legacy rows we cannot attribute to a page. Neither prune arm matches (SQL NULL
    comparison yields NULL), so they survive — carrying a stale row until the next real
    re-score is far cheaper than deleting work a user still needs."""
    client_id = _client_id()
    mid = _milestone_id(client_id)
    try:
        _seed_ticket(mid, "skill:legacy@x", None)
        _prune(mid, live_keys=[], scored=[PAGE], in_pack=[PAGE])
        assert _keys(mid) == {"skill:legacy@x"}
    finally:
        _cleanup(client_id)


# ── the end-to-end guard ──────────────────────────────────────────────────────────
# The tests above execute the prune STATEMENT, which pins its semantics but would keep
# passing if someone reverted the source and generate_tickets_from_run stopped issuing it.
# This one drives the real function over a real two-run fixture: run A scores the page, run B
# is the unchanged re-audit that scores nothing. It is the test that actually fails on the
# old code.


def _seed_run(client_id: int, *, key: str, scored: bool) -> int:
    """A run with one crawled page in pack 1, optionally with skill detail. `scored=False`
    reproduces a fingerprint-skipped re-crawl: the page is there, but nothing re-scored it."""
    import json

    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_runs (run_key, label, status) VALUES (%s, 'prune test', 'succeeded') "
            "ON CONFLICT (run_key) DO UPDATE SET label = EXCLUDED.label RETURNING id",
            (key,),
        )
        run_id = int(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO crawled_pages (run_id, client_id, url, url_normalized) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (run_id, client_id, PAGE, PAGE),
        )
        page_id = int(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO packs (run_id, pack_index, title, page_count, status) "
            "VALUES (%s, 1, 'Pack 1', 1, 'scored') ON CONFLICT (run_id, pack_index) DO NOTHING",
            (run_id,),
        )
        cur.execute(
            "INSERT INTO page_priorities "
            "  (run_id, url, page_type, base_weight, traffic_signal, final_score, selected, pack_index) "
            "VALUES (%s, %s, 'homepage', 1.0, 0, 1.0, TRUE, 1) ON CONFLICT DO NOTHING",
            (run_id, PAGE),
        )
        if scored:
            detail = {
                "skills": {"messaging": {"score": 20, "confidence": "high",
                                         "suggestions": [{"id": "s1", "text": "Say what you do."}]}},
                "priorities": [{"skill": "messaging", "text": "Say what you do", "criterion": None,
                                "skill_score": 20, "impact": 0.5, "lift": 0.4, "lift_basis": "headroom"}],
            }
            cur.execute(
                "INSERT INTO skill_scores (page_id, run_id, messaging_score, conversion_score, "
                "  discovery_visibility_score, proof_trust_score, structure_ux_score, overall_score, detail) "
                "VALUES (%s, %s, 20, 20, 20, 20, 20, 20, %s::jsonb) "
                "ON CONFLICT (page_id, run_id, skills_version) DO UPDATE SET detail = EXCLUDED.detail",
                (page_id, run_id, json.dumps(detail)),
            )
    return run_id


def _drop_run(run_id: int) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM page_priorities WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM crawl_runs WHERE id = %s", (run_id,))  # pages/packs cascade


def test_generate_tickets_survives_an_unchanged_re_audit_end_to_end() -> None:
    """The real thing, through the real function. Fails on the old code: the second call
    deleted every pending ticket the first had created."""
    client_id = _client_id()
    run_a = _seed_run(client_id, key="prune-test-run-a", scored=True)
    run_b = _seed_run(client_id, key="prune-test-run-b", scored=False)
    try:
        m.generate_tickets_from_run(run_a)
        after_first = len(m.list_tickets_for_run(client_id, 1))
        assert after_first > 0, "the scored run must produce tickets to begin with"

        m.generate_tickets_from_run(run_b)  # the unchanged re-audit
        after_reaudit = len(m.list_tickets_for_run(client_id, 1))
        assert after_reaudit == after_first, (
            f"re-auditing an unchanged site destroyed open fixes: "
            f"{after_first} -> {after_reaudit}"
        )
    finally:
        _drop_run(run_a)
        _drop_run(run_b)
        _cleanup(client_id)
