"""
PostgreSQL connection pool + transaction helper.

One pool per process. Threaded so async tasks running in default executor
get fresh connections without contention.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
import psycopg2.pool

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _parse_url(url: str) -> dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"DATABASE_URL must use postgresql:// — got {p.scheme!r}")
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "dbname": (p.path or "/aeo").lstrip("/") or "aeo",
        "user": p.username or "postgres",
        "password": p.password or "",
    }


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        s = get_settings()
        cfg = _parse_url(s.database.url)
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=s.database.pool_min,
            maxconn=s.database.pool_max,
            cursor_factory=psycopg2.extras.RealDictCursor,
            connect_timeout=10,
            **cfg,
        )
        log.info("db_pool_ready", host=cfg["host"], db=cfg["dbname"],
                 minconn=s.database.pool_min, maxconn=s.database.pool_max)
    return _pool


@contextlib.contextmanager
def transaction() -> Generator[psycopg2.extensions.connection, None, None]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def health_check() -> bool:
    try:
        with transaction() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception as exc:
        log.error("db_health_check_failed", error=str(exc))
        return False


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        log.info("db_pool_closed")
