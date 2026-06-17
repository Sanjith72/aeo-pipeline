"""
R2-4 — capture human overrides as human-gated PROPOSALS (never auto-applied).

The learning loop is capture → propose → human-validate. These tests pin the guardrail:
an override yields a refinement with status 'proposed' (never 'accepted'), and the
capture path never auto-applies anything. All offline (the DB seams are faked).
"""

from __future__ import annotations

from aeo.reference.feedback import propose_refinement_from_override


def test_override_yields_a_proposed_refinement():
    ref = propose_refinement_from_override(
        field="industry", old_value="PEV", new_value="Cybersecurity",
    )
    assert ref.status == "proposed"  # never 'accepted'
    assert ref.criterion == "override:industry"
    assert ref.current_target is None and ref.proposed_target is None
    assert ref.evidence == {
        "kind": "field_override", "field": "industry", "old": "PEV", "new": "Cybersecurity",
    }
    assert "not auto-applied" in ref.rationale


def test_override_criterion_is_truncated_to_column_width():
    ref = propose_refinement_from_override(
        field="x" * 80, old_value="a", new_value="b",
    )
    assert len(ref.criterion) <= 40


def test_rejected_recommendation_kind_is_carried():
    ref = propose_refinement_from_override(
        field="rec:add-faq", old_value="recommended", new_value="rejected",
        kind="recommendation_rejected",
    )
    assert ref.status == "proposed"
    assert ref.evidence["kind"] == "recommendation_rejected"


def test_api_override_records_event_and_proposes_refinement(monkeypatch):
    from fastapi.testclient import TestClient

    from aeo.api.app import app
    from aeo.storage.repos import events as events_repo
    from aeo.storage.repos import feedback as feedback_repo

    recorded: dict = {}
    saved: list = []

    monkeypatch.setattr(
        events_repo, "record",
        lambda **kw: recorded.update(kw) or 1,
    )
    # capture the actual CriteriaRefinement handed to the repo (status must be 'proposed')
    monkeypatch.setattr(feedback_repo, "save_refinement", lambda ref: saved.append(ref) or 7)

    client = TestClient(app)
    r = client.post(
        "/api/overrides",
        json={"session_id": "s1", "field": "industry", "old_value": "PEV", "new_value": "Cybersecurity"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["captured"] is True
    assert body["event_recorded"] is True
    assert body["refinement_id"] == 7
    assert body["refinement_status"] == "proposed"

    # the event was logged with the override namespaced event_type
    assert recorded["event_type"] == "override:field_override"
    assert recorded["metadata"]["new"] == "Cybersecurity"

    # the saved refinement is a proposal — the capture path NEVER auto-accepts
    assert len(saved) == 1
    assert saved[0].status == "proposed"


def test_api_override_requires_session_and_field():
    from fastapi.testclient import TestClient

    from aeo.api.app import app

    client = TestClient(app)
    assert client.post("/api/overrides", json={"session_id": " ", "field": "x"}).status_code == 422
    assert client.post("/api/overrides", json={"session_id": "s", "field": " "}).status_code == 422


def test_api_override_survives_a_down_db(monkeypatch):
    # Both seams blow up → the handler still returns 200 with captured=False (best-effort).
    from fastapi.testclient import TestClient

    from aeo.api.app import app
    from aeo.storage.repos import events as events_repo
    from aeo.storage.repos import feedback as feedback_repo

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(events_repo, "record", boom)
    monkeypatch.setattr(feedback_repo, "save_refinement", boom)

    client = TestClient(app)
    r = client.post("/api/overrides", json={"session_id": "s", "field": "industry", "new_value": "X"})
    assert r.status_code == 200
    assert r.json()["captured"] is False
