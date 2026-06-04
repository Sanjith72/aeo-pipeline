"""
blueprints — the versioned per-topic ideal site (v4 Reference Architecture).

Owns the reuse-vs-bump versioning the product depends on: a blueprint whose
*inputs* are unchanged (same ``content_hash``) reuses its pinned version, so
week-over-week scores stay comparable; any structural change inserts the next
version. ``pin_run`` records which version a run was measured against, so a score
jump can be read as "new baseline" rather than "real change".

SQL only — round-trips :class:`~aeo.reference.blueprint.Blueprint` through the
``body`` JSONB column.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...reference.blueprint import Blueprint
from ..db import transaction


@dataclass(slots=True)
class StoredBlueprint:
    id: int
    blueprint: Blueprint
    reused: bool = False


def _row_to_stored(row: dict, *, reused: bool = False) -> StoredBlueprint:
    return StoredBlueprint(id=row["id"], blueprint=Blueprint.from_jsonb(row["body"]), reused=reused)


def save_versioned(blueprint: Blueprint) -> StoredBlueprint:
    """Persist a blueprint, reusing the existing version when its inputs are
    unchanged or inserting the next version otherwise. Idempotent per content hash.

    The reuse-vs-bump decision reads ``MAX(version)+1`` then INSERTs; under the
    threaded pool two concurrent same-topic generations would otherwise read the
    same max and collide on ``UNIQUE(topic, version)``. A per-topic advisory
    transaction lock (auto-released on commit/rollback) serializes the bump so the
    monotonic-version semantics hold without an error-and-retry path."""
    bp = blueprint.with_hash()
    with transaction() as conn, conn.cursor() as cur:
        # Serialize concurrent same-topic writers for the duration of this txn.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('aeo:blueprint:' || %s))", (bp.topic,))
        cur.execute(
            """
            SELECT id, body FROM blueprints
            WHERE topic = %s AND content_hash = %s
            ORDER BY version DESC LIMIT 1
            """,
            (bp.topic, bp.content_hash),
        )
        existing = cur.fetchone()
        if existing:
            return _row_to_stored(existing, reused=True)

        cur.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM blueprints WHERE topic = %s",
            (bp.topic,),
        )
        next_version = cur.fetchone()["next"]
        stored_bp = bp.model_copy(update={"version": next_version})

        cur.execute(
            """
            INSERT INTO blueprints (
                topic, version, generator, framework_version, content_hash, competitors, body, notes
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            RETURNING id
            """,
            (
                stored_bp.topic, stored_bp.version, stored_bp.generator,
                stored_bp.framework_version, stored_bp.content_hash,
                json.dumps(stored_bp.competitors), json.dumps(stored_bp.to_jsonb(), default=str),
                stored_bp.notes,
            ),
        )
        new_id = cur.fetchone()["id"]
    return StoredBlueprint(id=new_id, blueprint=stored_bp, reused=False)


def latest(topic: str) -> StoredBlueprint | None:
    """The highest-version blueprint for a topic, or None if none generated yet."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body FROM blueprints WHERE topic = %s ORDER BY version DESC LIMIT 1",
            (topic,),
        )
        row = cur.fetchone()
    return _row_to_stored(row) if row else None


def get(blueprint_id: int) -> StoredBlueprint | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, body FROM blueprints WHERE id = %s", (blueprint_id,))
        row = cur.fetchone()
    return _row_to_stored(row) if row else None


def by_version(topic: str, version: int) -> StoredBlueprint | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body FROM blueprints WHERE topic = %s AND version = %s",
            (topic, version),
        )
        row = cur.fetchone()
    return _row_to_stored(row) if row else None


def pin_run(run_id: int, blueprint_id: int) -> None:
    """Record which blueprint version a run was measured against."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE crawl_runs SET blueprint_id = %s WHERE id = %s",
            (blueprint_id, run_id),
        )
