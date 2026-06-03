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
You are an Answer Engine Optimization specialist. Produce a JSON blueprint for "{domain}".
Use only real, specific names from "{domain}"s actual industry. No placeholder names.
Return a single JSON object with these exact keys:
- target_queries: list of 5-10 real questions users ask about {domain}
- required_entities: list of real product/company/standard names relevant to {domain}
- schema_types: list of schema.org types (e.g. FAQPage, HowTo, Article)
- content_sections: list of objects with keys heading, type, required_entities, min_words
- citation_sources: list of real authoritative URLs relevant to {domain}
- freshness_days: integer (7){seed_block}"""


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
            seed_block = f"\nSeed queries: {', '.join(seed_queries)}"

        prompt = _PROMPT_TEMPLATE.format(domain=domain, seed_block=seed_block)
        logger.info("generating_blueprint", domain=domain)

        # Ollama -- IPv4-bound, consistent with the rest of the pipeline (OCI ARM requirement)
        # format="json" forces the model to emit valid JSON regardless of model quirks
        async with httpx.AsyncClient(
            timeout=settings.ollama_timeout,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        ) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
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
        try:
            data: dict[str, Any] = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("blueprint_json_error", domain=domain, error=str(exc), raw=raw[:300])
            raise ValueError(f"Ollama returned invalid JSON for domain={domain!r}: {exc}") from exc

        # engine_target, taxonomy_tags and competitor_intel are caller-supplied --
        # the model must never invent taxonomy categories.
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
