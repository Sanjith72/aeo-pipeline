"""
The blueprint contract — the versioned, per-topic *ideal site* the v4 Reference
Architecture Generator emits and the Coverage Diff + gap analysis measure against.

This module is **the contract** (v4's "blueprint JSON schema"): the single typed
surface every downstream block depends on. The generator (``reference.generator``)
produces a :class:`Blueprint`; the Coverage Diff (``processor.coverage_diff``)
reads its ``sitemap``; the recommender turns missing nodes into net-new content
recs; and ``storage.repos.blueprints`` round-trips it through a JSONB column. Keep
these models stable — a richer generator changes how a blueprint is *built*, never
its shape.

Two product-load-bearing ideas live here:

  * **Versioning.** Regenerating the blueprint every run would move the measuring
    stick and make week-over-week scores meaningless. So a blueprint carries a
    monotonic ``version`` (assigned by the repo) and a ``content_hash`` of its
    *inputs* (topic + framework version + competitor set + node slugs). Identical
    inputs ⇒ identical hash ⇒ reuse the pinned version; changed inputs ⇒ a new
    version, flagged in the report so a score jump reads as "new baseline."

  * **Guardrailed vocabulary.** ``page_type`` / ``intent`` / ``journey_stage`` are
    closed sets aligned with the existing prioritizer and query-intent classifier,
    so a hallucinating generator can't invent categories the rest of the system
    can't route. Validation rejects out-of-vocabulary values rather than silently
    passing them downstream.

Pure and dependency-light: Pydantic models + a deterministic hash. No I/O.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Closed vocabularies (aligned with prioritization.yaml + query_intent) ──────
# page_type mirrors config/prioritization.yaml base_weights + the 'default' fallback.
PageType = Literal[
    "homepage", "product", "solution", "pillar", "blog", "about", "contact", "utility", "default"
]
# intent mirrors the query-intent classifier (commercial > navigational > informational).
Intent = Literal["commercial", "navigational", "informational"]
# journey_stage is the v4 coverage-map dimension.
JourneyStage = Literal["awareness", "consideration", "decision"]

# generator provenance written into every blueprint.
GENERATOR_DETERMINISTIC = "deterministic"


def normalize_slug(slug: str) -> str:
    """Canonical path form: lowercase, single leading slash, no trailing slash.

    ``/What-Is-CTEM/`` → ``/what-is-ctem``; a bare ``home`` → ``/home``; ``/`` and
    ``""`` both → ``/`` (the homepage). Matching the Coverage Diff against the
    prioritizer's URL paths needs both sides in the same shape, so this is the one
    normalizer both call."""
    s = (slug or "").strip().lower()
    if not s:
        return "/"
    if "://" in s:  # tolerate a full URL — keep only its path
        from urllib.parse import urlsplit

        s = urlsplit(s).path or "/"
    s = "/" + s.strip("/")
    return s or "/"


class SitemapNode(BaseModel):
    """One page the ideal site should contain — a row of the blueprint sitemap.

    Carries both the *structural* facts (slug, type, intent) the Coverage Diff
    matches on and the *coverage* facts (required entities, journey stage, seed
    questions) the recommender turns into a brief for a missing page."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    page_type: PageType = "default"
    intent: Intent = "informational"
    journey_stage: JourneyStage = "awareness"
    required_entities: list[str] = Field(default_factory=list)
    seed_questions: list[str] = Field(default_factory=list)
    cluster: str | None = None  # name of the coverage cluster this node belongs to
    priority: float = 0.5  # blueprint-importance 0..1 (drives missing-page prioritization)
    rationale: str = ""

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return normalize_slug(v)

    @field_validator("priority")
    @classmethod
    def _clamp_priority(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("required_entities", "seed_questions")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        # Dedupe preserving order, drop blanks — keeps hashes/diffs stable.
        seen: set[str] = set()
        out: list[str] = []
        for item in v or []:
            t = str(item).strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out


class CoverageCluster(BaseModel):
    """A topical-authority cluster: one pillar + its supporting pages. ``min_pages``
    encodes the v4 "10-20 pieces per cluster" target the Coverage Diff flags a
    cluster as *thin* against."""

    model_config = ConfigDict(extra="forbid")

    name: str
    pillar_slug: str
    supporting_slugs: list[str] = Field(default_factory=list)
    min_pages: int = 1

    @field_validator("pillar_slug")
    @classmethod
    def _pillar(cls, v: str) -> str:
        return normalize_slug(v)

    @field_validator("supporting_slugs")
    @classmethod
    def _support(cls, v: list[str]) -> list[str]:
        return [normalize_slug(s) for s in (v or [])]

    @field_validator("min_pages")
    @classmethod
    def _min_pages(cls, v: int) -> int:
        return max(1, int(v))

    def slugs(self) -> list[str]:
        """Every slug in the cluster (pillar first), deduped, order-stable."""
        seen: set[str] = set()
        result: list[str] = []
        for s in [self.pillar_slug, *self.supporting_slugs]:
            if s not in seen:
                seen.add(s)
                result.append(s)
        return result


class CoverageMap(BaseModel):
    """Topic-level coverage targets: the entity set the topic must cover, the
    journey stages it should span, and the topical clusters that structure it."""

    model_config = ConfigDict(extra="forbid")

    required_entities: list[str] = Field(default_factory=list)
    journey_stages: list[str] = Field(default_factory=list)
    clusters: list[CoverageCluster] = Field(default_factory=list)


class Blueprint(BaseModel):
    """The versioned ideal site for one ``(topic)``.

    Built by the generator, pinned per run, measured against by the Coverage Diff
    and (as the dynamic 60% baseline) the gap analysis. ``version`` is assigned by
    the repo on insert; ``content_hash`` is derived from the inputs so the repo can
    decide reuse-vs-bump deterministically."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    version: int = 1
    generator: str = GENERATOR_DETERMINISTIC  # 'deterministic' | 'gemini:<model>' | ...
    framework_version: str = "0"
    competitors: list[str] = Field(default_factory=list)  # domains feeding L1
    sitemap: list[SitemapNode] = Field(default_factory=list)
    coverage: CoverageMap = Field(default_factory=CoverageMap)
    content_hash: str = ""
    notes: str = ""

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        t = (v or "").strip()
        if not t:
            raise ValueError("blueprint.topic must be non-empty")
        return t

    @model_validator(mode="after")
    def _unique_slugs(self) -> Blueprint:
        slugs = [n.slug for n in self.sitemap]
        if len(slugs) != len(set(slugs)):
            dupes = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"blueprint.sitemap has duplicate slugs: {dupes}")
        return self

    # ── derived accessors (what downstream blocks ask for) ────────────────────

    def node_for_slug(self, slug: str) -> SitemapNode | None:
        target = normalize_slug(slug)
        return next((n for n in self.sitemap if n.slug == target), None)

    def slugs(self) -> set[str]:
        return {n.slug for n in self.sitemap}

    def all_required_entities(self) -> list[str]:
        """Union of the coverage map's entities and every node's, order-stable."""
        seen: set[str] = set()
        out: list[str] = []
        for ent in [*self.coverage.required_entities, *(e for n in self.sitemap for e in n.required_entities)]:
            if ent not in seen:
                seen.add(ent)
                out.append(ent)
        return out

    # ── versioning ────────────────────────────────────────────────────────────

    def hash_inputs(self) -> str:
        """Deterministic hash of the *inputs* that define this blueprint. Two
        generations from the same topic/framework/competitors/structure collapse to
        one version; any structural change bumps it. Excludes ``version``,
        ``content_hash``, ``notes``, and ``generator`` (provenance, not identity)."""
        payload = {
            "topic": self.topic,
            "framework_version": self.framework_version,
            "competitors": sorted(self.competitors),
            "sitemap": sorted(
                (
                    n.slug,
                    n.page_type,
                    n.intent,
                    n.journey_stage,
                    tuple(sorted(n.required_entities)),
                )
                for n in self.sitemap
            ),
            "coverage_entities": sorted(self.coverage.required_entities),
            "clusters": sorted(
                (c.name, c.pillar_slug, tuple(sorted(c.supporting_slugs)), c.min_pages)
                for c in self.coverage.clusters
            ),
        }
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_hash(self) -> Blueprint:
        """Return a copy with ``content_hash`` set from :meth:`hash_inputs`."""
        return self.model_copy(update={"content_hash": self.hash_inputs()})

    # ── JSON / JSONB round-trip ────────────────────────────────────────────────

    def to_jsonb(self) -> dict[str, Any]:
        """Plain dict for the ``blueprints.body`` JSONB column."""
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_jsonb(cls, data: dict[str, Any]) -> Blueprint:
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, raw: str) -> Blueprint:
        return cls.model_validate_json(raw)
