"""
Weekly milestone verification — the automated "did they do it?" crawl.

Runs on the same weekly cadence as the audit cycle (ops/). For a client it:
  1. re-scrapes the live site for its current signals (pages, offerings, headings, nav),
  2. evaluates the client's still-pending, on-site-checkable milestone tasks against them,
  3. flips any now-present task to ``verified_completed`` (status_source='crawl'),
  4. lets the repo re-derive each milestone's status — which moves the dashboard's
     progress bar without the owner touching anything.

Thin orchestration only: the matching logic lives in :func:`milestone_verify.evaluate`
(pure) and the persistence in :mod:`aeo.storage.repos.milestones`. Best-effort and
isolated — verification is bookkeeping layered on the crawl and must never abort it.
"""

from __future__ import annotations

from typing import Any

from ..intelligence.milestone_verify import FetchText, gather_site_signals
from ..logging import get_logger
from ..settings import get_settings
from ..storage.repos import milestones as milestones_repo

log = get_logger(__name__)


async def verify_client_milestones(
    client_id: int,
    domain: str,
    *,
    run_id: int | None = None,
    discovered_slugs: list[str] | None = None,
    fetch: FetchText | None = None,
) -> dict[str, Any]:
    """Re-crawl ``domain`` and auto-verify this client's pending milestone tasks.

    Returns a summary {checked, newly_verified, verified_keys}. A client with no
    pending verifiable tasks short-circuits without a crawl. ``discovered_slugs`` (e.g.
    the audit run's full URL inventory) is folded into the signals so deep pages count."""
    if not get_settings().milestones.verify_on_crawl:
        return {"checked": 0, "newly_verified": 0, "verified_keys": []}

    pending = milestones_repo.pending_verifiable(client_id)
    if not pending:
        log.info("milestone_verify_nothing_pending", client_id=client_id, domain=domain)
        return {"checked": 0, "newly_verified": 0, "verified_keys": []}

    signals = await gather_site_signals(domain, fetch=fetch, discovered_slugs=discovered_slugs)
    from ..intelligence.milestone_verify import evaluate

    verified_keys = sorted(evaluate(pending, signals))
    flipped = milestones_repo.mark_verified(client_id, verified_keys, run_id) if verified_keys else 0
    log.info(
        "milestone_verify_complete", client_id=client_id, domain=domain,
        checked=len(pending), newly_verified=flipped,
    )
    return {"checked": len(pending), "newly_verified": flipped, "verified_keys": verified_keys}
