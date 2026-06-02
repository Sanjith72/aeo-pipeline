from __future__ import annotations
import asyncpg


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    if pool is None:
        raise RuntimeError("Failed to create connection pool")
    return pool
