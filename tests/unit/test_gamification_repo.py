"""gamification repo — offline API existence (DB round-trip lives in integration)."""

from __future__ import annotations


def test_exposes_its_api() -> None:
    from aeo.storage.repos import gamification

    for fn in ("get_state", "upsert_state", "grant_award", "awards_for", "unlock_achievement"):
        assert callable(getattr(gamification, fn))
