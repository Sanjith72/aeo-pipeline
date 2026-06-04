"""Domain configuration loader -- reads domains/{domain}.yaml and maps to Blueprint inputs."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aeo.models.blueprint import CompetitorIntel, EngineTarget, TaxonomyTag


@dataclass
class DomainConfig:
    domain: str
    engine_target: EngineTarget = EngineTarget.GENERIC
    freshness_days: int = 7
    taxonomy_tags: list[TaxonomyTag] = field(default_factory=list)
    seed_queries: list[str] = field(default_factory=list)
    required_entities: list[str] = field(default_factory=list)
    competitors: list[CompetitorIntel] = field(default_factory=list)


def load_domain_config(domain: str, domains_dir: Path | None = None) -> DomainConfig | None:
    """Load domains/{domain}.yaml if it exists, returning None if not found."""
    if domains_dir is None:
        # Resolve relative to the repo root (two levels up from this file)
        domains_dir = Path(__file__).parent.parent.parent / "domains"

    yaml_path = domains_dir / f"{domain}.yaml"
    if not yaml_path.exists():
        return None

    try:
        import yaml  # pyyaml -- already a transitive dep via several packages
    except ImportError:
        raise ImportError(
            "pyyaml is required to load domain configs. Run: pip install pyyaml"
        )

    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # Parse engine_target
    engine_target = EngineTarget.GENERIC
    et_raw = str(raw.get("engine_target", "generic")).lower()
    for member in EngineTarget:
        if member.value == et_raw:
            engine_target = member
            break

    # Parse taxonomy_tags -- silently skip unknown values
    taxonomy_tags: list[TaxonomyTag] = []
    valid_tag_values = {t.value for t in TaxonomyTag}
    for tag_str in raw.get("taxonomy_tags", []):
        if str(tag_str) in valid_tag_values:
            taxonomy_tags.append(TaxonomyTag(tag_str))

    # Parse competitors
    competitors: list[CompetitorIntel] = []
    for c in raw.get("competitors", []):
        if not isinstance(c, dict) or not c.get("domain"):
            continue
        competitors.append(CompetitorIntel(
            domain=str(c["domain"]),
            structural_patterns=[str(p) for p in c.get("structural_patterns", [])],
            citation_sources=[str(s) for s in c.get("citation_sources", [])],
            observed_entities=[str(e) for e in c.get("observed_entities", [])],
        ))

    return DomainConfig(
        domain=raw.get("domain", domain),
        engine_target=engine_target,
        freshness_days=int(raw.get("freshness_days", 7)),
        taxonomy_tags=taxonomy_tags,
        seed_queries=[str(q) for q in raw.get("seed_queries", [])],
        required_entities=[str(e) for e in raw.get("required_entities", [])],
        competitors=competitors,
    )
