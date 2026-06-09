"""
Reference Architecture Framework loader — L2 (guardrail + ceiling).

Typed accessors over ``config/framework.yaml``: the curated topic taxonomy
(clusters → pillar + supporting nodes, standalone nodes), the required-entity
vocabulary, and the per-criterion definitions (perfect vs. average page). This is
the **guardrail** the generator hands the LLM so synthesis can enrich but not
invent — every framework node is already a validated :class:`SitemapNode`, so an
out-of-vocabulary page-type/intent fails *here*, at load, not downstream.

Pure config-over-code, mirroring the rubric/prioritization loaders: tuning the
ideal site is a YAML edit, and the loader is cached for the process lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..settings import get_settings, load_yaml_file
from .blueprint import SitemapNode, normalize_slug
from .domain_config import normalize_domain

# Base blueprint-importance per page-type, used when no competitor signal refines
# it. Pillars/products are the AEO cornerstone; utility pages barely matter.
_BASE_PRIORITY: dict[str, float] = {
    "pillar": 0.9,
    "product": 0.85,
    "solution": 0.8,
    "homepage": 0.7,
    "blog": 0.6,
    "about": 0.4,
    "contact": 0.4,
    "utility": 0.2,
    "default": 0.5,
}


@dataclass(slots=True)
class CriteriaDefinition:
    """The 'ceiling' half of L2: what better-than-competitor means for a criterion."""

    criterion: str
    target: int
    perfect: str = ""
    average: str = ""
    checkable: list[str] = field(default_factory=list)
    schema_org: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClusterDef:
    name: str
    min_pages: int
    pillar_slug: str
    node_slugs: list[str]  # pillar first, then supporting

    @property
    def supporting_slugs(self) -> list[str]:
        return [s for s in self.node_slugs if s != self.pillar_slug]


@dataclass(slots=True)
class Framework:
    version: str
    topic: str
    required_entities: list[str]
    journey_stages: list[str]
    nodes: list[SitemapNode]
    clusters: list[ClusterDef]
    criteria: dict[str, CriteriaDefinition]

    def criteria_definition(self, name: str) -> CriteriaDefinition | None:
        return self.criteria.get(name)

    def node_for_slug(self, slug: str) -> SitemapNode | None:
        target = normalize_slug(slug)
        return next((n for n in self.nodes if n.slug == target), None)


def _base_priority(page_type: str) -> float:
    return _BASE_PRIORITY.get(page_type, _BASE_PRIORITY["default"])


def _node_from_cfg(raw: dict, *, cluster: str | None, allowed_entities: set[str]) -> SitemapNode:
    """Build a validated SitemapNode from a framework YAML entry, dropping any
    required-entity outside the topic vocabulary (the guardrail). Constructed via
    ``model_validate`` so the closed-vocab Literals are enforced at runtime (an
    invalid page_type/intent raises here, at load)."""
    page_type = str(raw.get("page_type", "default"))
    entities = [e for e in (raw.get("required_entities") or []) if e in allowed_entities]
    return SitemapNode.model_validate(
        {
            "slug": raw["slug"],
            "title": str(raw.get("title", raw["slug"])),
            "page_type": page_type,
            "intent": str(raw.get("intent", "informational")),
            "journey_stage": str(raw.get("journey_stage", "awareness")),
            "required_entities": entities,
            "seed_questions": [str(q) for q in (raw.get("seed_questions") or [])],
            "cluster": cluster,
            "priority": _base_priority(page_type),
            "rationale": str(raw.get("rationale", "")),
        }
    )


def domain_framework_path(domain: str | None) -> Path | None:
    """Path to a per-domain framework override (``config/domains/{domain}.framework.yaml``)
    if it exists, else None. This is the seam that makes the tool topic-agnostic: any
    site can carry its own ideal-site taxonomy without touching the shared framework."""
    if not domain:
        return None
    key = normalize_domain(domain)
    if not key:
        return None
    path = Path(get_settings().config_dir) / "domains" / f"{key}.framework.yaml"
    return path if path.exists() else None


def _load_framework_raw(domain: str | None) -> dict[str, Any]:
    path = domain_framework_path(domain)
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return load_yaml_file("framework.yaml")


@lru_cache(maxsize=16)
def load_framework(domain: str | None = None) -> Framework:
    """Load the reference framework. With ``domain`` set and a per-domain override
    present, that override is used; otherwise the shared ``framework.yaml``."""
    return build_framework(_load_framework_raw(domain))


def build_framework(raw: dict[str, Any]) -> Framework:
    """Parse a raw framework mapping (from YAML or ``framework_bootstrap``) into a typed
    :class:`Framework`. Shared by :func:`load_framework` (file-backed) and the SP-2 brief
    path (in-memory), so a brief-tailored framework needs no file write to be usable."""
    required_entities = [str(e) for e in (raw.get("required_entities") or [])]
    allowed = set(required_entities)
    journey_stages = [str(s) for s in (raw.get("journey_stages") or ["awareness", "consideration", "decision"])]

    nodes: list[SitemapNode] = []
    clusters: list[ClusterDef] = []

    for cl in raw.get("clusters", []) or []:
        name = str(cl["name"])
        pillar_raw = cl.get("pillar") or {}
        cluster_nodes = [_node_from_cfg(pillar_raw, cluster=name, allowed_entities=allowed)]
        for sup in cl.get("supporting", []) or []:
            cluster_nodes.append(_node_from_cfg(sup, cluster=name, allowed_entities=allowed))
        nodes.extend(cluster_nodes)
        clusters.append(
            ClusterDef(
                name=name,
                min_pages=int(cl.get("min_pages", 1)),
                pillar_slug=cluster_nodes[0].slug,
                node_slugs=[n.slug for n in cluster_nodes],
            )
        )

    for sn in raw.get("standalone_nodes", []) or []:
        nodes.append(_node_from_cfg(sn, cluster=None, allowed_entities=allowed))

    criteria: dict[str, CriteriaDefinition] = {}
    for name, cdef in (raw.get("criteria_definitions") or {}).items():
        criteria[name] = CriteriaDefinition(
            criterion=name,
            target=int(cdef.get("target", 4)),
            perfect=str(cdef.get("perfect", "")),
            average=str(cdef.get("average", "")),
            checkable=[str(x) for x in (cdef.get("checkable") or [])],
            schema_org=[str(x) for x in (cdef.get("schema_org") or [])],
        )

    return Framework(
        version=str(raw.get("version", "0")),
        topic=str(raw.get("topic", "")),
        required_entities=required_entities,
        journey_stages=journey_stages,
        nodes=nodes,
        clusters=clusters,
        criteria=criteria,
    )
