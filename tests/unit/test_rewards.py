"""Reward reconciler: pure band/maturity + idempotent reconcile over injected fakes."""

from __future__ import annotations

import pytest

from aeo.companion.rewards import band, maturity, reconcile


@pytest.mark.parametrize("score,expected", [(10, "Barely visible"), (50, "On the radar"),
                                            (70, "Recommended"), (90, "Top answer")])
def test_band(score, expected) -> None:
    assert band(score) == expected


@pytest.mark.parametrize("score,expected", [(10, "foundations"), (50, "on_radar"),
                                            (70, "recommended"), (90, "authority")])
def test_maturity(score, expected) -> None:
    assert maturity(score) == expected


class FakeGam:
    def __init__(self) -> None:
        self.awarded: list[int] = []
        self.unlocked: list[str] = []
        self.state: dict | None = None

    def grant_award(self, session_id, *, award_type, source_table, source_id, **kw):
        if source_id in self.awarded:
            return None  # idempotent: already granted
        self.awarded.append(source_id)
        return source_id

    def get_state(self, session_id):
        return self.state

    def unlock_achievement(self, session_id, code, **kw):
        if code in self.unlocked:
            return False
        self.unlocked.append(code)
        return True

    def upsert_state(self, session_id, **fields):
        self.state = {"session_id": session_id, **fields}
        return self.state


def test_reconcile_grants_each_verified_win_once_and_unlocks_tiers() -> None:
    gam = FakeGam()
    wins = [{"id": 1, "criterion": "qa_blocks", "url_normalized": "https://acme.com/a"},
            {"id": 2, "criterion": "schema_markup", "url_normalized": "https://acme.com/b"}]

    out = reconcile("s1", "acme.com", aeo_score=72, _gam=gam, _wins=wins)
    assert len(out["new_awards"]) == 2
    assert out["unlocked"] == ["recommended"]
    assert out["state"]["verified_wins"] == 2
    assert out["state"]["momentum"] == 2
    assert out["state"]["maturity_stage"] == "recommended"

    # Re-running grants nothing new (idempotent), momentum doesn't inflate past real wins.
    gam.state = {"momentum": 2}
    out2 = reconcile("s1", "acme.com", aeo_score=72, _gam=gam, _wins=wins)
    assert out2["new_awards"] == []
    assert out2["state"]["momentum"] == 2  # 2 prior + 0 new
