"""Gamification endpoints — wiring over the repo/reconciler (monkeypatched, no DB)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_get_gamification_returns_state_and_awards(monkeypatch) -> None:
    from aeo.storage.repos import gamification as gam

    monkeypatch.setattr(gam, "get_state", lambda sid: {"session_id": sid, "verified_wins": 3})
    monkeypatch.setattr(gam, "awards_for", lambda sid, limit=50: [{"id": 1, "award_type": "verified_win"}])
    body = client.get("/api/gamification?session_id=s1&domain=acme.com").json()
    assert body["state"]["verified_wins"] == 3
    assert body["awards"][0]["award_type"] == "verified_win"


def test_get_gamification_empty_for_unknown_session(monkeypatch) -> None:
    from aeo.storage.repos import gamification as gam

    monkeypatch.setattr(gam, "get_state", lambda sid: None)
    body = client.get("/api/gamification?session_id=nope").json()
    assert body == {"state": None, "awards": []}


def test_reconcile_delegates_to_the_reconciler(monkeypatch) -> None:
    from aeo.companion import rewards

    monkeypatch.setattr(rewards, "reconcile",
                        lambda sid, domain, *, aeo_score=None: {"new_awards": [{"award_id": 9}],
                                                                "unlocked": ["recommended"], "state": {"momentum": 1}})
    body = client.post("/api/gamification/reconcile",
                       json={"session_id": "s1", "domain": "acme.com", "aeo_score": 72}).json()
    assert body["new_awards"] == [{"award_id": 9}]
    assert body["unlocked"] == ["recommended"]


def test_reconcile_is_best_effort_on_error(monkeypatch) -> None:
    from aeo.companion import rewards

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(rewards, "reconcile", boom)
    body = client.post("/api/gamification/reconcile", json={"session_id": "s1"}).json()
    assert body == {"new_awards": [], "unlocked": [], "state": None}
