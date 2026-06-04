"""
Coverage Diff — the v4 *site-level* gap (a new kind of gap).

Where the Dual-Layer Gap Analysis answers "is this room up to code?", the Coverage
Diff answers "which rooms are missing from the house?": it compares the client's
**discovered, classified sitemap** against the blueprint's **ideal sitemap** and
emits site-level findings — pages missing entirely, and topical clusters too thin
to earn authority (below the blueprint's ``min_pages`` target). Its missing nodes
become net-new content recommendations; its output also feeds Page Prioritization.

Matching is deterministic and explainable: a blueprint node is *covered* when a
discovered page has the same normalized slug, or shares the node's distinctive
slug tokens and page-type (so ``/what-is-ctem`` is satisfied by ``/guides/ctem``
but a blog post never silently satisfies a product page). Pure — no I/O; the
caller supplies discovered pages (from Site Discovery + the prioritizer's
classifier) and a :class:`Blueprint`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..reference.blueprint import Blueprint, SitemapNode, normalize_slug

# Slug tokens that carry no topical signal — excluded from overlap matching.
_STOPWORDS = frozenset(
    {"what", "is", "are", "the", "a", "an", "to", "vs", "of", "and", "or",
     "how", "why", "for", "in", "on", "with", "your", "you", "guide", "page"}
)
# A discovered slug covers a node when it includes at least this share of the
# node's distinctive tokens (and the page-type matches). Exact slug always wins.
_OVERLAP_THRESHOLD = 0.6


@dataclass(slots=True)
class DiscoveredPage:
    """A client page as the Coverage Diff sees it (slug + classification)."""

    url: str
    slug: str
    page_type: str
    intent: str = "informational"

    @classmethod
    def from_url(cls, url: str, page_type: str, intent: str = "informational") -> DiscoveredPage:
        return cls(url=url, slug=normalize_slug(url), page_type=page_type, intent=intent)


@dataclass(slots=True)
class MissingNode:
    """A blueprint node with no covering client page — a net-new content target."""

    slug: str
    title: str
    page_type: str
    intent: str
    journey_stage: str
    cluster: str | None
    priority: float
    required_entities: list[str] = field(default_factory=list)
    seed_questions: list[str] = field(default_factory=list)
    rationale: str = ""

    @classmethod
    def from_node(cls, node: SitemapNode) -> MissingNode:
        return cls(
            slug=node.slug, title=node.title, page_type=node.page_type, intent=node.intent,
            journey_stage=node.journey_stage, cluster=node.cluster, priority=node.priority,
            required_entities=list(node.required_entities), seed_questions=list(node.seed_questions),
            rationale=node.rationale or f"Missing {node.page_type} for the {node.cluster or 'topic'} cluster",
        )


@dataclass(slots=True)
class ThinCluster:
    """A topical cluster with fewer present pages than its authority target."""

    name: str
    present_count: int
    min_pages: int
    missing_slugs: list[str] = field(default_factory=list)

    @property
    def shortfall(self) -> int:
        return max(0, self.min_pages - self.present_count)


@dataclass(slots=True)
class CoverageDiffResult:
    topic: str
    blueprint_version: int
    total_nodes: int
    matched: list[str]
    missing: list[MissingNode]
    thin_clusters: list[ThinCluster]

    @property
    def matched_count(self) -> int:
        return len(self.matched)

    @property
    def coverage_pct(self) -> float:
        return round(self.matched_count / self.total_nodes * 100, 1) if self.total_nodes else 0.0

    def missing_by_priority(self) -> list[MissingNode]:
        """Missing nodes, highest blueprint-priority first — the build order."""
        return sorted(self.missing, key=lambda m: (-m.priority, m.slug))

    def to_detail(self) -> dict:
        """JSONB payload for the coverage_diffs.detail column."""
        return {
            "topic": self.topic,
            "blueprint_version": self.blueprint_version,
            "coverage_pct": self.coverage_pct,
            "total_nodes": self.total_nodes,
            "matched": self.matched,
            "missing": [asdict(m) for m in self.missing_by_priority()],
            "thin_clusters": [asdict(t) for t in self.thin_clusters],
        }

    @classmethod
    def from_detail(cls, detail: dict) -> CoverageDiffResult:
        """Reconstruct a result from a persisted ``coverage_diffs.detail`` payload
        (used by the site report, which reads the diff back from the DB)."""
        return cls(
            topic=detail.get("topic", ""),
            blueprint_version=int(detail.get("blueprint_version", 0) or 0),
            total_nodes=int(detail.get("total_nodes", 0) or 0),
            matched=list(detail.get("matched", []) or []),
            missing=[MissingNode(**m) for m in (detail.get("missing", []) or [])],
            thin_clusters=[ThinCluster(**t) for t in (detail.get("thin_clusters", []) or [])],
        )


def _tokens(slug: str) -> set[str]:
    raw = normalize_slug(slug).strip("/").replace("/", "-")
    return {t for t in raw.split("-") if t and t not in _STOPWORDS}


def _covers(node: SitemapNode, page: DiscoveredPage) -> bool:
    """Does a discovered page cover a blueprint node?"""
    if page.slug == node.slug:
        return True
    node_tokens = _tokens(node.slug)
    if not node_tokens:
        return False
    if page.page_type != node.page_type:
        return False
    overlap = len(node_tokens & _tokens(page.slug)) / len(node_tokens)
    return overlap >= _OVERLAP_THRESHOLD


def coverage_diff(blueprint: Blueprint, discovered: list[DiscoveredPage]) -> CoverageDiffResult:
    """Compare the discovered sitemap against the blueprint's ideal sitemap."""
    matched: list[str] = []
    missing: list[MissingNode] = []
    covered_slugs: set[str] = set()

    for node in blueprint.sitemap:
        if any(_covers(node, p) for p in discovered):
            matched.append(node.slug)
            covered_slugs.add(node.slug)
        else:
            missing.append(MissingNode.from_node(node))

    thin: list[ThinCluster] = []
    for cluster in blueprint.coverage.clusters:
        slugs = cluster.slugs()
        present = sum(1 for s in slugs if s in covered_slugs)
        if present < cluster.min_pages:
            thin.append(
                ThinCluster(
                    name=cluster.name,
                    present_count=present,
                    min_pages=cluster.min_pages,
                    missing_slugs=[s for s in slugs if s not in covered_slugs],
                )
            )

    return CoverageDiffResult(
        topic=blueprint.topic,
        blueprint_version=blueprint.version,
        total_nodes=len(blueprint.sitemap),
        matched=matched,
        missing=missing,
        thin_clusters=thin,
    )
