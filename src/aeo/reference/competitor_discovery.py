"""
Competitor discovery — LLM proposal + live domain verification.

Onboarding a new client today means hand-curating ``config/entities.yaml``: canonical
name, aliases, first-person markers, and domain for every competitor. This module
automates the *discovery* half of that:

  1. :func:`discover_competitors` asks the LLM for a short list of real competitors
     (name + likely domain + aliases) given the client's name/domain/topic.
  2. Every proposed domain is then verified with a live HEAD request through the
     force-IPv4 transport seam (the same reachability check ``validation.adversarial``
     uses for citation signals) — an LLM can describe a real competitor and still
     invent a domain that doesn't exist (e.g. ``pentera-security.com`` instead of
     ``pentera.io``).

Verified candidates are safe to merge into ``entities.yaml``; unverified ones are
returned separately (never written) so the run summary can show what got dropped —
the LLM may have had the right company and the wrong domain, which is worth a human
glance rather than silent loss.

Deterministic-first like ``framework_bootstrap``: with no LLM (or on any LLM/parse
failure) this returns an empty result rather than raising — onboarding continues
with zero discovered competitors and the operator adds them by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..logging import get_logger
from ..nlp.llm import LLMClient
from .domain_config import normalize_domain

log = get_logger(__name__)

_MAX_ALIASES = 4
_DEFAULT_COUNT = 5
_MAX_COUNT = 12  # a hard ceiling so a pathological prompt response can't flood entities.yaml

_DISCOVERY_SYSTEM = (
    "You are a market research analyst identifying REAL, currently-operating direct "
    "competitors of a given company. Only name companies that actually exist — never "
    "invent plausible-sounding ones. Reply with JSON only."
)


def _discovery_prompt(name: str, domain: str, topic: str | None, count: int) -> str:
    topic_clause = f' in the "{topic}" space' if topic else ""
    return (
        f"Company: {name} ({domain}){topic_clause}\n"
        f"List up to {count} of this company's REAL, direct competitors — companies that "
        "actually exist and compete for the same customers today. Do not include the "
        "company itself.\n"
        'Return JSON with key "competitors": a list of objects, each '
        '{"name": canonical company name, "domain": their bare website domain '
        '(e.g. "acme.com", no scheme/www), "aliases": 1-3 alternate names or short '
        "forms people use for them (e.g. abbreviations, former names — NOT the canonical "
        "name repeated)}.\n"
        "If you are not confident a competitor is real and current, omit it rather than "
        "guessing. Reply JSON only."
    )


@dataclass(slots=True)
class DiscoveredCompetitor:
    """A single LLM-proposed competitor, pre- or post-verification."""

    name: str
    domain: str
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompetitorDiscoveryResult:
    """Verified candidates are merge-safe; dropped ones are reporting-only."""

    verified: list[DiscoveredCompetitor] = field(default_factory=list)
    dropped: list[DiscoveredCompetitor] = field(default_factory=list)
    raw_count: int = 0

    def to_summary(self) -> dict:
        return {
            "proposed": self.raw_count,
            "verified": [{"name": c.name, "domain": c.domain} for c in self.verified],
            "dropped": [{"name": c.name, "domain": c.domain} for c in self.dropped],
        }


HeadCheck = Callable[[str], bool]


def _default_head_check(domain: str) -> bool:
    """Reachability probe over the force-IPv4 transport — mirrors
    ``validation.adversarial._default_head_check``. Tries HEAD first (cheap), then
    falls back to GET for sites that 405/406 HEAD requests (common for marketing sites
    behind certain CDNs/WAFs). Any failure (DNS, timeout, 4xx/5xx on both) = unreachable."""
    from ..crawl.transport import sync_transport

    url = f"https://{domain}"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, transport=sync_transport()) as client:
            resp = client.head(url)
            if resp.status_code < 400:
                return True
            resp = client.get(url)
            return resp.status_code < 400
    except Exception as exc:  # unreachable is a signal, not a crash
        log.warning("competitor_domain_unreachable", domain=domain, error=str(exc))
        return False


def _clean_candidate(raw: object) -> DiscoveredCompetitor | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    domain = normalize_domain(str(raw.get("domain") or ""))
    if not name or not domain or "." not in domain:
        return None
    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        aliases_raw = []
    aliases = [str(a).strip() for a in aliases_raw if str(a).strip()][:_MAX_ALIASES]
    # Drop an alias that's just the canonical name again (models do this often).
    aliases = [a for a in aliases if a.lower() != name.lower()]
    return DiscoveredCompetitor(name=name, domain=domain, aliases=aliases)


def discover_competitors(
    name: str,
    domain: str,
    *,
    topic: str | None = None,
    count: int = _DEFAULT_COUNT,
    llm: LLMClient | None = None,
    head_check: HeadCheck | None = None,
) -> CompetitorDiscoveryResult:
    """Propose ``count`` competitors via the LLM and verify each domain live.

    Returns an empty result (never raises) when the LLM is unavailable, disabled, or
    returns nothing usable — onboarding must be able to proceed with zero discovered
    competitors and let the operator add them by hand."""
    if llm is None or not llm.enabled:
        return CompetitorDiscoveryResult()

    count = max(1, min(int(count), _MAX_COUNT))
    check = head_check or _default_head_check

    try:
        data = llm.generate_json(_discovery_prompt(name, domain, topic, count), _DISCOVERY_SYSTEM)
    except Exception as exc:
        log.warning("competitor_discovery_llm_failed", name=name, domain=domain, error=str(exc))
        return CompetitorDiscoveryResult()
    if not isinstance(data, dict):
        return CompetitorDiscoveryResult()

    self_host = normalize_domain(domain)
    candidates: list[DiscoveredCompetitor] = []
    seen_domains: set[str] = set()
    for raw in (data.get("competitors") or [])[: _MAX_COUNT]:
        cand = _clean_candidate(raw)
        if cand is None or cand.domain == self_host or cand.domain in seen_domains:
            continue
        seen_domains.add(cand.domain)
        candidates.append(cand)

    verified: list[DiscoveredCompetitor] = []
    dropped: list[DiscoveredCompetitor] = []
    for cand in candidates[:count]:
        if check(cand.domain):
            verified.append(cand)
        else:
            dropped.append(cand)
            log.info("competitor_candidate_dropped", name=cand.name, domain=cand.domain)

    return CompetitorDiscoveryResult(verified=verified, dropped=dropped, raw_count=len(candidates))
