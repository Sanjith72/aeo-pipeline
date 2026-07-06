"""content_embeddings — the pgvector store behind semantic search (migration 0025).

Graceful-degradation contract: migration 0025 only creates the table when the server
has pgvector, so every entry point here first checks :func:`available`. Callers (the
agent's ``semantic_search`` tool, the indexing path) treat ``available() == False`` as
"semantic search disabled" and fall back to keyword lookups — never an error.

Vectors travel as pgvector text literals (``[0.1,0.2,…]``) cast with ``::vector``;
similarity is cosine (``<=>`` returns distance, so similarity = 1 - distance).
"""

from __future__ import annotations

from typing import Any, cast

from ..db import transaction

# Must match vector(768) in migration 0025 — Gemini gemini-embedding-001 at
# output_dimensionality=768 (also text-embedding-004's native size).
EMBEDDING_DIM = 768


def _literal(embedding: list[float]) -> str:
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"embedding must have {EMBEDDING_DIM} dims (got {len(embedding)})")
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def available() -> bool:
    """True when the content_embeddings table exists (pgvector was present at migrate
    time). One catalog lookup — cheap enough for the rare embeddings call paths."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.content_embeddings') AS t")
        row = cast(dict[str, Any], cur.fetchone())  # RealDictCursor → dict rows
        return row["t"] is not None


def upsert(
    url: str,
    embedding: list[float],
    *,
    content_sha256: str,
    model: str,
    kind: str = "page",
    chunk_ix: int = 0,
    page_id: int | None = None,
) -> int:
    """Insert or refresh the embedding for one (url, kind, chunk_ix) slot. Returns its id."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO content_embeddings
                (page_id, url, kind, chunk_ix, content_sha256, model, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (url, kind, chunk_ix) DO UPDATE SET
                page_id        = EXCLUDED.page_id,
                content_sha256 = EXCLUDED.content_sha256,
                model          = EXCLUDED.model,
                embedding      = EXCLUDED.embedding,
                created_at     = NOW()
            RETURNING id
            """,
            (page_id, url, kind, chunk_ix, content_sha256, model, _literal(embedding)),
        )
        row = cast(dict[str, Any], cur.fetchone())  # RealDictCursor → dict rows
        return int(row["id"])


def search(
    embedding: list[float],
    *,
    kind: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Nearest neighbours by cosine similarity, best first.

    Returns ``[{url, kind, chunk_ix, page_id, similarity}, …]`` — ``similarity`` is in
    [-1, 1] with 1 = identical direction."""
    literal = _literal(embedding)
    clauses: list[str] = []
    params: list[Any] = [literal]
    if kind is not None:
        clauses.append("kind = %s")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params += [literal, max(1, min(limit, 100))]
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT url, kind, chunk_ix, page_id,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM content_embeddings
            {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cast(list[dict[str, Any]], cur.fetchall())  # RealDictCursor → dict rows
        return [{**row, "similarity": float(row["similarity"])} for row in rows]


def is_current(url: str, content_sha256: str, *, kind: str = "page", chunk_ix: int = 0) -> bool:
    """True when the stored embedding for this slot was computed from this exact content
    (sha match) — lets the indexer skip re-embedding unchanged pages."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM content_embeddings
            WHERE url = %s AND kind = %s AND chunk_ix = %s AND content_sha256 = %s
            """,
            (url, kind, chunk_ix, content_sha256),
        )
        return cur.fetchone() is not None
