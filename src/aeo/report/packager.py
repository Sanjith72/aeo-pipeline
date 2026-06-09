"""
Implementation Asset Packager (SP-3) — turn a blueprint + strategy into a
**developer-ready bundle** the user can hand straight to a developer or site builder.

Where the site report says *what's missing*, this produces *the things to build*:

  * ``README.md``               — bundle overview + how to use it
  * ``sitemap.xml``             — the ideal sitemap (valid XML, absolute URLs)
  * ``navigation.md``           — primary nav + topic-cluster hierarchy
  * ``content-briefs.md``       — priority-ordered briefs for every page to build
  * ``internal-linking.md``     — pillar↔supporting + cross-cluster linking plan
  * ``schema-and-entities.md``  — required entities + recommended schema.org types
  * ``pages/<slug>.md``         — per-page spec sheets (H1, sections, FAQ, JSON-LD)
  * ``STRATEGY.md``             — the scenario + prioritized action plan (when a profile is given)

Deterministic-first: the structural assets are pure transforms of the blueprint /
coverage diff; the per-page specs reuse :func:`aeo.recommender.draft.draft_missing_page`,
which emits a grounded scaffold with the LLM off and full prose when enabled. The
JSON-LD is always built in code (valid regardless of the prose path).

Pure core (:func:`build_asset_bundle` → :class:`AssetBundle`); the only I/O is
:meth:`AssetBundle.write`, which materializes the bundle to a directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from ..processor.coverage_diff import CoverageDiffResult
from ..reference.blueprint import Blueprint

_DEFAULT_ORIGIN = "https://www.example.com"
_MAX_PRIMARY_NAV = 8

# Recommended schema.org @type per page type (FAQPage is added when a page has Q&A).
_SCHEMA_FOR_TYPE: dict[str, list[str]] = {
    "pillar": ["TechArticle", "Article"],
    "blog": ["Article", "BlogPosting"],
    "product": ["Product", "SoftwareApplication"],
    "solution": ["Service"],
    "homepage": ["Organization", "WebSite"],
    "about": ["AboutPage", "Organization"],
    "contact": ["ContactPage"],
    "utility": ["WebPage"],
    "default": ["WebPage"],
}


@dataclass(slots=True)
class Asset:
    path: str  # relative path within the bundle (e.g. "sitemap.xml", "pages/what-is-ctem.md")
    content: str
    kind: str  # readme | sitemap | nav | content_briefs | linking | schema | page_spec | strategy


@dataclass(slots=True)
class AssetBundle:
    name: str
    assets: list[Asset] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "bundle": self.name,
            "asset_count": len(self.assets),
            "assets": [{"path": a.path, "kind": a.kind} for a in self.assets],
        }

    def write(self, out_dir: str | Path) -> list[Path]:
        """Materialize the bundle to ``out_dir`` (creating subdirs). Writes every asset
        plus a ``manifest.json``. Returns the paths written."""
        base = Path(out_dir)
        written: list[Path] = []
        for asset in self.assets:
            path = base / asset.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(asset.content, encoding="utf-8")
            written.append(path)
        manifest_path = base / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest(), indent=2), encoding="utf-8")
        written.append(manifest_path)
        return written


# ── helpers ───────────────────────────────────────────────────────────────────


def _origin(value: str | None) -> str:
    """Resolve a base origin from a domain or URL; falls back to a clearly-placeholder host."""
    if not value:
        return _DEFAULT_ORIGIN
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if parts.netloc:
        return f"{parts.scheme or 'https'}://{parts.netloc}"
    return _DEFAULT_ORIGIN


def _slug_filename(slug: str) -> str:
    stem = slug.strip("/").replace("/", "-")
    return stem or "home"


def _target_nodes(blueprint: Blueprint, coverage: CoverageDiffResult | None) -> list[Any]:
    """The pages to build, priority-ordered. From the coverage diff's missing nodes when
    available (the real to-build set), else the whole blueprint sitemap."""
    if coverage is not None:
        return list(coverage.missing_by_priority())
    return sorted(blueprint.sitemap, key=lambda n: (-n.priority, n.slug))


def _node_attr(node: Any, key: str, default: Any = None) -> Any:
    return getattr(node, key, default)


# ── individual assets ───────────────────────────────────────────────────────


def _sitemap_xml(blueprint: Blueprint, origin: str) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for node in sorted(blueprint.sitemap, key=lambda n: (-n.priority, n.slug)):
        loc = escape(f"{origin}{node.slug}")
        lines.append(f"  <url><loc>{loc}</loc><priority>{node.priority:.1f}</priority></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _navigation_md(blueprint: Blueprint) -> str:
    standalone = [n for n in blueprint.sitemap if not n.cluster]
    primary = sorted(standalone, key=lambda n: (-n.priority, n.slug))[:_MAX_PRIMARY_NAV]
    lines = [f"# Navigation Structure — {blueprint.topic}", "",
             "## Primary navigation (suggested)", ""]
    for n in primary:
        lines.append(f"- {n.title}  (`{n.slug}`)")
    lines += ["", "## Topic clusters (group these for topical authority + internal linking)", ""]
    for cl in blueprint.coverage.clusters:
        pillar = blueprint.node_for_slug(cl.pillar_slug)
        ptitle = pillar.title if pillar else cl.pillar_slug
        lines.append(f"### {cl.name}  (target ≥ {cl.min_pages} pages)")
        lines.append(f"- **Pillar:** {ptitle}  (`{cl.pillar_slug}`)")
        for slug in cl.supporting_slugs:
            sn = blueprint.node_for_slug(slug)
            lines.append(f"  - {sn.title if sn else slug}  (`{slug}`)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _content_briefs_md(blueprint: Blueprint, nodes: list[Any]) -> str:
    lines = [f"# Content Briefs — {blueprint.topic}", "",
             f"{len(nodes)} page(s) to build, highest priority first.", ""]
    for i, n in enumerate(nodes, start=1):
        ents = ", ".join(_node_attr(n, "required_entities", []) or []) or "—"
        lines += [
            f"## {i}. {_node_attr(n, 'title', '')}  (`{_node_attr(n, 'slug', '')}`)",
            f"- **Type / intent:** {_node_attr(n, 'page_type', '')} / {_node_attr(n, 'intent', '')}",
            f"- **Journey stage:** {_node_attr(n, 'journey_stage', '')}",
            f"- **Cluster:** {_node_attr(n, 'cluster', None) or '—'}",
            f"- **Priority:** {_node_attr(n, 'priority', 0.0)}",
            f"- **Required entities:** {ents}",
        ]
        seeds = _node_attr(n, "seed_questions", []) or []
        if seeds:
            lines.append("- **Questions this page must answer:**")
            lines += [f"  - {q}" for q in seeds]
        why = _node_attr(n, "rationale", "") or ""
        if why:
            lines.append(f"- **Why:** {why}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _internal_linking_md(blueprint: Blueprint) -> str:
    lines = [f"# Internal Linking Plan — {blueprint.topic}", "",
             "Authority flows along internal links; answer engines map your topic graph from them.",
             "", "## Rules", "",
             "- Every supporting page links **up** to its cluster pillar.",
             "- Each pillar links **down** to all its supporting pages.",
             "- Cross-link related clusters where topics overlap.",
             "- Use descriptive, entity-rich anchor text (not \"click here\").", "",
             "## Per-cluster links", ""]
    for cl in blueprint.coverage.clusters:
        pillar = blueprint.node_for_slug(cl.pillar_slug)
        ptitle = pillar.title if pillar else cl.pillar_slug
        lines.append(f"### {cl.name}")
        lines.append(f"- Pillar `{cl.pillar_slug}` ({ptitle}) ⇄ supporting:")
        for slug in cl.supporting_slugs:
            lines.append(f"  - `{slug}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _schema_entities_md(blueprint: Blueprint) -> str:
    entities = blueprint.all_required_entities()
    lines = [f"# Schema & Entity Recommendations — {blueprint.topic}", "",
             "## Required entities to cover (name these explicitly across the site)", ""]
    lines += [f"- {e}" for e in entities] or ["- (none specified)"]
    lines += ["", "## Recommended schema.org types by page type", ""]
    seen_types = {n.page_type for n in blueprint.sitemap}
    for pt in sorted(seen_types):
        types = _SCHEMA_FOR_TYPE.get(pt, _SCHEMA_FOR_TYPE["default"])
        lines.append(f"- **{pt}** → `{'`, `'.join(types)}`")
    lines += ["", "_Add `FAQPage` to any page with a Q&A block. JSON-LD per page is in `pages/`._"]
    return "\n".join(lines).rstrip() + "\n"


def _page_spec_md(node: Any, *, topic: str, origin: str, llm: Any) -> Asset:
    from ..recommender.draft import draft_missing_page

    draft = draft_missing_page(node, topic=topic, llm=llm, origin=origin)
    p = draft.to_payload()
    header = (
        f"<!-- page spec: {draft.page_type}/{draft.intent} · "
        f"generator={p['generator']} · quality={p['draft_quality']} -->\n\n"
    )
    jsonld = json.dumps(p["jsonld"], indent=2)
    body = f"{header}{p['body_markdown']}\n\n## JSON-LD (paste into <head>)\n\n```json\n{jsonld}\n```\n"
    return Asset(path=f"pages/{_slug_filename(draft.slug)}.md", content=body, kind="page_spec")


def _strategy_md(profile: dict[str, Any]) -> str:
    cls = profile.get("classification", {}) or {}
    biz = profile.get("business_intent", {}) or {}
    jr = profile.get("journey", {}) or {}
    lines = [f"# Strategy — {profile.get('domain', '')}", "",
             f"**Scenario:** {profile.get('scenario')} → {profile.get('deliverable')}",
             f"**Business model:** {biz.get('model')}  ({biz.get('decided_by')})",
             f"**Site class:** {cls.get('site_class')}  ({cls.get('page_count')} pages)",
             f"**Journey gaps:** {', '.join(jr.get('gaps', [])) or 'none'}", "",
             profile.get("narrative", ""), "", "## Action plan (priority order)", ""]
    for a in profile.get("actions", []) or []:
        lines.append(f"{a.get('priority')}. [{a.get('category')}] {a.get('title')}  ({a.get('effort')})")
    return "\n".join(lines).rstrip() + "\n"


def _readme_md(blueprint: Blueprint, nodes: list[Any], origin: str, has_strategy: bool) -> str:
    files = ["`sitemap.xml` — the ideal sitemap", "`navigation.md` — nav + cluster hierarchy",
             "`content-briefs.md` — a brief per page to build",
             "`internal-linking.md` — how pages link together",
             "`schema-and-entities.md` — entities + schema.org types",
             "`pages/` — a publishable spec sheet (H1, sections, FAQ, JSON-LD) per page"]
    if has_strategy:
        files.insert(0, "`STRATEGY.md` — the scenario + prioritized action plan")
    lines = [f"# AEO Implementation Bundle — {blueprint.topic}", "",
             f"Ideal site for **{origin}**: {len(blueprint.sitemap)} pages, {len(nodes)} to build.",
             "", "Hand this folder to a developer or site builder. Contents:", ""]
    lines += [f"- {f}" for f in files]
    lines += ["", "Start with `content-briefs.md` (priority order), use the matching `pages/<slug>.md`",
              "spec for each, then apply `internal-linking.md` and `schema-and-entities.md`.",
              "", "_Generated by the AEO pipeline. Per-page prose is a grounded scaffold unless the LLM",
              "was enabled; JSON-LD is always code-built and valid._"]
    return "\n".join(lines).rstrip() + "\n"


# ── public API ────────────────────────────────────────────────────────────────


def build_asset_bundle(
    *,
    blueprint: Blueprint,
    coverage: CoverageDiffResult | None = None,
    profile: dict[str, Any] | None = None,
    origin: str | None = None,
    llm: Any = None,
    draft_limit: int = 10,
    name: str | None = None,
) -> AssetBundle:
    """Assemble a developer-ready asset bundle from a blueprint (+ optional coverage diff
    and SiteProfile dict). ``draft_limit`` caps the per-page spec sheets (the expensive,
    LLM-backed step); the rest of the bundle covers every page. Pure — call
    :meth:`AssetBundle.write` to materialize it."""
    org = _origin(origin or (profile or {}).get("domain"))
    nodes = _target_nodes(blueprint, coverage)
    bundle_name = name or blueprint.topic

    assets: list[Asset] = [
        Asset("README.md", _readme_md(blueprint, nodes, org, profile is not None), "readme"),
        Asset("sitemap.xml", _sitemap_xml(blueprint, org), "sitemap"),
        Asset("navigation.md", _navigation_md(blueprint), "nav"),
        Asset("content-briefs.md", _content_briefs_md(blueprint, nodes), "content_briefs"),
        Asset("internal-linking.md", _internal_linking_md(blueprint), "linking"),
        Asset("schema-and-entities.md", _schema_entities_md(blueprint), "schema"),
    ]
    if profile is not None:
        assets.append(Asset("STRATEGY.md", _strategy_md(profile), "strategy"))

    if draft_limit > 0:
        for node in nodes[:draft_limit]:
            assets.append(_page_spec_md(node, topic=blueprint.topic, origin=org, llm=llm))

    return AssetBundle(name=bundle_name, assets=assets)
