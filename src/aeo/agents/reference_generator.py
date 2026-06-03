"""Reference Architecture Generator — uses Google Gemini (free tier) to produce a Blueprint."""
from __future__ import annotations
import json
from typing import Any

from google import genai
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

Respond with ONLY valid JSON — no markdown fences, no explanation — matching this schema exactly:
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
        client = genai.Client(api_key=settings.gemini_api_key)

        seed_block = ""
        if seed_queries:
            seed_block = f"\n\nSeed queries: {', '.join(seed_queries)}"

        prompt = _PROMPT_TEMPLATE.format(domain=domain, seed_block=seed_block)
        logger.info("generating_blueprint", domain=domain)

        response = await client.aio.models.generate_content(
            model=f"models/{settings.gemini_model}",
            contents=prompt,
        )
        raw: str = response.text.strip()

        # Strip markdown code fences if the model wraps its output anyway
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data: dict[str, Any] = json.loads(raw)
        # engine_target, taxonomy_tags and competitor_intel are caller-supplied (Layer 2
        # guardrail + Layer 1 empirical floor) — the generator never lets Gemini invent
        # taxonomy categories. created_at + locked_until (+30d) + content_hash are derived
        # on the model. Core structural fields are frozen once constructed.
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
