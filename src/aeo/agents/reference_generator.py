"""Reference Architecture Generator -- uses local Ollama to produce a Blueprint."""
from __future__ import annotations
import json
import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import (
    Blueprint,
    CompetitorIntel,
    ContentSection,
    EngineTarget,
    TaxonomyTag,
)
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_PROMPT_TEMPLATE = """\
You are an Answer Engine Optimization (AEO) specialist.
Produce a reference blueprint for what content on the domain "{domain}" must contain to be \
cited by AI answer engines (Perplexity, ChatGPT, Gemini).{seed_block}

Respond with ONLY valid JSON -- no markdown fences, no explanation -- matching this schema:
{{
  "target_queries": ["10-20 queries this domain's content should answer"],
  "required_entities": ["people, products, companies, concepts that must be present"],
  "schema_types": ["schema.org types, e.g. FAQPage, HowTo, Article, Product"],
  "content_sections": [
    {{
      "heading": "section heading",
      "type": "faq|howto|definition|comparison|listicle|narrative",
      "required_entities": ["entities for this section"],
      "min_words": 150
    }}
  ],
  "citation_sources": ["authoritative URLs to cite"],
  "freshness_days": 7
}}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def generate_blueprint(
    domain: str,
    seed_queries: list[str] | None = None,
    engine_target: EngineTarget = EngineTarget.GENERIC,
    taxonomy_tags: list[TaxonomyTag] | None = None,
    competitor_intel: list[CompetitorIntel] | None = None,
) -> Blueprint:
    with tracer.start_as_current_span("reference_generator.generate") as span:
        span.set_attribute("domain", domain)
        span.set_attribute("engine_target", str(engine_target))
        settings = get_settings()

        seed_block = ""
        if seed_queries:
            seed_block = f"\n\nSeed queries: {', '.join(seed_queries)}"

        prompt = _PROMPT_TEMPLATE.format(domain=domain, seed_block=seed_block)
        logger.info("generating_blueprint", domain=domain)

        # Ollama -- IPv4-bound, consistent with the rest of the pipeline (OCI ARM requirement)
        async with httpx.AsyncClient(
            timeout=120.0,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        ) as client:
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
            raw: str = resp.json()["message"]["content"].strip()

        # Strip markdown code fences if the model wraps its output
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Extract first JSON object in case the model emits extra prose
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"Ollama returned no JSON object for domain={domain!r}")
        data: dict[str, Any] = json.loads(match.group())

        # engine_target, taxonomy_tags and competitor_intel are caller-supplied --
        # the model must never invent taxonomy categories.
        # created_at + locked_until (+30d) + content_hash are derived on the Pydantic model.
        # Core structural fields are frozen once constructed.
        blueprint = Blueprint(
            domain=domain,
            engine_target=engine_target,
            taxonomy_tags=taxonomy_tags or [],
            target_queries=data.get("target_queries", []),
            required_entities=data.get("required_entities", []),
            schema_types=data.get("schema_types", []),
            content_sections=[ContentSection(**s) for s in data.get("content_sections", [])],
            citation_sources=data.get("citation_sources", []),
            competitor_intel=competitor_intel or [],
            freshness_days=int(data.get("freshness_days", 7)),
        )
        logger.info(
            "blueprint_generated",
            domain=domain,
            queries=len(blueprint.target_queries),
            entities=len(blueprint.required_entities),
        )
        span.set_attribute("queries_count", len(blueprint.target_queries))
        return blueprint
