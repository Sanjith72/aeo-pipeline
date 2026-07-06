"""
Best-effort page-embedding indexer for the deep audit (opt-in).

When ``AEO__LLM__EMBEDDING_INDEXING=true`` and the server has pgvector (migration
0025), each processed page's identity text (title + description + headings — small on
purpose: bodies would burn the free-tier token budget) is embedded and upserted into
``content_embeddings``, which powers the agent's ``semantic_search`` tool.

Deterministic-first contract: everything here is a garnish. Any failure — no table, no
embeddings provider, API error — logs a debug line and returns False; it can never
raise into (or slow-fail) an audit run.
"""

from __future__ import annotations

import hashlib

from ..logging import get_logger
from ..settings import get_settings
from ..storage.models import ExtractionBundle, FetchedPage

log = get_logger(__name__)


def _identity_text(page: FetchedPage, bundle: ExtractionBundle) -> str:
    parts: list[str] = [page.url]
    meta = bundle.get("meta") or {}
    if isinstance(meta, dict):
        for key in ("title", "description"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    headings = bundle.get("headings") or {}
    if isinstance(headings, dict):
        for key in ("h1", "h2"):
            values = headings.get(key)
            if isinstance(values, list):
                parts.extend(str(v).strip() for v in values[:10] if str(v).strip())
    return "\n".join(parts)[:4000]


def maybe_index_page(page: FetchedPage, page_id: int, bundle: ExtractionBundle) -> bool:
    """Embed + upsert one page's identity text. Returns True only on a fresh upsert."""
    try:
        if not get_settings().llm.embedding_indexing:
            return False
        from ..storage.repos import embeddings as embeddings_repo

        if not embeddings_repo.available():
            return False
        text = _identity_text(page, bundle)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if embeddings_repo.is_current(page.url, sha):
            return False

        from ..nlp.llm import get_client

        vector = get_client().embed(text)
        if vector is None:
            return False
        embeddings_repo.upsert(
            page.url, vector,
            content_sha256=sha,
            model=get_settings().llm.embedding_model,
            page_id=page_id,
        )
        log.info("page_embedding_indexed", url=page.url, page_id=page_id)
        return True
    except Exception as exc:  # garnish — never break the audit
        log.debug("page_embedding_skipped", url=page.url, error=str(exc))
        return False
