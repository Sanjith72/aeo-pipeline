"""
Milestone verification — the automated "did they do it?" crawl.

Runs on the weekly audit cadence (ops/) and on demand behind the "Check my site now"
button. For a client it:
  1. re-scrapes the live site for its current signals (pages, offerings, headings, nav),
  2. evaluates the client's still-pending, on-site-checkable milestone tasks against them,
  3. CONFIRMS each candidate page actually resolves (a sitemap entry can point at a 404),
  4. flips what's now present to ``verified_completed``, and
  5. lets the repo re-derive each milestone's status — which moves the dashboard's
     progress bar without the owner touching anything.

Two honesty rules are enforced here, because both were previously violated:

  * **Baseline.** The plan is generated crawl-free, so it recommends pages the client may
    already have. The FIRST run for a client is therefore a baseline: whatever is already
    live is recorded as ``status_source='baseline'`` and reported as ``already_live``,
    never as ``newly_verified``. Only artifacts appearing after that earn credit.
  * **Reachability.** A blocked or unreachable site returns ``site_reachable=False`` rather
    than a cheerful "nothing new is live yet" — the caller must be able to tell a silent
    failure from a real absence of work.

Thin orchestration only: the matching logic lives in :mod:`aeo.intelligence.milestone_verify`
(pure) and the persistence in :mod:`aeo.storage.repos.milestones`. Best-effort and
isolated — verification is bookkeeping layered on the crawl and must never abort it.
"""

from __future__ import annotations

from typing import Any

from ..intelligence.milestone_verify import (
    FetchText,
    confirm_slugs,
    evaluate,
    gather_site_signals,
    page_candidates,
    path_slug,
)
from ..logging import get_logger
from ..settings import get_settings
from ..storage.repos import milestones as milestones_repo

log = get_logger(__name__)


def _summary(
    *,
    checked: int = 0,
    newly_verified: int = 0,
    already_live: int = 0,
    verified_keys: list[str] | None = None,
    site_reachable: bool = True,
    site_blocked: bool = False,
    pages_fetched: int = 0,
    baselined: bool = False,
    skipped: str | None = None,
) -> dict[str, Any]:
    """The verification summary contract shared by every return path below."""
    return {
        "checked": checked,
        "newly_verified": newly_verified,
        # Tasks found already satisfied when we first looked. Real, but not the client's
        # doing this session — the UI labels them "already in place".
        "already_live": already_live,
        "verified_keys": verified_keys or [],
        # False → we could not read the site; "nothing new" would be a lie.
        "site_reachable": site_reachable,
        "site_blocked": site_blocked,
        "pages_fetched": pages_fetched,
        "baselined": baselined,
        # Set when we returned without crawling ('nothing_pending' | 'disabled').
        "skipped": skipped,
    }


async def verify_client_milestones(
    client_id: int,
    domain: str,
    *,
    run_id: int | None = None,
    discovered_slugs: list[str] | None = None,
    fetch: FetchText | None = None,
) -> dict[str, Any]:
    """Re-crawl ``domain`` and auto-verify this client's pending milestone tasks.

    Returns the summary described in :func:`_summary`. A client with no pending verifiable
    tasks (or a deployment with verification disabled) short-circuits WITHOUT crawling —
    callers must check :func:`should_verify` before paying for discovery. ``discovered_slugs``
    (e.g. the audit run's full URL inventory) is folded into the signals so deep pages count.
    """
    if not get_settings().milestones.verify_on_crawl:
        return _summary(skipped="disabled")

    pending = milestones_repo.pending_verifiable(client_id)
    if not pending:
        log.info("milestone_verify_nothing_pending", client_id=client_id, domain=domain)
        return _summary(skipped="nothing_pending")

    signals = await gather_site_signals(domain, fetch=fetch, discovered_slugs=discovered_slugs)

    # An unreadable site cannot prove anything is live. Flipping nothing is correct; saying
    # "nothing new is live yet" is not, so surface the reachability instead of a bare zero.
    if not signals.reachable:
        log.warning(
            "milestone_verify_site_unreachable",
            client_id=client_id, domain=domain, blocked=signals.blocked,
        )
        return _summary(
            checked=len(pending), site_reachable=False,
            site_blocked=signals.blocked, pages_fetched=signals.pages_fetched,
        )

    # Candidate page slugs come from things the site merely REFERENCES (sitemap entries,
    # nav hrefs). Confirm each actually resolves before crediting it.
    candidates = page_candidates(pending, signals)

    # Discovery is lossy in exactly the way that matters here: the sitemap reader keeps the
    # first 200 URLs in document order (WordPress appends new posts LAST) and the BFS
    # fallback keeps the top 200 by inbound-link count — both preferentially drop the
    # brand-new, weakly-linked page the user just published, which is the whole point of
    # the check. But we already know the exact URL the plan asked for, so probe it directly
    # instead of hoping a crawl surfaces it. This also rescues JS-rendered sites, where the
    # page serves fine at its own URL even though nothing links to it in the raw HTML.
    for task in pending:
        key = str(task.get("task_key") or "")
        target = str(task.get("verify_target") or "")
        if str(task.get("verify_kind") or "") == "page" and key and target and key not in candidates:
            candidates[key] = path_slug(target)

    confirmed = await confirm_slugs(set(candidates.values()), domain=domain, fetch=fetch)
    confirmed_pages = {key for key, slug in candidates.items() if slug in confirmed}
    # Non-page kinds (service / heading) matched live text, so they need no URL confirmation.
    non_page = set(evaluate(pending, signals)) - set(candidates)
    verified_keys = sorted(confirmed_pages | non_page)

    is_baseline = not milestones_repo.is_baselined(client_id)
    source = milestones_repo.SOURCE_BASELINE if is_baseline else milestones_repo.SOURCE_CRAWL
    flipped = (
        milestones_repo.mark_verified(client_id, verified_keys, run_id, source=source)
        if verified_keys
        else 0
    )
    if is_baseline:
        # Stamp the baseline even when nothing matched — the site HAS been looked at, so the
        # next run is a real comparison and any future match is genuinely new work.
        milestones_repo.mark_baselined(client_id)

    log.info(
        "milestone_verify_complete",
        client_id=client_id, domain=domain, checked=len(pending),
        flipped=flipped, baseline=is_baseline, pages_fetched=signals.pages_fetched,
        candidates=len(candidates), confirmed=len(confirmed),
    )
    return _summary(
        checked=len(pending),
        newly_verified=0 if is_baseline else flipped,
        already_live=flipped if is_baseline else 0,
        verified_keys=verified_keys,
        pages_fetched=signals.pages_fetched,
        baselined=is_baseline,
    )


def should_verify(client_id: int) -> bool:
    """Whether a verification run would do any work — checked BEFORE the caller pays for
    site discovery. Previously the on-demand endpoint ran a full crawl and only then
    discovered there was nothing pending (or that verification was switched off), so users
    waited through a real crawl to be told "nothing new is live yet"."""
    if not get_settings().milestones.verify_on_crawl:
        return False
    return bool(milestones_repo.pending_verifiable(client_id))
