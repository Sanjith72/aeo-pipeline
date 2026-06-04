"""Deterministic extractors. Each module exposes an `extract(html, soup, url) -> dict`."""

from . import (
    chunker,
    eeat,
    entities,
    glossary,
    headings,
    links,
    meta,
    qa_blocks,
    readability,
    render_mode,
    schema_jsonld,
    stats,
)

# Registered extractors run by the pipeline ExtractStage, in order.
# Adding a new extractor = add the module + append here.
DEFAULT_EXTRACTORS = [
    ("meta",          meta.extract),
    ("headings",      headings.extract),
    ("schema_jsonld", schema_jsonld.extract),
    ("qa_blocks",     qa_blocks.extract),
    ("stats",         stats.extract),
    ("entities",      entities.extract),
    ("eeat",          eeat.extract),
    ("links",         links.extract),
    ("readability",   readability.extract),
    ("render_mode",   render_mode.extract),
    ("glossary",      glossary.extract),
    ("chunker",       chunker.extract),
]
