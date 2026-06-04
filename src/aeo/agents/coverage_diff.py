"""Gap analysis — Ollama phi3 for semantic query coverage; deterministic for entities/schema.

Block 3 adds a 4-track parallel evaluator (:func:`evaluate_gaps`) on top of the original
per-page :func:`compute_coverage_diff`. The four tracks fan out via ``asyncio.gather`` and
each is a standalone ``async def`` with no shared mutable state.

NOTE (conflict with task spec): the v4 brief asks for the Gemini API key here. This codebase
deliberately reserves Gemini for the Reference Architecture Generator and runs all secondary
reasoning on local Ollama (see config.py / architecture v4). That existing pattern is preserved;
the Gemini key is still loadable from .env via Settings but is not called from this module.
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import (
    Blueprint,
    CoveredEntity,
    CoveredQuery,
    CoverageDiff,
    CrawledPage,
    EngineTarget,
    GapAnalysisReport,
    GapTrackResult,
)
from aeo.utils.http import async_client
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
    async with async_client(timeout=settings.ollama_timeout) as client:
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


# ── Block 3: 4-track parallel gap evaluator ────────────────────────────────────

# engine_target → emphasis injected into the content-gap LLM prompt.
_ENGINE_EMPHASIS: dict[EngineTarget, str] = {
    EngineTarget.PERPLEXITY: (
        "Weight CITATION DENSITY heavily: a query is only 'covered' if the page answers it "
        "with specific, sourceable claims that Perplexity could cite."
    ),
    EngineTarget.CHATGPT_SEARCH: (
        "Weight CONVERSATIONAL COVERAGE: a query is 'covered' if the page answers it in a "
        "direct, natural-language way a ChatGPT Search summary would reuse."
    ),
    EngineTarget.GEMINI: (
        "Weight STRUCTURED ENTITY coverage: a query is 'covered' if the relevant entities and "
        "their relationships are explicitly present and well-structured."
    ),
    EngineTarget.GENERIC: "Assess whether the page substantively answers each query.",
}


def _select_emphasis(engine_target: EngineTarget) -> str:
    """Dynamically select the prompt emphasis for an engine target (default = generic)."""
    return _ENGINE_EMPHASIS.get(engine_target, _ENGINE_EMPHASIS[EngineTarget.GENERIC])


def _extract_dates(schema_markup: list[dict]) -> list[str]:
    dates: list[str] = []
    for item in schema_markup:
        for node in [item, *item.get("@graph", [])]:
            for key in ("datePublished", "dateModified"):
                val = node.get(key)
                if isinstance(val, str):
                    dates.append(val)
    return dates


# Each track below is a standalone async def with NO shared mutable state — it reads only
# its arguments and returns its own GapTrackResult. This keeps the asyncio.gather fan-out safe.


async def _content_gap_track(
    page: CrawledPage, blueprint: Blueprint, engine_target: EngineTarget, settings: Any
) -> GapTrackResult:
    """Track 1 — semantic query coverage via Ollama, with engine-routed emphasis."""
    emphasis = _select_emphasis(engine_target)
    queries_block = "\n".join(f"{i+1}. {q}" for i, q in enumerate(blueprint.target_queries))
    prompt = (
        f"{emphasis}\n\n"
        + _COVERAGE_PROMPT.format(
            title=page.title, excerpt=page.body_text[:2000], queries=queries_block
        )
    )
    covered: list[dict[str, Any]] = []
    try:
        async with async_client(timeout=settings.ollama_timeout) as client:
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
            content = resp.json()["message"]["content"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            covered = json.loads(match.group()).get("coverage", [])
    except Exception as exc:  # noqa: BLE001 — track degrades gracefully, never crashes the fan-out
        logger.warning("content_gap_track_failed", url=page.url, error=str(exc))

    covered_map = {c.get("query", ""): bool(c.get("covered")) for c in covered}
    total = len(blueprint.target_queries) or 1
    hit = sum(1 for q in blueprint.target_queries if covered_map.get(q))
    missing = [q for q in blueprint.target_queries if not covered_map.get(q)]
    return GapTrackResult(
        track="content_gap",
        engine_target=engine_target,
        score=round(hit / total, 4),
        findings=[f"uncovered query: {q}" for q in missing[:10]],
        details={"covered": hit, "total": total},
    )


async def _citation_gap_track(
    page: CrawledPage, blueprint: Blueprint, engine_target: EngineTarget, settings: Any
) -> GapTrackResult:
    """Track 2 — deterministic: are the blueprint's authoritative citation sources present?"""
    haystack = f"{page.body_text} {' '.join(page.links)}".lower()
    sources = blueprint.citation_sources
    present = [s for s in sources if s.lower() in haystack]
    missing = [s for s in sources if s.lower() not in haystack]
    score = 1.0 if not sources else round(len(present) / len(sources), 4)
    return GapTrackResult(
        track="citation_gap",
        engine_target=engine_target,
        score=score,
        findings=[f"missing citation source: {s}" for s in missing[:10]],
        details={"present": len(present), "expected": len(sources)},
    )


