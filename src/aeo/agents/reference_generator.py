"""Reference Architecture Generator — uses Google Gemini (free tier) to produce a Blueprint."""
from __future__ import annotations
import json
from typing import Any

import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import Blueprint, ContentSection
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
async def generate_blueprint(domain: str, seed_queries: list[str] | None = None) -> Blueprint:
    with tracer.start_as_current_span("reference_generator.generate") as span:
        span.set_attribute("domain", domain)
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        seed_block = ""
        if seed_queries:
            seed_block = f"\n\nSeed queries: {', '.join(seed_queries)}"

        prompt = _PROMPT_TEMPLATE.format(domain=domain, seed_block=seed_block)
        logger.info("generating_blueprint", domain=domain)

        response = await model.generate_content_async(prompt)
        raw: str = response.text.strip()

        # Strip markdown code fences if the model wraps its output anyway
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data: dict[str, Any] = json.loads(raw)
        blueprint = Blueprint(
            domain=domain,
            target_queries=data.get("target_queries", []),
            required_entities=data.get("required_entities", []),
            schema_types=data.get("schema_types", []),
            content_sections=[ContentSection(**s) for s in data.get("content_sections", [])],
            citation_sources=data.get("citation_sources", []),
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
