"""v5 CH-14 — the AI-snapshot visibility verdict. Offline: the engine is disabled by
default and mocked when enabled; no network, no DB."""

from __future__ import annotations

import asyncio

from aeo.nlp.perplexity import CitationProbe
from aeo.pipeline import ai_visibility as av
from aeo.storage.models import ExtractionBundle


def _bundle(title: str = "Best project management software", h1: str | None = "PM software") -> ExtractionBundle:
    b = ExtractionBundle(page_id=0)
    b.put("meta", {"title": title})
    b.put("headings", {"h1_text": h1} if h1 else {})
    return b


def _run(bundle, url="https://acme.com/"):
    return asyncio.run(av.check_ai_visibility(bundle, url))


def test_disabled_engine_is_unavailable_not_a_fake_verdict(monkeypatch):
    # Perplexity is disabled by default → honest 'unavailable', never a fake 'not_cited'.
    r = _run(_bundle())
    assert r["status"] == "unavailable" and r["reason"] == "not_configured"
    assert r["question"]  # the derived question is still surfaced


def test_no_derivable_question_is_unavailable():
    r = _run(_bundle(title="", h1=None))
    assert r["status"] == "unavailable" and r["reason"] == "no_question"


class _Fake:
    enabled = True

    def __init__(self, probe):
        self._probe = probe

    def cited(self, question, *, target_url, timeout=None):
        return self._probe


def _patch(monkeypatch, probe):
    import aeo.nlp.perplexity as px

    av._CACHE.clear()
    monkeypatch.setattr(px, "get_perplexity_client", lambda: _Fake(probe))


def test_cited_via_structured_citations(monkeypatch):
    _patch(monkeypatch, CitationProbe(question="q", cited=True,
                                      citations=["https://acme.com/pm"], matched=["https://acme.com/pm"]))
    r = _run(_bundle())
    assert r["status"] == "cited" and r["via"] == "citations" and r["matched"]


def test_cited_only_in_answer_text_is_softer_signal(monkeypatch):
    # Domain appeared in the answer prose but not in structured citations → via='answer_text'.
    _patch(monkeypatch, CitationProbe(question="q", cited=True, citations=[], matched=[], answer="acme.com is good"))
    r = _run(_bundle())
    assert r["status"] == "cited" and r["via"] == "answer_text"


def test_not_cited(monkeypatch):
    _patch(monkeypatch, CitationProbe(question="q", cited=False, citations=[], matched=[]))
    r = _run(_bundle())
    assert r["status"] == "not_cited" and r["via"] is None


def test_probe_failure_degrades_to_unavailable(monkeypatch):
    _patch(monkeypatch, None)  # client.cited returns None (transport failure / disabled mid-flight)
    r = _run(_bundle())
    assert r["status"] == "unavailable" and r["reason"] == "probe_failed"


def test_result_is_cached_per_domain_question(monkeypatch):
    _patch(monkeypatch, CitationProbe(question="q", cited=True, citations=["https://acme.com/x"], matched=["https://acme.com/x"]))
    assert _run(_bundle())["cached"] is False
    assert _run(_bundle())["cached"] is True  # second call served from cache
