"""Coverage diff — Ollama phi3 for semantic query coverage; deterministic for entities/schema."""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import Blueprint, CoveredEntity, CoveredQuery, CoverageDiff, CrawledPage
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_COVERAGE_PROMPT = """\
You are an SEO content analyst. Assess whether the webpage content answers each query below.

Page title: {title}
Content (excerpt): {excerpt}

Queries to evaluate:
{queries}

Respond with JSON only — no extra text:
{{"coverage": [{{"query": "...", "covered": true, "confidence": 0.9}}]}}
"""


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def _ollama_coverage(
    title: str, body_text: str, queries: list[str], settings
) -> list[dict[str, Any]]:
    queries_block = "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))
    prompt = _COVERAGE_PROMPT.format(
        title=title,
        excerpt=body_text[:2000],
        queries=queries_block,
    )
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
        data = json.loads(match.group())
        return data.get("coverage", [])
    return []


def _extract_schema_types(schema_markup: list[dict]) -> set[str]:
    types: set[str] = set()
    for item in schema_markup:
        for node in ([item] + item.get("@graph", [])):
            t = node.get("@type")
            if isinstance(t, list):
                types.update(t)
            elif t:
                types.add(t)
    return types


def _entity_coverage(entity: str, text: str) -> CoveredEntity:
    mentions = len(re.findall(re.escape(entity.lower()), text.lower()))
    return CoveredEntity(entity=entity, present=mentions > 0, mentions=mentions)


async def compute_coverage_diff(page: CrawledPage, blueprint: Blueprint) -> CoverageDiff:
    with tracer.start_as_current_span("coverage_diff.compute") as span:
        span.set_attribute("url", page.url)
        settings = get_settings()

        # --- semantic query coverage via Ollama ---
        raw_coverage: list[dict] = []
        try:
            raw_coverage = await _ollama_coverage(
                page.title, page.body_text, blueprint.target_queries, settings
            )
        except Exception as exc:
            logger.warning("ollama_coverage_failed", url=page.url, error=str(exc))

        query_map = {item.get("query", ""): item for item in raw_coverage}
        query_coverage: list[CoveredQuery] = []
        for q in blueprint.target_queries:
            item = query_map.get(q, {})
            query_coverage.append(
                CoveredQuery(
                    query=q,
                    covered=bool(item.get("covered", False)),
                    confidence=float(item.get("confidence", 0.0)),
                )
            )

        # --- deterministic entity coverage ---
        combined = f"{page.title} {' '.join(page.headings)} {page.body_text}"
        entity_coverage = [_entity_coverage(e, combined) for e in blueprint.required_entities]

        # --- deterministic schema detection ---
        schema_types_found = sorted(_extract_schema_types(page.schema_markup))
        schema_types_missing = [t for t in blueprint.schema_types if t not in schema_types_found]

        # weighted score: queries 50%, entities 30%, schema 20%
        q_score = sum(1 for q in query_coverage if q.covered) / max(len(query_coverage), 1)
        e_score = sum(1 for e in entity_coverage if e.present) / max(len(entity_coverage), 1)
        s_score = (
            len(schema_types_found) / len(blueprint.schema_types)
            if blueprint.schema_types
            else 1.0
        )
        coverage_score = round(q_score * 0.5 + e_score * 0.3 + s_score * 0.2, 4)

        diff = CoverageDiff(
            domain=blueprint.domain,
            blueprint_id=blueprint.id,
            url=page.url,
            query_coverage=query_coverage,
            entity_coverage=entity_coverage,
            schema_types_found=schema_types_found,
            schema_types_missing=schema_types_missing,
            coverage_score=coverage_score,
            generated_at=datetime.utcnow(),
        )
        logger.info("coverage_diff_computed", url=page.url, score=coverage_score)
        span.set_attribute("coverage_score", coverage_score)
        return diff
