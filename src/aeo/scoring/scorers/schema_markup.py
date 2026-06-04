"""
Criterion 1 — Schema Markup.

Tiering is by count of *high-value* JSON-LD types present (FAQPage, HowTo,
TechArticle, …). Malformed blocks cost a point. Surfaces the glossary
DefinedTerm gap as evidence — the audit's biggest single quick win.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("schema_markup")
    valued = set(crit.cfg.get("valued_types", []))

    schema = ctx.bundle.get("schema_jsonld", {}) or {}
    glossary = ctx.bundle.get("glossary", {}) or {}

    types_present = set(schema.get("types", []))
    block_count = int(schema.get("block_count", 0) or 0)
    invalid = int(schema.get("invalid_blocks", 0) or 0)
    valued_present = sorted(types_present & valued)
    n_valued = len(valued_present)

    if block_count == 0:
        value = 1
    elif n_valued == 0:
        value = 2
    elif n_valued == 1:
        value = 3
    elif n_valued == 2:
        value = 4
    else:
        value = 5

    if invalid > 0 and value > 1:
        value -= 1  # malformed JSON-LD is a real defect, not just a miss

    dt_opportunity = bool(glossary.get("opportunity"))
    notes = []
    if block_count == 0:
        notes.append("No structured data found")
    if invalid > 0:
        notes.append(f"{invalid} malformed JSON-LD block(s)")
    if dt_opportunity:
        notes.append("Glossary page lacks DefinedTerm schema (high-impact quick win)")

    return CriterionScore(
        name="schema_markup",
        value=value,
        evidence={
            "block_count": block_count,
            "invalid_blocks": invalid,
            "types_present": sorted(types_present),
            "valued_types_present": valued_present,
            "defined_term_opportunity": dt_opportunity,
        },
        notes="; ".join(notes) or f"{n_valued} high-value schema type(s)",
    )
