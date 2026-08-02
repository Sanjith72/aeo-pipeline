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

    # Whether we could actually READ the site this run. Without these the caller cannot
    # tell "you haven't published anything yet" from "we couldn't reach your site" — and
    # the UI told every blocked user the former, which is a lie that blames them.
    reachable: bool = False       # the homepage returned real, parseable HTML
    blocked: bool = False         # a bot wall / challenge stub / non-200 answered instead
    pages_fetched: int = 0        # how many documents we actually read

    def to_dict(self) -> dict[str, Any]:
        return {
            "slugs": sorted(self.slugs),
            "services": self.services,
            "headings": self.headings,
            "nav_labels": self.nav_labels,
            "reachable": self.reachable,
            "blocked": self.blocked,
            "pages_fetched": self.pages_fetched,
        }


# ── pure normalization + matching ────────────────────────────────────────────


def _norm_text(value: str) -> str:
    """Lowercase, collapse non-alphanumerics to single spaces — for fuzzy label match."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _tokens(value: str) -> list[str]:
    """Normalized word tokens. Matching is done on WHOLE tokens, never raw substrings:
    a bare ``in`` must not match ``insurance``, and a two-letter language switcher (``EN``)
    must not match ``emergency`` — both of which the old substring test accepted."""
    return [t for t in _norm_text(value).split(" ") if t]


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


_MIN_MATCH_TOKENS = 2  # a single generic word ("services", "about") is never enough


def _label_matches(target: str, labels: list[str]) -> bool:
    """Whole-token containment: every token of the target appears, in order and adjacently,
    inside a label (or the label inside the target) — so "Teeth Whitening" still matches
    "Teeth Whitening Services", but "EN" no longer matches "Emergency Plumbing".

    Deliberately strict. This decides whether we tell someone their work is live, and a
    false 'verified' is worse than a missed one: it is permanent, it inflates the progress
    bar, and it tells them to stop working on something they never did.
    """
    t_tokens = _tokens(target)
    if len(t_tokens) < _MIN_MATCH_TOKENS:
        return False
    for raw in labels:
        l_tokens = _tokens(raw)
        if not l_tokens:
            continue
        short, long = (t_tokens, l_tokens) if len(t_tokens) <= len(l_tokens) else (l_tokens, t_tokens)
        if len(short) < _MIN_MATCH_TOKENS:
            continue
        # Contiguous whole-token subsequence.
        if any(long[i : i + len(short)] == short for i in range(len(long) - len(short) + 1)):
            return True
    return False


def _page_candidate(target: str, signals: SiteSignals) -> str | None:
    """The live slug that would satisfy this 'page' task, or None.

    Returns a *candidate* — a slug we have seen referenced (sitemap entry, <a href>, or a
    page we actually fetched). Whether it truly resolves is confirmed separately by
    :func:`confirm_slugs`, because a sitemap entry or a nav link can point at a 404.

    Two ways to match, both exact at the path level:
      * the slug itself is live, or
      * (multi-segment targets only) a live slug has the same LAST segment — sites move
        ``/services/x`` to ``/x`` or ``/our-services/x`` freely. Single-segment targets are
        excluded from this, otherwise ``/blog/pricing`` would satisfy a ``/pricing`` task.

    The old heading/offering/nav-label fallback is GONE. It was the source of essentially
    every false verification: a nav link "About Us" satisfied ``/about``, and an <h1>
    reading "Emergency Plumbing Services in Austin" satisfied ``/locations/austin``. A page
    task is about a PAGE existing — mentioning the topic in copy is not the same thing.
    """
    slug = path_slug(target)
    if slug in signals.slugs:
        return slug
    if len([seg for seg in slug.split("/") if seg]) < 2:
        return None
    tail = _last_segment(slug)
    if not tail:
        return None
    for live in signals.slugs:
        if _last_segment(live) == tail:
            return live
    return None


def evaluate(tasks: list[dict[str, Any]], signals: SiteSignals) -> set[str]:
    """Pure: return the ``task_key``s now satisfied by the live ``signals``.

    Each task is a dict with at least ``task_key``, ``verify_kind`` and ``verify_target``.
    ``manual`` tasks are never returned (no on-site signal). Unknown kinds are ignored.

    For ``page`` tasks this is a *provisional* verdict based on slugs the site merely
    REFERENCES. Callers with network access should narrow it with :func:`confirm_slugs`
    (see :func:`page_candidates`) so a stale sitemap entry or a broken nav link can't mark
    a 404 as "live"."""
    return {key for key, _ in _satisfied(tasks, signals)}


def page_candidates(tasks: list[dict[str, Any]], signals: SiteSignals) -> dict[str, str]:
    """``{task_key: live_slug}`` for the ``page`` tasks :func:`evaluate` provisionally
    accepted — the slugs whose existence still needs confirming with a real request."""
    return {key: slug for key, slug in _satisfied(tasks, signals) if slug is not None}


def _satisfied(tasks: list[dict[str, Any]], signals: SiteSignals) -> list[tuple[str, str | None]]:
    """(task_key, slug-needing-confirmation | None) for every satisfied task."""
    out: list[tuple[str, str | None]] = []
    for task in tasks:
        kind = str(task.get("verify_kind") or "manual")
        target = str(task.get("verify_target") or "")
        key = str(task.get("task_key") or "")
        if not key or not target or kind == "manual":
            continue
        if kind == "page":
            slug = _page_candidate(target, signals)
            if slug is not None:
                out.append((key, slug))
        elif kind in ("service", "heading"):
            # Text-presence kinds: matched against live copy, so no URL to confirm.
            labels = signals.headings + signals.nav_labels
            if kind == "service":
                labels = signals.services + labels
            if _label_matches(target, labels):
                out.append((key, None))
    return out


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

    injected = fetch is not None
    fetch = fetch or _default_fetch_text
    home = seed_url(domain)

    async def _try(fn: FetchText, url: str) -> str | None:
        try:
            return await fn(url)
        except Exception as exc:
            log.warning("milestone_signals_fetch_failed", domain=domain, url=url, error=str(exc))
            return None

    home_html = await _try(fetch, home)

    # Escalate ONLY when the polite AEOBot UA came back empty or bot-walled. Many sites
    # 403 an identified bot while serving a browser fine, and this check runs against the
    # user's OWN site at their explicit request — silently reporting "nothing new is live"
    # because their WAF blocked us is the worst possible answer. Polite first, escalate on
    # failure: identical behaviour on sites that answer us normally.
    blocked = site_facts.is_challenge_page(home_html)
    if blocked and not injected:
        from ..crawl.discovery import browser_fetch_text, playwright_fetch_text
        from ..settings import get_settings

        retried = await _try(browser_fetch_text, home)
        if not site_facts.is_challenge_page(retried):
            home_html, blocked, fetch = retried, False, browser_fetch_text
        elif get_settings().intake.playwright_fallback:
            rendered = await _try(playwright_fetch_text, home)
            if not site_facts.is_challenge_page(rendered):
                home_html, blocked = rendered, False

    docs: list[site_facts.FetchedDoc] = []
    if home_html and not blocked:
        docs.append(site_facts.FetchedDoc(url=home, html=home_html))
        for url in site_facts._select_key_pages(home, home_html, limit=max(0, max_pages - 1)):
            html = await _try(fetch, url)
            if html and not site_facts.is_challenge_page(html):
                docs.append(site_facts.FetchedDoc(url=url, html=html))

    try:
        signals = signals_from_docs(docs, domain=domain)
    except Exception as exc:  # extraction must never break the verification run
        log.warning("milestone_signals_extract_failed", domain=domain, error=str(exc))
        signals = SiteSignals()
    signals.reachable = bool(docs)
    signals.blocked = blocked
    signals.pages_fetched = len(docs)
    for slug in discovered_slugs or []:
        signals.slugs.add(path_slug(slug))
    return signals


async def confirm_slugs(
    slugs: set[str],
    *,
    domain: str,
    fetch: FetchText | None = None,
    limit: int = 50,
) -> set[str]:
    """Return the subset of ``slugs`` that a real request confirms resolves (HTTP 200).

    Nothing upstream proves a slug exists: sitemap ``<loc>`` values are parsed but never
    fetched, the BFS records links it never visits, and homepage ``<a href>``s go straight
    into the signal set. So a stale sitemap entry or a broken nav link used to mark a page
    "verified live" for a URL that 404s. This is the confirmation step.

    Bounded by ``limit`` (only candidate matches for still-pending tasks reach here, so the
    realistic count is a handful). Anything beyond the cap is left unconfirmed rather than
    optimistically accepted — under-claiming is the safe direction."""
    from ..crawl.discovery import seed_url

    fetch = fetch or _confirm_fetch
    base = seed_url(domain)
    confirmed: set[str] = set()
    for slug in sorted(slugs)[:limit]:
        try:
            if await fetch(absolute(base, slug)):
                confirmed.add(slug)
        except Exception as exc:
            log.warning("milestone_confirm_failed", domain=domain, slug=slug, error=str(exc))
    if len(slugs) > limit:
        log.info("milestone_confirm_capped", domain=domain, candidates=len(slugs), limit=limit)
    return confirmed


async def _confirm_fetch(url: str) -> str | None:
    """Default confirmation fetch: polite bot UA first, browser headers if that's blocked.

    Mirrors the escalation in :func:`gather_site_signals` — without it a WAF-fronted site
    could pass signal gathering (which escalates) and then fail every confirmation (which
    wouldn't), turning real published pages back into "nothing new is live yet".

    A soft-404 (HTTP 200 rendering a "not found" page) still reads as live; that's a known
    limit of confirming by status code alone."""
    from ..crawl.discovery import _default_fetch_text, browser_fetch_text

    html = await _default_fetch_text(url)
    if not site_facts.is_challenge_page(html):
        return html
    return await browser_fetch_text(url)
