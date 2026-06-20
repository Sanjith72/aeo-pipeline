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

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from ..logging import get_logger
from ..nlp.llm import LLMClient
from .domain_config import normalize_domain

log = get_logger(__name__)

_MAX_ALIASES = 4
_DEFAULT_COUNT = 5
_MAX_COUNT = 12  # a hard ceiling so a pathological prompt response can't flood entities.yaml
# Verify candidate domains concurrently — independent network probes, so a sequential loop
# wasted ~timeout × N seconds. Bounded so a pathological response can't open a thread storm.
_MAX_VERIFY_WORKERS = 8
_HEAD_CHECK_TIMEOUT = 5.0  # per-probe; a competitor's site is either up fast or not worth waiting on

_DISCOVERY_SYSTEM = (
    "You are a market research analyst identifying REAL, currently-operating direct "
    "competitors of a given company. Only name companies that actually exist — never "
    "invent plausible-sounding ones. Reply with JSON only."
)


def _discovery_prompt(
    name: str,
    domain: str,
    topic: str | None,
    location: str | None,
    count: int,
    services: list[str] | None = None,
    *,
    strict: bool = True,
) -> str:
    topic_clause = f' in the {topic} industry' if topic else ""
    location_clause = f", based in or serving {location}" if location else ""
    company = f"{name} ({domain})" if domain else name
    # The crawled offerings sharpen "direct competitor" — companies selling these same
    # things, not just anyone in the broad category.
    cleaned_services = [s.strip() for s in (services or []) if s and s.strip()][:8]
    services_clause = (
        f"\nThey offer: {', '.join(cleaned_services)}. Prioritise competitors that sell "
        "these same products/services."
        if cleaned_services
        else ""
    )
    # Strict constraints (R: industry + location) so suggestions are true peers, not just
    # well-known names in an adjacent space. Dropped on a relaxed pass (``strict=False``):
    # a strict industry+city ask can legitimately match nothing, and a blank picker is
    # worse than slightly broader peers — see the relaxation ladder in ``discover_competitors``.
    constraints: list[str] = []
    if strict and topic:
        constraints.append(f"be in the SAME industry ({topic})")
    if strict and location:
        constraints.append(f"serve the SAME market ({location})")
    constraint_clause = (
        "\nEvery competitor MUST " + " and ".join(constraints) + "." if constraints else ""
    )
    return (
        f"Company: {company}{topic_clause}{location_clause}{services_clause}{constraint_clause}\n"
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
    # True when the strict industry+location pass found nothing and a broadened pass
    # (wider location / dropped location / softened industry) supplied these results.
    relaxed: bool = False

    def to_summary(self) -> dict:
        return {
            "proposed": self.raw_count,
            "verified": [{"name": c.name, "domain": c.domain} for c in self.verified],
            "dropped": [{"name": c.name, "domain": c.domain} for c in self.dropped],
            "relaxed": self.relaxed,
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
        with httpx.Client(
            timeout=_HEAD_CHECK_TIMEOUT, follow_redirects=True, transport=sync_transport()
        ) as client:
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


def _relax_location(location: str | None) -> str | None:
    """Broaden a specific location to its region for a second-pass query, e.g.
    ``"Austin, TX"`` → ``"TX"`` (drop the city, keep the state/region/country). Returns
    None when there's nothing coarser to fall back to (a bare city/region with no comma)."""
    if not location:
        return None
    parts = [p.strip() for p in location.split(",") if p.strip()]
    return parts[-1] if len(parts) >= 2 else None


def _discover_once(
    name: str,
    domain: str,
    topic: str | None,
    location: str | None,
    services: list[str] | None,
    count: int,
    strict: bool,
    llm: LLMClient,
    check: HeadCheck,
) -> CompetitorDiscoveryResult:
    """One LLM proposal + verification pass. Returns an empty result on any LLM/parse
    failure (the caller decides whether to relax and retry)."""
    try:
        data = llm.generate_json(
            _discovery_prompt(name, domain, topic, location, count, services, strict=strict),
            _DISCOVERY_SYSTEM,
        )
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

    # Verify every candidate domain CONCURRENTLY (the checks are independent HEAD/GET
    # probes). ``executor.map`` preserves candidate order, so verified/dropped read the
    # same as the old sequential loop — only faster (wall-clock ≈ the slowest single probe,
    # not their sum).
    to_check = candidates[:count]
    verified: list[DiscoveredCompetitor] = []
    dropped: list[DiscoveredCompetitor] = []
    if to_check:
        with ThreadPoolExecutor(max_workers=min(len(to_check), _MAX_VERIFY_WORKERS)) as pool:
            outcomes = list(pool.map(lambda c: (c, check(c.domain)), to_check))
        for cand, ok in outcomes:
            if ok:
                verified.append(cand)
            else:
                dropped.append(cand)
                log.info("competitor_candidate_dropped", name=cand.name, domain=cand.domain)

    return CompetitorDiscoveryResult(verified=verified, dropped=dropped, raw_count=len(candidates))


def discover_competitors(
    name: str,
    domain: str = "",
    *,
    topic: str | None = None,
    location: str | None = None,
    services: list[str] | None = None,
    count: int = _DEFAULT_COUNT,
    llm: LLMClient | None = None,
    head_check: HeadCheck | None = None,
) -> CompetitorDiscoveryResult:
    """Propose ``count`` competitors via the LLM and verify each domain live.

    A strict industry+city ask can legitimately match nothing — our market may lack
    density for that exact combo, and the LLM is told to omit rather than guess. Rather
    than hand the UI a blank "No competitors found", walk a **relaxation ladder** and stop
    at the first pass that yields a verified competitor:

      1. strict industry + full location (the original behaviour),
      2. strict industry + broadened location (city → state/region),
      3. strict industry, location dropped,
      4. softened industry (no hard MUST), location dropped — widest net.

    Returns an empty result (never raises) when the LLM is unavailable, disabled, or every
    pass returns nothing usable — onboarding must be able to proceed with zero discovered
    competitors and let the operator add them by hand."""
    if llm is None or not llm.enabled:
        return CompetitorDiscoveryResult()

    count = max(1, min(int(count), _MAX_COUNT))
    check = head_check or _default_head_check

    relaxed_loc = _relax_location(location)
    # (topic, location, strict). Ordered strict → broad; deduped below so we never spend an
    # LLM call on a pass identical to one already tried (e.g. no location to broaden).
    ladder: list[tuple[str | None, str | None, bool]] = [(topic, location, True)]
    if relaxed_loc and relaxed_loc != location:
        ladder.append((topic, relaxed_loc, True))
    if location:
        ladder.append((topic, None, True))
    ladder.append((topic, None, False))

    seen: set[tuple[str | None, str | None, bool]] = set()
    fallback = CompetitorDiscoveryResult()
    for idx, (pass_topic, pass_loc, strict) in enumerate(ladder):
        key = (pass_topic, pass_loc, strict)
        if key in seen:
            continue
        seen.add(key)
        result = _discover_once(
            name, domain, pass_topic, pass_loc, services, count, strict, llm, check
        )
        # EARLY EXIT: the first pass that yields any verified competitor wins — we return
        # immediately rather than walking the rest of the ladder. So a strict pass that
        # already finds peers costs exactly ONE llm.generate_json call, never four; only a
        # genuinely empty pass falls through to the next (broader) rung.
        if result.verified:
            result.relaxed = idx > 0
            if result.relaxed:
                log.info(
                    "competitor_discovery_relaxed",
                    name=name, domain=domain, location=pass_loc, strict=strict,
                )
            return result
        # Keep the richest attempt (most proposed) for reporting if every pass comes up empty.
        if result.raw_count > fallback.raw_count:
            fallback = result

    return fallback
