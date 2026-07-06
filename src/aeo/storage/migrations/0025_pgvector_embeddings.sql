-- src/aeo/storage/migrations/0025_pgvector_embeddings.sql
-- Semantic-search substrate: pgvector + content_embeddings.
--
-- Guarded: on servers where the pgvector extension is not installed (e.g. a plain local
-- Postgres), this migration logs a NOTICE and creates nothing — the embeddings repo and
-- the agent's semantic_search tool detect the missing table and degrade to keyword search.
-- On Supabase (pgvector preinstalled) the table + HNSW index are created.
--
-- Dimension 768 = Gemini gemini-embedding-001 with output_dimensionality=768 (also the
-- native size of text-embedding-004 and of sentence-transformers all-mpnet-base-v2).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';
    ELSE
        RAISE NOTICE 'pgvector not available on this server — content_embeddings skipped (semantic search disabled)';
        RETURN;
    END IF;

    EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS content_embeddings (
            id              BIGSERIAL     PRIMARY KEY,
            page_id         BIGINT        REFERENCES crawled_pages(id) ON DELETE CASCADE,
            url             VARCHAR(2048) NOT NULL,
            kind            VARCHAR(32)   NOT NULL DEFAULT 'page'
                                CHECK (kind IN ('page','chunk','blueprint_page','query')),
            chunk_ix        INTEGER       NOT NULL DEFAULT 0,
            content_sha256  CHAR(64)      NOT NULL,
            model           VARCHAR(120)  NOT NULL,
            embedding       vector(768)   NOT NULL,
            created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_embedding_slot UNIQUE (url, kind, chunk_ix)
        )
    $ddl$;

    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_embeddings_page ON content_embeddings (page_id)';

    -- HNSW needs pgvector >= 0.5. If this server ships an older build, skip the index —
    -- at this schema's scale (hundreds of pages) a sequential scan is fast enough.
    BEGIN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON content_embeddings USING hnsw (embedding vector_cosine_ops)';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'hnsw index unavailable (pgvector < 0.5?) — skipped';
    END;
END $$;
