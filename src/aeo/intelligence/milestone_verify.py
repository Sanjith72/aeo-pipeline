"""
Milestone verification — "did they actually do it?", detected from the live site.

Two halves, kept apart so the decision logic is offline-testable:

  * :func:`evaluate` — **pure**: given pending milestone tasks and a snapshot of the
    site's live signals (:class:`SiteSignals`), decide which tasks are now satisfied.
    A ``page`` task is verified when a page at its slug is live (or a heading / offering
    matching its title appears); ``service`` / ``heading`` tasks match the live offerings
    / headings; ``manual`` tasks (off-site visibility wins) are never auto-verified.

  * :func:`gather_site_signals` — the network half: re-scrape the homepage + key pages
    (reusing :mod:`aeo.intelligence.site_facts`) and fold them into a :class:`SiteSignals`.

This is intentionally NARROWER than the Retention Engine's hash check
(:mod:`aeo.storage.repos.outcomes`): rather than "the page changed", we look for the
*specific recommended artifact* — the page, offering, or heading — being present. That's
a direct, non-circular signal (we detect existence, we don't re-grade quality), which is
exactly what the weekly crawl needs to flip a milestone to ``verified_completed``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger
from ..utils.html import parse
from ..utils.url import absolute, normalize, same_site
from . import site_facts

log = get_logger(__name__)

FetchText = Callable[[str], Awaitable["str | None"]]

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(slots=True)
class SiteSignals:
    """A snapshot of what's live on the site, for verifying milestone completion."""

    # Normalized URL path slugs present live (homepage links + discovered inventory),
    # e.g. {"/services/teeth-whitening", "/about"}.
    slugs: set[str] = field(default_factory=set)
    services: list[str] = field(default_factory=list)   # site_facts offerings
    headings: list[str] = field(default_factory=list)    # all heading text, live
    nav_labels: list[str] = field(default_factory=list)  # anchor/menu text, live

    def to_dict(self) -> dict[str, Any]:
        return {
            "slugs": sorted(self.slugs),
            "services": self.services,
            "headings": self.headings,
            "nav_labels": self.nav_labels,
        }


# ── pure normalization + matching ────────────────────────────────────────────


def _norm_text(value: str) -> str:
    """Lowercase, collapse non-alphanumerics to single spaces — for fuzzy label match."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def path_slug(url_or_slug: str) -> str:
    """The normalized path of a URL or slug, no trailing slash, lowercased.

    ``https://x.com/Services/Teeth-Whitening/`` and ``/services/teeth-whitening`` both
    become ``/services/teeth-whitening`` so live URLs and plan slugs compare cleanly."""
    s = (url_or_slug or "").strip()
    if "://" in s:
        from urllib.parse import urlsplit

        s = urlsplit(s).path
    s = "/" + s.strip("/").lower()
    return s if s != "/" else "/"


def _last_segment(slug: str) -> str:
    seg = path_slug(slug).rstrip("/").rsplit("/", 1)[-1]
    return _norm_text(seg.replace("-", " "))


def _label_matches(target: str, labels: list[str]) -> bool:
    """Fuzzy presence: the normalized target equals or is contained in a normalized label
    (or vice-versa, to catch "Teeth Whitening" vs "Teeth Whitening Services")."""
    t = _norm_text(target)
    if not t or len(t) < 3:
        return False
    for raw in labels:
        label = _norm_text(raw)
        if not label:
            continue
        if t == label or t in label or label in t:
            return True
    return False


def _page_verified(target: str, signals: SiteSignals) -> bool:
    """A 'page' task is done when its slug is live, OR (sites rename slugs freely) a
    heading / offering / nav label matching the slug's last segment is present."""
    slug = path_slug(target)
    if slug in signals.slugs:
        return True
    # The slug's tail vs live slugs' tails (e.g. /services/x vs /x or /our-services/x).
    tail = _last_segment(slug)
    if tail and any(_last_segment(s) == tail for s in signals.slugs):
        return True
    label = tail or _norm_text(slug.replace("/", " "))
    return _label_matches(label, signals.headings + signals.services + signals.nav_labels)