async def _freshness_gap_track(
    page: CrawledPage, blueprint: Blueprint, engine_target: EngineTarget, settings: Any
) -> GapTrackResult:
    """Track 3 — deterministic: does the page carry a recent published/modified date?"""
    dates = _extract_dates(page.schema_markup)
    score = 0.0
    findings: list[str] = []
    if not dates:
        findings.append("no datePublished/dateModified in schema markup")
    else:
        newest: datetime | None = None
        for raw in dates:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if newest is None or parsed > newest:
                newest = parsed
        if newest is None:
            findings.append("date fields present but unparseable")
            score = 0.25
        else:
            age_days = (datetime.utcnow() - newest).days
            score = 1.0 if age_days <= blueprint.freshness_days else 0.5
            if score < 1.0:
                findings.append(f"content is {age_days}d old (> {blueprint.freshness_days}d window)")
    return GapTrackResult(
        track="freshness_gap",
        engine_target=engine_target,
        score=score,
        findings=findings,
        details={"dates_found": len(dates)},
    )


async def _entity_coverage_gap_track(
    page: CrawledPage, blueprint: Blueprint, engine_target: EngineTarget, settings: Any
) -> GapTrackResult:
    """Track 4 — deterministic: are the blueprint's required entities mentioned on the page?"""
    combined = f"{page.title} {' '.join(page.headings)} {page.body_text}"
    coverage = [_entity_coverage(e, combined) for e in blueprint.required_entities]
    total = len(coverage) or 1
    present = sum(1 for c in coverage if c.present)
    missing = [c.entity for c in coverage if not c.present]
    return GapTrackResult(
        track="entity_coverage_gap",
        engine_target=engine_target,
        score=round(present / total, 4),
        findings=[f"missing entity: {e}" for e in missing[:10]],
        details={"present": present, "total": total},
    )


async def evaluate_gaps(
    page: CrawledPage,
    blueprint: Blueprint,
    engine_target: EngineTarget | None = None,
) -> GapAnalysisReport:
    """Run all four gap-analysis tracks in parallel and merge into one report.

    ``engine_target`` defaults to the blueprint's own target. The four tracks are launched
    concurrently with ``asyncio.gather``; a track that raises degrades to a zero-score result
    rather than failing the whole evaluation.
    """
    settings = get_settings()
    target = engine_target or blueprint.engine_target

    with tracer.start_as_current_span("gap_analysis.evaluate") as span:
        span.set_attribute("url", page.url)
        span.set_attribute("engine_target", str(target))

        tracks = (
            _content_gap_track,
            _citation_gap_track,
            _freshness_gap_track,
            _entity_coverage_gap_track,
        )
        results = await asyncio.gather(
            *(fn(page, blueprint, target, settings) for fn in tracks),
            return_exceptions=True,
        )

        merged: list[GapTrackResult] = []
        for fn, res in zip(tracks, results, strict=True):
            if isinstance(res, BaseException):
                logger.error("gap_track_error", track=fn.__name__, error=str(res))
                merged.append(
                    GapTrackResult(
                        track=fn.__name__.strip("_").removesuffix("_track"),  # type: ignore[arg-type]
                        engine_target=target,
                        score=0.0,
                        findings=[f"track errored: {res}"],
                    )
                )
            else:
                merged.append(res)

        aggregate = round(sum(t.score for t in merged) / len(merged), 4) if merged else 0.0
        report = GapAnalysisReport(
            url=page.url,
            domain=blueprint.domain,
            blueprint_id=blueprint.id,
            engine_target=target,
            tracks=merged,
            aggregate_score=aggregate,
        )
        logger.info(
            "gap_analysis_done", url=page.url, engine_target=str(target), score=aggregate
        )
        span.set_attribute("aggregate_score", aggregate)
        return report
