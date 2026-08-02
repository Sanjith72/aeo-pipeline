"""
AI-snapshot visibility (v5 CH-14) — does this page get CITED by AI answer engines?

Surfaces the existing Perplexity citation machinery (``nlp/perplexity.py`` +
``validation/independent.derive_question``) as a clean, per-page "AI visibility" signal
for the Discovery & Visibility skill and the free-overview headline.

Honest by construction — the three states are distinct and never conflated:
  * ``cited``       — the page's own domain showed up in the answer's citations (or, as a
                      fallback, in the answer text). ``via`` says which, so a text-scan
                      match (softer evidence) is never presented as a hard citation.
  * ``not_cited``   — the engine ran and the domain did NOT appear.
  * ``unavailable`` — the check did not run (Perplexity disabled/unkeyed, no derivable
                      question, or it exceeded the bounded wait) — never a fake "not cited".

Cheap + bounded for the free tier: one cached probe per (domain, question), a short wait
cap so it never blows the overview's latency budget, and — when Perplexity is unconfigured
(the default) — an instant ``unavailable`` with zero network cost. The engine's per-request
rate is already bounded by the overview's per-domain cache + per-IP/global caps.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..logging import get_logger
from ..storage.models import ExtractionBundle
from ..utils.url import host_of, normalize

log = get_logger(__name__)

# Cap the wait on the (synchronous) Perplexity probe so it never blows the overview's
# ~10s budget (the client's own timeout is 60s — far too long for a free, inline check).
_PROBE_WAIT_SEC = 8.0
# Per-(domain, question) result cache — repeated overviews of a domain reuse the verdict.
_CACHE_TTL_SEC = 86400
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_MAX = 500


def _unavailable(reason: str, question: str | None = None) -> dict[str, Any]:
    return {"status": "unavailable", "engine": "perplexity", "reason": reason, "question": question}


def _cache_get(domain: str, question: str) -> dict[str, Any] | None:
    hit = _CACHE.get((domain, question))
    if hit is None or time.time() - hit[0] >= _CACHE_TTL_SEC:
        return None
    return hit[1]


def _cache_put(domain: str, question: str, payload: dict[str, Any]) -> None:
    now = time.time()
    if len(_CACHE) > _CACHE_MAX:
        # Drop expired first…
        for k, (ts, _) in list(_CACHE.items()):
            if now - ts >= _CACHE_TTL_SEC:
                _CACHE.pop(k, None)
        # …then, if still over the cap, evict oldest-first so _CACHE_MAX is a real memory
        # bound, not just a TTL bound (mirrors overview._store).
        if len(_CACHE) >= _CACHE_MAX:
            for k, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: len(_CACHE) - _CACHE_MAX + 1]:
                _CACHE.pop(k, None)
    _CACHE[(domain, question)] = (now, payload)


def _prepare(bundle: ExtractionBundle, url: str) -> tuple[dict[str, Any] | None, Any, str, str]:
    """Shared pre-flight for both entry points: derive the question, check the engine is
    configured, and serve the cache. Returns ``(early_result, client, question, domain)`` —
    when ``early_result`` is non-None the caller returns it verbatim and never probes."""
    from ..nlp.perplexity import get_perplexity_client
    from ..validation.independent import derive_question

    try:
        question = derive_question(bundle)
    except Exception:
        question = None
    if not question:
        return _unavailable("no_question"), None, "", ""

    try:
        client = get_perplexity_client()
        if not client.enabled:
            return _unavailable("not_configured", question), None, question, ""
        domain = host_of(normalize(url)) or url
    except Exception:  # honor the never-raises contract even on a malformed URL
        return _unavailable("error", question), None, question or "", ""

    cached = _cache_get(domain, question)
    if cached is not None:
        return {**cached, "cached": True}, None, question, domain
    return None, client, question, domain


def _verdict(probe: Any, question: str, domain: str) -> dict[str, Any]:
    """Turn a probe result into the honest three-state verdict and cache it."""
    if probe is None:  # disabled mid-flight / transport failure
        return _unavailable("probe_failed", question)
    verdict = {
        "status": "cited" if probe.cited else "not_cited",
        "engine": "perplexity",
        "question": question,
        # 'citations' = structured citation URLs that matched our domain (hard signal);
        # 'answer_text' = the domain only appeared in the answer prose (softer signal).
        "via": "citations" if probe.matched else ("answer_text" if probe.cited else None),
        "matched": probe.matched,
        "cached": False,
    }
    _cache_put(domain, question, verdict)
    return verdict


def check_ai_visibility_sync(bundle: ExtractionBundle, url: str) -> dict[str, Any]:
    """Blocking variant, for callers already off the event loop (the deep-audit page
    pipeline, ``Orchestrator._process_one``, is synchronous). The Perplexity client is
    itself synchronous, so this is the primitive and the async version wraps it — no
    ``asyncio.run`` inside a live loop. Same never-raises contract."""
    early, client, question, domain = _prepare(bundle, url)
    if early is not None:
        return early
    try:
        probe = client.cited(question, target_url=url, timeout=_PROBE_WAIT_SEC)
    except Exception as exc:
        log.warning("ai_visibility_probe_failed", url=url, error=str(exc))
        return _unavailable("probe_failed", question)
    return _verdict(probe, question, domain)


async def check_ai_visibility(bundle: ExtractionBundle, url: str) -> dict[str, Any]:
    """The AI-visibility verdict for one page. Never raises — any failure or a disabled
    engine degrades to ``unavailable`` (honest), never a fake ``not_cited``."""
    early, client, question, domain = _prepare(bundle, url)
    if early is not None:
        return early

    try:
        # The client is synchronous → run it off the event loop. The HTTP call itself is
        # bounded (timeout=_PROBE_WAIT_SEC) so the worker thread finishes at the cap instead
        # of lingering on the client's 60s default and starving the shared pool; the outer
        # wait_for is a small-margin safety net. A timeout yields 'unavailable', never a
        # wrong verdict.
        probe = await asyncio.wait_for(
            asyncio.to_thread(client.cited, question, target_url=url, timeout=_PROBE_WAIT_SEC),
            timeout=_PROBE_WAIT_SEC + 2.0,
        )
    except Exception as exc:  # TimeoutError included — any failure degrades honestly
        log.warning("ai_visibility_probe_failed", url=url, error=str(exc))
        return _unavailable("probe_failed", question)

    return _verdict(probe, question, domain)
