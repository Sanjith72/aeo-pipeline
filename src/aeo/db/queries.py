from __future__ import annotations
import json
from uuid import UUID

import asyncpg

from aeo.models.blueprint import Blueprint, CoverageDiff, CrawledPage, Recommendation, ValidationResult


# ── content-hash gate ─────────────────────────────────────────────────────────

async def get_content_hashes(pool: asyncpg.Pool, domain: str) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT url, hash FROM content_hashes WHERE url LIKE $1",
        f"%{domain}%",
    )
    return {row["url"]: row["hash"] for row in rows}


async def upsert_content_hash(pool: asyncpg.Pool, url: str, hash_: str) -> None:
    await pool.execute(
        """
        INSERT INTO content_hashes (url, hash, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (url) DO UPDATE SET hash = EXCLUDED.hash, updated_at = NOW()
        """,
        url, hash_,
    )


# ── runs ──────────────────────────────────────────────────────────────────────

async def insert_run(pool: asyncpg.Pool, domain: str) -> UUID:
    row = await pool.fetchrow(
        "INSERT INTO runs (domain) VALUES ($1) RETURNING id",
        domain,
    )
    assert row is not None
    return row["id"]  # asyncpg returns uuid.UUID for UUID columns


async def finish_run(
    pool: asyncpg.Pool,
    run_id: UUID,
    pages_crawled: int,
    pages_changed: int,
    status: str = "completed",
    error: str | None = None,
) -> None:
    await pool.execute(
        """
        UPDATE runs
        SET finished_at = NOW(), status = $2,
            pages_crawled = $3, pages_changed = $4, error = $5
        WHERE id = $1
        """,
        run_id, status, pages_crawled, pages_changed, error,
    )


# ── blueprints ────────────────────────────────────────────────────────────────

async def upsert_blueprint(pool: asyncpg.Pool, blueprint: Blueprint) -> None:
    # 30-day lock window: an existing (domain, version) row is only overwritten once its
    # locked_until has passed. Inside the window the WHERE guard makes the UPDATE a no-op,
    # so a blueprint's persisted core stays immutable until the lock expires. engine_target,
    # locked_until and content_hash (computed fields) are denormalised into columns for
    # querying; the full model still lives in `data`.
    await pool.execute(
        """
        INSERT INTO blueprints
            (id, domain, version, data, created_at, engine_target, locked_until, content_hash)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        ON CONFLICT (domain, version) DO UPDATE
            SET data = EXCLUDED.data,
                engine_target = EXCLUDED.engine_target,
                locked_until = EXCLUDED.locked_until,
                content_hash = EXCLUDED.content_hash
            WHERE blueprints.locked_until IS NULL OR blueprints.locked_until < NOW()
        """,
        blueprint.id,
        blueprint.domain,
        blueprint.version,
        blueprint.model_dump_json(),
        blueprint.created_at,
        str(blueprint.engine_target),
        blueprint.locked_until,
        blueprint.content_hash,
    )


async def get_latest_blueprint(pool: asyncpg.Pool, domain: str) -> Blueprint | None:
    row = await pool.fetchrow(
        "SELECT data FROM blueprints WHERE domain = $1 ORDER BY version DESC LIMIT 1",
        domain,
    )
    if row is None:
        return None
    return Blueprint.model_validate_json(row["data"])


# ── pages ─────────────────────────────────────────────────────────────────────

async def insert_page(pool: asyncpg.Pool, run_id: UUID, page: CrawledPage) -> UUID:
    row = await pool.fetchrow(
        """
        INSERT INTO pages
            (run_id, url, content_hash, title, headings, body_text, links, schema_markup, crawled_at, changed)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8::jsonb, $9, $10)
        RETURNING id
        """,
        run_id,
        page.url,
        page.content_hash,
        page.title,
        json.dumps(page.headings),
        page.body_text,
        json.dumps(page.links),
        json.dumps(page.schema_markup),
        page.crawled_at,
        page.changed,
    )
    assert row is not None
    return row["id"]


# ── coverage reports ──────────────────────────────────────────────────────────

async def insert_coverage_report(
    pool: asyncpg.Pool,
    run_id: UUID,
    page_id: UUID,
    blueprint_id: UUID,
    diff: CoverageDiff,
) -> None:
    await pool.execute(
        """
        INSERT INTO coverage_reports (run_id, page_id, blueprint_id, data, coverage_score, generated_at)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        run_id, page_id, blueprint_id,
        diff.model_dump_json(),
        diff.coverage_score,
        diff.generated_at,
    )


# ── recommendations ───────────────────────────────────────────────────────────

async def insert_recommendation(pool: asyncpg.Pool, run_id: UUID, rec: Recommendation) -> None:
    await pool.execute(
        """
        INSERT INTO recommendations
            (id, run_id, domain, url, type, priority, title, description, implementation,
             impact_estimate, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        rec.id, run_id, rec.domain, rec.url,
        rec.type, rec.priority, rec.title,
        rec.description, rec.implementation,
        rec.impact_estimate, rec.created_at,
    )


async def insert_validation_result(pool: asyncpg.Pool, result: ValidationResult) -> None:
    await pool.execute(
        """
        INSERT INTO validation_results
            (id, recommendation_id, is_valid, confidence, reasoning,
             word_count_ok, h1_question_ok, json_ld_ok, validated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        result.id, result.recommendation_id,
        result.is_valid, result.confidence, result.reasoning,
        result.word_count_ok, result.h1_question_ok, result.json_ld_ok,
        result.validated_at,
    )


# ── reporting ─────────────────────────────────────────────────────────────────

async def get_run_recommendations(
    pool: asyncpg.Pool, domain: str, limit: int = 50
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT r.title, r.type, r.priority, r.url, r.impact_estimate,
               vr.is_valid, vr.confidence, vr.reasoning,
               vr.word_count_ok, vr.h1_question_ok, vr.json_ld_ok
        FROM recommendations r
        LEFT JOIN validation_results vr ON vr.recommendation_id = r.id
        WHERE r.domain = $1
        ORDER BY r.created_at DESC, r.impact_estimate DESC
        LIMIT $2
        """,
        domain, limit,
    )
    return [dict(row) for row in rows]