def evaluate(tasks: list[dict[str, Any]], signals: SiteSignals) -> set[str]:
    """Pure: return the ``task_key``s now satisfied by the live ``signals``.

    Each task is a dict with at least ``task_key``, ``verify_kind`` and ``verify_target``.
    ``manual`` tasks are never returned (no on-site signal). Unknown kinds are ignored."""
    done: set[str] = set()
    for task in tasks:
        kind = str(task.get("verify_kind") or "manual")
        target = str(task.get("verify_target") or "")
        key = str(task.get("task_key") or "")
        if not key or not target or kind == "manual":
            continue
        if (kind == "page" and _page_verified(target, signals)) or (kind == "service" and _label_matches(target, signals.services + signals.nav_labels + signals.headings)) or (kind == "heading" and _label_matches(target, signals.headings + signals.nav_labels)):
            done.add(key)
    return done


# ── network gatherer (injectable fetch) ──────────────────────────────────────


def signals_from_docs(docs: list[site_facts.FetchedDoc], *, domain: str) -> SiteSignals:
    """Fold fetched (url, html) pairs into a :class:`SiteSignals`. Pure — no network."""
    own = normalize(docs[0].url) if docs else f"https://{domain}/"
    slugs: set[str] = set()
    headings: list[str] = []
    nav_labels: list[str] = []
    for doc in docs:
        slugs.add(path_slug(doc.url))
        soup = parse(doc.html)
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = absolute(doc.url, href)
            if same_site(abs_url, own):
                slugs.add(path_slug(abs_url))
            text = a.get_text(" ", strip=True)
            if text:
                nav_labels.append(text)
        for h in soup.find_all(_HEADING_TAGS):
            text = h.get_text(" ", strip=True)
            if text:
                headings.append(text)
    services = site_facts.extract_facts(docs, domain=domain).services if docs else []
    return SiteSignals(slugs=slugs, services=services, headings=headings, nav_labels=nav_labels)


async def gather_site_signals(
    domain: str,
    *,
    fetch: FetchText | None = None,
    discovered_slugs: list[str] | None = None,
    max_pages: int = site_facts._MAX_PAGES,
) -> SiteSignals:
    """Re-scrape the homepage + key pages and derive live :class:`SiteSignals`.

    Best-effort (mirrors :func:`site_facts.gather_site_facts`): a dead homepage yields
    empty signals rather than raising, so the weekly verifier degrades to "nothing newly
    verified" instead of failing the run. ``discovered_slugs`` (e.g. the audit run's full
    URL inventory) is merged in, so a page deep in the site still counts as live even when
    it isn't linked from the homepage."""
    from ..crawl.discovery import _default_fetch_text, seed_url

    fetch = fetch or _default_fetch_text
    home = seed_url(domain)
    try:
        home_html = await fetch(home)
    except Exception as exc:
        log.warning("milestone_signals_homepage_failed", domain=domain, error=str(exc))
        home_html = None

    docs: list[site_facts.FetchedDoc] = []
    if home_html:
        docs.append(site_facts.FetchedDoc(url=home, html=home_html))
        for url in site_facts._select_key_pages(home, home_html, limit=max(0, max_pages - 1)):
            try:
                html = await fetch(url)
            except Exception:
                html = None
            if html:
                docs.append(site_facts.FetchedDoc(url=url, html=html))

    try:
        signals = signals_from_docs(docs, domain=domain)
    except Exception as exc:  # extraction must never break the verification run
        log.warning("milestone_signals_extract_failed", domain=domain, error=str(exc))
        signals = SiteSignals()
    for slug in discovered_slugs or []:
        signals.slugs.add(path_slug(slug))
    return signals
