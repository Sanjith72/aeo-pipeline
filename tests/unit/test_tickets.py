"""v5 CH-08/CH-15 tickets — offline API-surface + pure-helper checks (no Postgres, per the
offline test convention). The DB lifecycle (generate→close→verify→completed) is verified
separately against a live PG."""

from __future__ import annotations

import inspect


def test_ticket_repo_exposes_its_api() -> None:
    from aeo.storage.repos import milestones as m

    for fn in ("generate_tickets_from_run", "close_ticket", "reopen_ticket", "set_ticket_fields",
               "verify_tickets_by_recrawl", "list_tickets_for_run"):
        assert callable(getattr(m, fn))


def test_completed_pack_indices_exists() -> None:
    from aeo.storage.repos import packs

    assert callable(packs.completed_pack_indices)


def test_skills_with_findings_prefers_priorities() -> None:
    from aeo.storage.repos.milestones import _skills_with_findings

    detail = {"priorities": [{"skill": "messaging"}, {"skill": "conversion"}, {"skill": "messaging"}],
              "skills": {}}
    assert _skills_with_findings(detail) == ["messaging", "conversion"]  # deduped, ordered


def test_skills_with_findings_falls_back_to_suggestions() -> None:
    from aeo.storage.repos.milestones import _skills_with_findings

    detail = {"priorities": [],
              "skills": {"messaging": {"suggestions": [{"text": "x"}]}, "conversion": {"suggestions": []}}}
    assert _skills_with_findings(detail) == ["messaging"]


def test_url_path_helper() -> None:
    from aeo.storage.repos.milestones import _url_path

    assert _url_path("https://x.com/pricing") == "/pricing"
    assert _url_path("https://x.com/") == "x.com"  # bare-domain fallback for the homepage


def test_run_urls_accepts_force_recrawl() -> None:
    # CH-15: the close→verify re-crawl must bypass the fingerprint skip gate.
    from aeo.pipeline.orchestrator import Orchestrator

    assert "force_recrawl" in inspect.signature(Orchestrator.run_urls).parameters


def test_enqueue_batch_carries_force_recrawl() -> None:
    from aeo.pipeline import worker

    assert "force_recrawl" in inspect.signature(worker.enqueue_batch).parameters
