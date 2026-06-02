"""Parallel processor — asyncio.gather across changed pages; Ollama triage for processing order."""
from __future__ import annotations
import asyncio
import json
import re
from uuid import UUID

import asyncpg
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.agents.coverage_diff import compute_coverage_diff
from aeo.agents.crawler import crawl_site
from aeo.config import get_settings
from aeo.db.queries import (
    get_content_hashes,
    insert_coverage_report,
    insert_page,
    upsert_content_hash,
)
from aeo.models.blueprint import Blueprint, CoverageDiff, CrawledPage
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_TRIAGE_PROMPT = """\
You are an SEO strategist. Given a list of crawled pages and their titles, \
rank the top {limit} URLs most likely to benefit from AEO optimization for the domain "{domain}".
Prioritise pages that serve informational queries, FAQs, how-to guides, or comparison content.

Pages:
{pages_block}

Respond with JSON only:
{{"ranked_urls": ["url1", "url2", ...]}}
"""


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def _ollama_triage(
    domain: str, pages: list[CrawledPage], limit: int, settings
) -> list[str]:
    pages_block = "\n".join(f"{i+1}. {p.url} — {p.title}" for i, p in enumerate(pages[:50]))
    prompt = _TRIAGE_PROMPT.format(domain=domain, limit=limit, pages_block=pages_block)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0},
            },
        )
        resp.raise_for_status()
        content: str = resp.json()["message"]["content"]

    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group()).get("ranked_urls", [])
    return [p.url for p in pages[:limit]]


async def process_domain(
    domain: str,
    start_url: str,
    blueprint: Blueprint,
    pool: asyncpg.Pool,
    run_id: UUID,
    triage_limit: int = 50,
) -> tuple[list[CoverageDiff], list[CrawledPage]]:
    with tracer.start_as_current_span("processor.process_domain") as span:
        span.set_attribute("domain", domain)
        settings = get_settings()

        known_hashes = await get_content_hashes(pool, domain)
        all_pages = await crawl_site(start_url, known_hashes)

        changed = [p for p in all_pages if p.changed]
        logger.info(
            "pages_crawled",
            domain=domain,
            total=len(all_pages),
            changed=len(changed),
        )

        # Use Ollama to rank changed pages by AEO relevance, then cap at triage_limit
        if len(changed) > triage_limit:
            try:
                ranked_urls = await _ollama_triage(domain, changed, triage_limit, settings)
                url_set = set(ranked_urls)
                changed = [p for p in changed if p.url in url_set] + [
                    p for p in changed if p.url not in url_set
                ]
                changed = changed[:triage_limit]
                logger.info("triage_applied", kept=len(changed))
            except Exception as exc:
                logger.warning("triage_failed", error=str(exc))
                changed = changed[:triage_limit]

        async def _process(page: CrawledPage) -> CoverageDiff:
            page_id = await insert_page(pool, run_id, page)
            await upsert_content_hash(pool, page.url, page.content_hash)
            diff = await compute_coverage_diff(page, blueprint)
            await insert_coverage_report(pool, run_id, page_id, blueprint.id, diff)
            return diff

        results = await asyncio.gather(*[_process(p) for p in changed], return_exceptions=True)

        diffs: list[CoverageDiff] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.error("page_error", error=str(r))
            else:
                diffs.append(r)

        span.set_attribute("diffs_computed", len(diffs))
        return diffs, changed
