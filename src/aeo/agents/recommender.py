"""Recommender — Ollama phi3 generates rich, actionable recommendations from coverage gaps."""
from __future__ import annotations
import json
import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import Blueprint, CoverageDiff, Recommendation
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_REC_PROMPT = """\
You are an AEO (Answer Engine Optimization) consultant.
Given the coverage gap analysis below, generate 3-5 specific, actionable recommendations.

Domain: {domain}
Page URL: {url}
Coverage score: {score:.0%}
Uncovered queries: {uncovered}
Missing entities: {missing_entities}
Missing schema types: {missing_schema}

Each recommendation should be precise enough to hand directly to a content writer or developer.
Respond with JSON only:
{{
  "recommendations": [
    {{
      "type": "content|schema|entity|citation",
      "priority": "high|medium|low",
      "title": "short action title",
      "description": "why this matters for AEO",
      "implementation": "step-by-step implementation guidance",
      "impact_estimate": 0.0
    }}
  ]
}}
"""


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
async def _ollama_recommend(diff: CoverageDiff, settings) -> list[dict[str, Any]]:
    uncovered = [q.query for q in diff.query_coverage if not q.covered][:8]
    missing_entities = [e.entity for e in diff.entity_coverage if not e.present][:8]

    prompt = _REC_PROMPT.format(
        domain=diff.domain,
        url=diff.url,
        score=diff.coverage_score,
        uncovered=", ".join(uncovered) or "none",
        missing_entities=", ".join(missing_entities) or "none",
        missing_schema=", ".join(diff.schema_types_missing) or "none",
    )

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        resp.raise_for_status()
        content: str = resp.json()["message"]["content"]

    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group()).get("recommendations", [])
    return []


def _coerce_recommendation(raw: dict, domain: str, url: str) -> Recommendation | None:
    try:
        return Recommendation(
            domain=domain,
            url=url,
            type=raw.get("type", "content"),
            priority=raw.get("priority", "medium"),
            title=str(raw.get("title", ""))[:200],
            description=str(raw.get("description", ""))[:1000],
            implementation=str(raw.get("implementation", ""))[:2000],
            impact_estimate=min(1.0, max(0.0, float(raw.get("impact_estimate", 0.1)))),
        )
    except Exception:
        return None


async def generate_recommendations(
    diffs: list[CoverageDiff],
    blueprint: Blueprint,
) -> list[Recommendation]:
    with tracer.start_as_current_span("recommender.generate") as span:
        span.set_attribute("domain", blueprint.domain)
        settings = get_settings()
        all_recs: list[Recommendation] = []

        for diff in diffs:
            if diff.coverage_score >= 0.9:
                continue  # skip well-covered pages
            try:
                raw_recs = await _ollama_recommend(diff, settings)
                for raw in raw_recs:
                    rec = _coerce_recommendation(raw, diff.domain, diff.url)
                    if rec:
                        all_recs.append(rec)
            except Exception as exc:
                logger.warning("recommend_failed", url=diff.url, error=str(exc))

        # Sort: high priority first, then by impact descending
        priority_order = {"high": 0, "medium": 1, "low": 2}
        all_recs.sort(key=lambda r: (priority_order[r.priority], -r.impact_estimate))

        logger.info("recommendations_ready", domain=blueprint.domain, count=len(all_recs))
        span.set_attribute("recommendations_count", len(all_recs))
        return all_recs
