"""Live-DB round-trip for the gamification repo. Skips when no DB is reachable."""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check, transaction
from aeo.storage.repos import gamification as gam

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")


def test_state_award_and_idempotency() -> None:
    sid = "itest-session-gam"
    try:
        gam.upsert_state(sid, domain="acme.example", aeo_score=72, aeo_band="Recommended",
                         maturity_stage="recommended", momentum=1, verified_wins=1)
        state = gam.get_state(sid)
        assert state["aeo_score"] == 72 and state["maturity_stage"] == "recommended"

        first = gam.grant_award(sid, award_type="verified_win", source_table="recommendation_outcomes",
                                source_id=987654321, criterion="qa_blocks")
        again = gam.grant_award(sid, award_type="verified_win", source_table="recommendation_outcomes",
                                source_id=987654321, criterion="qa_blocks")
        assert first is not None
        assert again is None  # idempotent — same verdict row never double-grants
        assert any(a["source_id"] == 987654321 for a in gam.awards_for(sid))

        assert gam.unlock_achievement(sid, "recommended") is True
        assert gam.unlock_achievement(sid, "recommended") is False  # earn-once
    finally:
        # Leave no itest rows behind, so the suite stays re-run-safe (mirrors test_db_smoke).
        with transaction() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM gamification_awards WHERE session_id = %s", (sid,))
            cur.execute("DELETE FROM achievement_unlocks WHERE session_id = %s", (sid,))
            cur.execute("DELETE FROM gamification_state WHERE session_id = %s", (sid,))
