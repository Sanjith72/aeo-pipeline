"""
Unit tests for the blueprint contract (v4 keystone).

The blueprint is the single typed surface the generator emits and the Coverage
Diff / gap analysis consume, so its guarantees are tested as a spec:
  * slug/path normalization is the one shape both sides match on;
  * closed vocabularies reject hallucinated categories;
  * the input hash is stable across reorderings and sensitive to real change;
  * JSON / JSONB round-trips are lossless.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aeo.reference import Blueprint, CoverageCluster, CoverageMap, SitemapNode, normalize_slug


def _node(slug: str, **kw) -> SitemapNode:
    return SitemapNode(slug=slug, title=kw.pop("title", slug), **kw)


def _blueprint(**kw) -> Blueprint:
    nodes = kw.pop(
        "sitemap",
        [
            _node("/what-is-ctem", page_type="pillar", intent="informational",
                  journey_stage="awareness", required_entities=["CTEM", "MITRE ATT&CK"],
                  seed_questions=["What is CTEM?"], cluster="ctem"),
            _node("/platform", page_type="product", intent="commercial",
                  journey_stage="decision"),
        ],
    )
    return Blueprint(topic=kw.pop("topic", "PEV"), sitemap=nodes, **kw)


class TestNormalizeSlug:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/What-Is-CTEM/", "/what-is-ctem"),
            ("home", "/home"),
            ("", "/"),
            ("/", "/"),
            ("///a//b///", "/a//b"),
            ("https://securin.io/blog/x", "/blog/x"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_slug(raw) == expected


class TestSitemapNode:
    def test_slug_normalized_on_construct(self):
        assert _node("/What-Is-X/").slug == "/what-is-x"

    def test_priority_clamped(self):
        assert _node("/a", priority=5.0).priority == 1.0
        assert _node("/a", priority=-1.0).priority == 0.0

    def test_lists_deduped_and_stripped(self):
        n = _node("/a", required_entities=[" CVSS ", "CVSS", "", "KEV"])
        assert n.required_entities == ["CVSS", "KEV"]

    def test_rejects_unknown_page_type(self):
        with pytest.raises(ValidationError):
            SitemapNode(slug="/a", title="A", page_type="megapage")

    def test_rejects_unknown_intent(self):
        with pytest.raises(ValidationError):
            SitemapNode(slug="/a", title="A", intent="transactional")

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            SitemapNode(slug="/a", title="A", surprise="x")


class TestBlueprintValidation:
    def test_requires_topic(self):
        with pytest.raises(ValidationError):
            Blueprint(topic="  ")

    def test_duplicate_slugs_rejected(self):
        with pytest.raises(ValidationError):
            Blueprint(topic="t", sitemap=[_node("/a"), _node("/a/")])

    def test_node_for_slug_matches_normalized(self):
        bp = _blueprint()
        assert bp.node_for_slug("/WHAT-IS-CTEM/").page_type == "pillar"
        assert bp.node_for_slug("/missing") is None

    def test_all_required_entities_union_stable(self):
        bp = _blueprint(
            coverage=CoverageMap(required_entities=["KEV", "CTEM"]),
        )
        ents = bp.all_required_entities()
        assert ents[:2] == ["KEV", "CTEM"]  # coverage first
        assert "MITRE ATT&CK" in ents
        assert len(ents) == len(set(ents))  # no dupes (CTEM appears twice in inputs)


class TestVersioningHash:
    def test_hash_is_order_independent_for_nodes(self):
        a = _blueprint()
        reordered = Blueprint(topic=a.topic, sitemap=list(reversed(a.sitemap)))
        assert a.hash_inputs() == reordered.hash_inputs()

    def test_hash_changes_when_structure_changes(self):
        a = _blueprint()
        b = _blueprint(sitemap=[*a.sitemap, _node("/new-pillar", page_type="pillar")])
        assert a.hash_inputs() != b.hash_inputs()

    def test_hash_ignores_provenance_fields(self):
        a = _blueprint(generator="deterministic", notes="x", version=1)
        b = _blueprint(generator="gemini:pro", notes="y", version=99)
        assert a.hash_inputs() == b.hash_inputs()

    def test_with_hash_sets_content_hash(self):
        bp = _blueprint().with_hash()
        assert bp.content_hash and bp.content_hash == bp.hash_inputs()


class TestRoundTrip:
    def test_jsonb_round_trip_lossless(self):
        bp = _blueprint(
            coverage=CoverageMap(
                required_entities=["CTEM", "CVSS"],
                journey_stages=["awareness", "decision"],
                clusters=[CoverageCluster(name="ctem", pillar_slug="/what-is-ctem",
                                          supporting_slugs=["/ctem-vs-vm"], min_pages=10)],
            )
        ).with_hash()
        restored = Blueprint.from_jsonb(bp.to_jsonb())
        assert restored == bp

    def test_json_round_trip_lossless(self):
        bp = _blueprint().with_hash()
        assert Blueprint.from_json(bp.to_json()) == bp

    def test_cluster_slugs_pillar_first(self):
        c = CoverageCluster(name="x", pillar_slug="/p", supporting_slugs=["/s1", "/p", "/s2"])
        assert c.slugs() == ["/p", "/s1", "/s2"]
