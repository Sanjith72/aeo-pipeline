"""Critic agent: per-draft independent + adversarial + claim verdicts (deterministic floors)."""

from __future__ import annotations

from aeo.agents.critic import claim_audit, review_drafts


def _draft_payload(body: str = "# Page\n\nA clean, liftable answer in one short sentence.\n") -> dict:
    return {
        "body_markdown": body,
        "jsonld": [{"@context": "https://schema.org", "@type": "WebPage", "name": "Page"}],
        "h1": "Page", "meta_description": "x", "sections": [], "faq": [],
    }


def test_claim_audit_flags_stats_and_superlatives_deterministically() -> None:
    res = claim_audit("We are the #1 leading platform with a 99% uptime guarantee.", llm=None)
    assert res["flagged"] is True
    assert res["source"] == "deterministic"
    assert res["claims"], "expected at least one flagged claim phrase"


def test_claim_audit_passes_clean_prose() -> None:
    res = claim_audit("This page explains how the process works and who it helps.", llm=None)
    assert res["flagged"] is False


def test_review_annotates_every_draft() -> None:
    graph = {"tasks": [{"id": "page:/x", "slug": "/x", "draft": _draft_payload()}]}
    out = review_drafts(graph, llm=None, origin="https://acme.com")
    verdict = out["tasks"][0]["critic"]
    assert set(verdict) >= {"passed", "independent_passed", "adversarial", "claims_flagged", "needs_review"}
    assert isinstance(verdict["needs_review"], bool)


def test_review_fails_a_draft_with_a_hallucinated_citation() -> None:
    # 'http://no-dot-host/page' has a netloc with no dot → structurally invalid → hallucinated,
    # with no network needed (the deterministic citation-signal floor catches it).
    graph = {"tasks": [{"id": "x", "slug": "/x", "draft": _draft_payload("Source: http://no-dot-host/page")}]}
    out = review_drafts(graph, llm=None, origin=None)
    verdict = out["tasks"][0]["critic"]
    assert verdict["adversarial"]["hallucinated"] >= 1
    assert verdict["passed"] is False
    assert verdict["needs_review"] is True
    assert out["tasks"][0]["status"] == "flagged"


def test_review_skips_tasks_without_a_draft() -> None:
    graph = {"tasks": [{"id": "x", "slug": "/x"}]}
    out = review_drafts(graph, llm=None)
    assert "critic" not in out["tasks"][0]
