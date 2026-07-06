"""
PostgreSQL connection pool + transaction helper.

One pool per process. Threaded so async tasks running in default executor
get fresh connections without contention.
"""

from __future__ import annotations

import contextlib
import threading
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
_pool_lock = threading.Lock()


def _parse_url(url: str) -> dict[str, Any]:
    """Validate the URL scheme and return SAFE (credential-free) fields for logging.

    The URL itself is handed to libpq verbatim (see get_pool), so query params such as
    ``?sslmode=require`` — mandatory for Supabase/Neon — reach the driver instead of
    being stripped by re-assembly here.
    """
    p = urlparse(url)
    if p.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"DATABASE_URL must use postgresql:// — got {p.scheme!r}")
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "dbname": (p.path or "/aeo").lstrip("/") or "aeo",
    }


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    # Double-checked locking: without it, concurrent first callers (the API's thread
    # pool) would each build a pool and the losers' minconn connections leak for the
    # process lifetime — real money on Supabase's free-tier connection caps.
    with _pool_lock:
        if _pool is None:
            s = get_settings()
            safe = _parse_url(s.database.url)
            # Full URI as the libpq DSN: keeps sslmode/options/application_name query
            # params, which Supabase (Supavisor pooler) and Neon require. Keyword args
            # are merged over the DSN by psycopg2.
            kwargs: dict[str, Any] = {
                "cursor_factory": psycopg2.extras.RealDictCursor,
            }
            if "connect_timeout" not in s.database.url:
                kwargs["connect_timeout"] = 10
            _pool = psycopg2.pool.ThreadedConnectionPool(
                s.database.pool_min,
                s.database.pool_max,
                s.database.url,
                **kwargs,
            )
            log.info("db_pool_ready", host=safe["host"], port=safe["port"], db=safe["dbname"],
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
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            log.info("db_pool_closed")
