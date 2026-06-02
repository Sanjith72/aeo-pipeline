from __future__ import annotations
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ContentSection(BaseModel):
    heading: str
    type: Literal["faq", "howto", "definition", "comparison", "listicle", "narrative"]
    required_entities: list[str] = Field(default_factory=list)
    min_words: int = 150


class Blueprint(BaseModel):
    """Reference architecture for what a domain's content must look like to be cited by AI engines."""

    id: UUID = Field(default_factory=uuid4)
    domain: str
    version: int = 1
    target_queries: list[str] = Field(
        description="Queries this domain's content should answer to be cited by AI engines"
    )
    required_entities: list[str] = Field(
        description="People, products, and concepts that must be present"
    )
    schema_types: list[str] = Field(
        description="schema.org types to implement (e.g. FAQPage, HowTo, Article)"
    )
    content_sections: list[ContentSection] = Field(default_factory=list)
    citation_sources: list[str] = Field(
        description="Authoritative external URLs that should be cited",
        default_factory=list,
    )
    freshness_days: int = 7
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CrawledPage(BaseModel):
    url: str
    content_hash: str
    title: str
    headings: list[str] = Field(default_factory=list)
    body_text: str
    links: list[str] = Field(default_factory=list)
    schema_markup: list[dict] = Field(default_factory=list)
    crawled_at: datetime = Field(default_factory=datetime.utcnow)
    changed: bool = True


class CoveredQuery(BaseModel):
    query: str
    covered: bool
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class CoveredEntity(BaseModel):
    entity: str
    present: bool
    mentions: int = 0


class CoverageDiff(BaseModel):
    domain: str
    blueprint_id: UUID
    url: str
    query_coverage: list[CoveredQuery] = Field(default_factory=list)
    entity_coverage: list[CoveredEntity] = Field(default_factory=list)
    schema_types_found: list[str] = Field(default_factory=list)
    schema_types_missing: list[str] = Field(default_factory=list)
    coverage_score: float = Field(ge=0.0, le=1.0)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class Recommendation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    domain: str
    url: str
    type: Literal["content", "schema", "entity", "citation"]
    priority: Literal["high", "medium", "low"]
    title: str
    description: str
    implementation: str
    impact_estimate: float = Field(ge=0.0, le=1.0, description="Estimated coverage improvement")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    recommendation_id: UUID
    is_valid: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    # Deterministic gate results
    word_count_ok: bool = False
    h1_question_ok: bool = False
    json_ld_ok: bool = False
    validated_at: datetime = Field(default_factory=datetime.utcnow)
