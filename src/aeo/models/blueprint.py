from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field

# Number of days a generated blueprint's core structure stays locked/immutable.
BLUEPRINT_LOCK_DAYS = 30


class EngineTarget(StrEnum):
    """Answer engine a blueprint / gap analysis is tuned for.

    Used for prompt routing in the gap analyzer:
      perplexity      -> citation-density emphasis
      chatgpt_search  -> conversational coverage emphasis
      gemini          -> structured entity emphasis
      generic         -> engine-neutral template
    """

    PERPLEXITY = "perplexity"
    CHATGPT_SEARCH = "chatgpt_search"
    GEMINI = "gemini"
    GENERIC = "generic"


class TaxonomyTag(StrEnum):
    """Taxonomic classification tags aligned with security frameworks.

    String-enum only — these are static classification labels. No live calls are
    made to MITRE ATT&CK, NVD/CVSS, or the CISA KEV catalog from this module.
    """

    # MITRE ATT&CK tactics (subset)
    MITRE_INITIAL_ACCESS = "mitre:initial-access"
    MITRE_EXECUTION = "mitre:execution"
    MITRE_PERSISTENCE = "mitre:persistence"
    MITRE_PRIVILEGE_ESCALATION = "mitre:privilege-escalation"
    MITRE_CREDENTIAL_ACCESS = "mitre:credential-access"
    MITRE_EXFILTRATION = "mitre:exfiltration"
    # CVSS severity bands
    CVSS_LOW = "cvss:low"
    CVSS_MEDIUM = "cvss:medium"
    CVSS_HIGH = "cvss:high"
    CVSS_CRITICAL = "cvss:critical"
    # CISA Known Exploited Vulnerabilities catalog status
    CISA_KEV_LISTED = "cisa-kev:listed"
    CISA_KEV_NOT_LISTED = "cisa-kev:not-listed"


class ContentSection(BaseModel):
    heading: str
    type: Literal["faq", "howto", "definition", "comparison", "listicle", "narrative"]
    required_entities: list[str] = Field(default_factory=list)
    min_words: int = 150


class CompetitorIntel(BaseModel):
    """Competitor intelligence folded into the blueprint (Layer 1 empirical floor)."""

    domain: str
    structural_patterns: list[str] = Field(default_factory=list)
    citation_sources: list[str] = Field(default_factory=list)
    observed_entities: list[str] = Field(default_factory=list)


class Blueprint(BaseModel):
    """Reference architecture for what a domain's content must look like to be cited by AI engines.

    Core structural fields are ``frozen`` — once a blueprint is generated they cannot be
    reassigned on the instance. The 30-day ``locked_until`` window additionally prevents the
    *persisted* row from being replaced (see aeo.db.queries.upsert_blueprint). After the window
    expires, regeneration produces a NEW version rather than mutating this one — versioning is
    mandatory so week-over-week scores stay comparable (architecture v4 §1).
    """

    # validate_assignment makes assignment to a frozen field raise instead of silently passing.
    model_config = ConfigDict(validate_assignment=True)

    id: UUID = Field(default_factory=uuid4, frozen=True)
    domain: str = Field(frozen=True)
    # Persisted as the `blueprint_version` column; `version` is the in-model name kept for
    # backward compatibility with existing rows/queries.
    version: int = Field(default=1, frozen=True)
    engine_target: EngineTarget = Field(default=EngineTarget.GENERIC, frozen=True)
    taxonomy_tags: list[TaxonomyTag] = Field(default_factory=list, frozen=True)
    target_queries: list[str] = Field(
        frozen=True,
        description="Queries this domain's content should answer to be cited by AI engines",
    )
    required_entities: list[str] = Field(
        frozen=True,
        description="People, products, and concepts that must be present",
    )
    schema_types: list[str] = Field(
        frozen=True,
        description="schema.org types to implement (e.g. FAQPage, HowTo, Article)",
    )
    content_sections: list[ContentSection] = Field(default_factory=list, frozen=True)
    citation_sources: list[str] = Field(
        frozen=True,
        description="Authoritative external URLs that should be cited",
        default_factory=list,
    )
    competitor_intel: list[CompetitorIntel] = Field(default_factory=list, frozen=True)
    freshness_days: int = 7
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blueprint_id(self) -> str:
        """Stable public identifier (string form of the UUID primary key)."""
        return str(self.id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def locked_until(self) -> datetime:
        """Timestamp until which the core structure is immutable (created_at + 30 days)."""
        return self.created_at + timedelta(days=BLUEPRINT_LOCK_DAYS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """SHA-256 (64-char hex) fingerprint of the frozen core structure.

        Persisted to the CHAR(64) `content_hash` column; used to detect whether a
        regenerated blueprint actually changed the baseline.
        """
        core = {
            "domain": self.domain,
            "version": self.version,
            "engine_target": str(self.engine_target),
            "taxonomy_tags": sorted(str(t) for t in self.taxonomy_tags),
            "target_queries": self.target_queries,
            "required_entities": self.required_entities,
            "schema_types": self.schema_types,
            "content_sections": [s.model_dump() for s in self.content_sections],
            "citation_sources": self.citation_sources,
        }
        payload = json.dumps(core, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def is_locked(self) -> bool:
        """True while now is before locked_until — the persisted row must not be replaced."""
        return datetime.utcnow() < self.locked_until


class BlueprintLockError(RuntimeError):
    """Raised when an attempt is made to replace a blueprint still inside its lock window."""


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


# ── multi-track gap analysis (Block 3) ─────────────────────────────────────────


class GapTrackResult(BaseModel):
    """Result of one of the four stateless gap-analysis tracks."""

    track: Literal["content_gap", "citation_gap", "freshness_gap", "entity_coverage_gap"]
    engine_target: EngineTarget
    score: float = Field(ge=0.0, le=1.0, description="1.0 = fully covered, 0.0 = total gap")
    findings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class GapAnalysisReport(BaseModel):
    """Merged output of the 4-track parallel evaluator."""

    url: str
    domain: str
    blueprint_id: UUID
    engine_target: EngineTarget
    tracks: list[GapTrackResult] = Field(default_factory=list)
    aggregate_score: float = Field(ge=0.0, le=1.0)
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


# ── independent audit (Block 4) ────────────────────────────────────────────────


class CitationSignal(BaseModel):
    """Behavioral signal for a single cited URL found in an LLM output."""

    url: str
    structurally_valid: bool
    reachable: bool | None = None  # None when no HEAD request was performed
    hallucinated: bool


class AuditReport(BaseModel):
    """Structured output of the independent (adversarial) auditor."""

    id: UUID = Field(default_factory=uuid4)
    recommendation_id: UUID | None = None
    passed: bool
    citation_signals: list[CitationSignal] = Field(default_factory=list)
    failure_reason: str | None = None
    retry_attempts: int = 0
    audited_at: datetime = Field(default_factory=datetime.utcnow)
