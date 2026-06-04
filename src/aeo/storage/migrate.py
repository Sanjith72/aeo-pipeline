"""
Tiny migration runner. Applies *.sql files from storage/migrations/ in
filename order, recording successes in schema_versions.

Idempotent: re-running only applies new files.
"""

from __future__ import annotations

from pathlib import Path

from ..logging import get_logger
from .db import transaction

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version     VARCHAR(20)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""


def _applied_versions() -> set[str]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(_BOOTSTRAP_SQL)
        cur.execute("SELECT version FROM schema_versions")
        return {row["version"] for row in cur.fetchall()}


def _discover() -> list[tuple[str, str, Path]]:
    """Return [(version, name, path)] sorted by version (filename prefix)."""
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version, _, name = p.stem.partition("_")
        if not version.isdigit():
            log.warning("migration_skipped", reason="non-numeric prefix", file=p.name)
            continue
        out.append((version, name or p.stem, p))
    return out


def apply_pending() -> list[str]:
    applied = _applied_versions()
    pending = [(v, n, p) for v, n, p in _discover() if v not in applied]

    if not pending:
        log.info("migrations_up_to_date", applied=len(applied))
        return []

    done = []
    for version, name, path in pending:
        sql = path.read_text(encoding="utf-8")
        with transaction() as conn, conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_versions (version, name) VALUES (%s, %s)",
                (version, name),
            )
        log.info("migration_applied", version=version, name=name)
        done.append(version)

    return done
