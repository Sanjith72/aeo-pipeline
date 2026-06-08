"""
Taxonomic Ceiling tests — the multi-category generalization of the L2 ceiling.

Pure data/string helpers: curated resolution + aliases, deterministic standards seed,
the dynamic prompt clause (known/unknown/none), and the entity merge that guarantees a
category's regulatory ceiling survives whatever an LLM returns.
"""

from __future__ import annotations

from aeo.reference.taxonomic_ceiling import (
    ceiling_prompt_clause,
    ceiling_standards,
    merge_ceiling_entities,
    resolve_ceiling,
)


class TestResolve:
    def test_known_categories_and_aliases(self):
        assert resolve_ceiling("healthcare").category == "healthcare"
        assert resolve_ceiling("Personal Finance").category == "finance"
        assert resolve_ceiling("infosec").category == "cybersecurity"
        assert resolve_ceiling("e-commerce SaaS").category == "ecommerce_saas"
        assert resolve_ceiling("legal services").category == "legal"

    def test_substring_match(self):
        assert resolve_ceiling("b2b fintech platform").category == "finance"
        assert resolve_ceiling("a healthcare analytics startup").category == "healthcare"

    def test_unknown_and_empty(self):
        assert resolve_ceiling("underwater basket weaving") is None
        assert resolve_ceiling(None) is None
        assert resolve_ceiling("") is None


class TestStandards:
    def test_seed_for_known_category(self):
        s = ceiling_standards("healthcare")
        assert "HIPAA" in s
        assert ceiling_standards("finance")  # non-empty

    def test_empty_for_unknown(self):
        assert ceiling_standards("nonsense-vertical") == []
        assert ceiling_standards(None) == []


class TestPromptClause:
    def test_known_category_anchors_on_curated_standards(self):
        clause = ceiling_prompt_clause("finance")
        assert "SEC regulations" in clause
        assert "ceiling" in clause.lower()

    def test_unknown_category_still_asks_for_synthesis(self):
        clause = ceiling_prompt_clause("veterinary telehealth")
        assert "veterinary telehealth" in clause
        assert "TAXONOMIC CEILING" in clause  # instructs the model to synthesize one

    def test_none_category_is_noop(self):
        assert ceiling_prompt_clause(None) == ""
        assert ceiling_prompt_clause("") == ""


class TestMergeEntities:
    def test_ceiling_unioned_first_and_deduped(self):
        merged = merge_ceiling_entities("healthcare", ["Acme Health", "HIPAA"], limit=20)
        assert merged[0] == "HIPAA"          # ceiling standards lead
        assert "Acme Health" in merged       # LLM entities preserved
        assert merged.count("HIPAA") == 1    # de-duplicated

    def test_unknown_category_is_passthrough(self):
        assert merge_ceiling_entities(None, ["a", "b", "c"], limit=2) == ["a", "b"]

    def test_respects_limit(self):
        merged = merge_ceiling_entities("finance", ["x", "y", "z"], limit=3)
        assert len(merged) == 3
