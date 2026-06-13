"""
Implementation Asset Packager (SP-3) — turn a blueprint + strategy into the bundle
that fits **whoever is building the site** (``builder_mode``):

  * ``dev``  (default) — the original developer bundle, unchanged:
      ``README.md``, ``sitemap.xml``, ``navigation.md``, ``content-briefs.md``,
      ``internal-linking.md``, ``schema-and-entities.md``, ``pages/<slug>.md`` spec
      sheets, ``STRATEGY.md`` (when a profile is given).
  * ``diy``  — owner building it themselves on a site builder: ``START-HERE.md``
      (30-day plan), paste-ready ``pages/<slug>.md`` drafts, ``platform-tips.md``,
      ``get-found-now.md``, and the dev bundle tucked under ``for-your-developer/``.
  * ``ai``   — owner building with ChatGPT / builder AI: ``prompts/<slug>.md``
      (deterministic — no LLM calls server-side) instead of page drafts.
  * ``hire`` — owner hiring help: the diy pack plus ``hire-someone/job-post.md``
      and ``hire-someone/acceptance-checklist.md``.

Deterministic-first: the structural assets are pure transforms of the blueprint /
coverage diff; the per-page drafts reuse :func:`aeo.recommender.draft.draft_missing_page`,
which emits a grounded scaffold with the LLM off and full prose when enabled. The
JSON-LD is always built in code (valid regardless of the prose path).

Pure core (:func:`build_asset_bundle` → :class:`AssetBundle`); the only I/O is
:meth:`AssetBundle.write`, which materializes the bundle to a directory.

Design: docs/superpowers/specs/2026-06-11-owner-mode-launch-kit-design.md
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

    def to_zip_bytes(self) -> bytes:
        """The whole bundle as a single in-memory ``.zip`` (every asset + manifest.json).
        Pure — no filesystem. Powers the API's one-click 'Download all' and any caller that
        wants the bundle as one artifact."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for asset in self.assets:
                zf.writestr(asset.path, asset.content)
            zf.writestr("manifest.json", json.dumps(self.manifest(), indent=2))
        return buf.getvalue()


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


# ── owner-mode assets (builder_mode diy / ai / hire) ─────────────────────────

_INTENT_JOB: dict[str, str] = {
    "informational": "answer the questions customers ask before they choose anyone",
    "commercial": "help a customer decide that this business is the right choice",
    "transactional": "get a ready-to-buy customer to take action (book, buy, or call)",
    "navigational": "help customers find, contact, and trust the business",
}

# Free listings every business should claim, regardless of category.
_UNIVERSAL_LISTINGS = [
    ("Google Business Profile", "https://business.google.com", "the single biggest source AI assistants use for local recommendations"),
    ("Bing Places", "https://www.bingplaces.com", "feeds Microsoft Copilot and Bing"),
    ("Apple Business Connect", "https://businessconnect.apple.com", "feeds Apple Maps and Siri"),
    ("Yelp", "https://biz.yelp.com", "widely quoted by AI assistants for local businesses"),
    ("Facebook page", "https://www.facebook.com/business", "expected by customers; another verified source of your details"),
]

# Category-keyword → directories AI assistants actually draw on for that space.
_CATEGORY_DIRECTORIES: list[tuple[tuple[str, ...], list[str]]] = [
    (("health", "medical", "dental", "clinic"), ["Healthgrades", "Zocdoc", "WebMD Care"]),
    (("legal", "law"), ["Avvo", "Justia", "FindLaw"]),
    (("restaurant", "food", "cafe"), ["TripAdvisor", "OpenTable", "Google Maps menus"]),
    (("real estate", "property"), ["Zillow", "Realtor.com"]),
    (("saas", "software", "tech"), ["G2", "Capterra", "Product Hunt"]),
    (("e-commerce", "retail", "shop"), ["Trustpilot", "Google Merchant Center"]),
    (("fitness", "wellness", "gym"), ["Mindbody", "ClassPass"]),
    (("automotive", "car"), ["CarGurus", "DealerRater"]),
    (("home services", "construction", "trades", "plumb", "electric"), ["Angi", "Thumbtack", "Houzz"]),
    (("hospitality", "travel", "hotel"), ["TripAdvisor", "Booking.com"]),
    (("marketing", "advertising", "consult"), ["Clutch", "G2"]),
    (("finance", "insurance", "accounting", "tax"), ["Trustpilot", "Better Business Bureau"]),
]


def _biz(business: dict[str, Any] | None) -> dict[str, Any]:
    b = business or {}
    return {
        "name": str(b.get("name") or "").strip() or "your business",
        "category": str(b.get("category") or "").strip(),
        "location": str(b.get("location") or "").strip(),
        "services": [str(s) for s in (b.get("services") or []) if str(s).strip()],
    }


def _chunk_weeks(nodes: list[Any]) -> list[list[Any]]:
    """Pages → up to three build weeks (3 / 3 / rest); week 4 is always visibility work."""
    return [w for w in (nodes[:3], nodes[3:6], nodes[6:10]) if w]


def _start_here_md(nodes: list[Any], business: dict[str, Any] | None, mode: str) -> str:
    biz = _biz(business)
    page_word = "page" if len(nodes) == 1 else "pages"
    build_line = {
        "diy": "Each file in `pages/` is a ready-to-paste draft — open it, create the page in your website builder, paste, and personalize.",
        "ai": "Each file in `prompts/` is a ready-made AI prompt — paste it into ChatGPT or your website builder's AI and use what it writes.",
        "hire": "Hand `hire-someone/job-post.md` to a freelancer, or build it yourself with the drafts in `pages/`.",
    }[mode]
    lines = [
        f"# Start here — your website plan for {biz['name']}",
        "",
        f"This folder is your complete launch kit: {len(nodes)} {page_word} to create, in the",
        "order that wins AI recommendations fastest. No technical skills needed.",
        "",
        f"**How it works:** {build_line}",
        "",
        "## Your 30-day plan",
        "",
    ]
    for week_num, week in enumerate(_chunk_weeks(nodes), start=1):
        lines.append(f"### Week {week_num} — build these {'first' if week_num == 1 else 'next'}")
        for n in week:
            lines.append(f"- **{_node_attr(n, 'title', '')}** (web address ending `{_node_attr(n, 'slug', '')}`)")
        lines.append("")
    lines += [
        "### Final week — get found everywhere else",
        "- Work through `get-found-now.md` (free listings + reviews — no website needed).",
        "- Read every new page once on your phone: typos, broken links, missing contact info.",
        "",
        "## What's in this folder",
        "",
    ]
    contents = {
        "diy": [
            "`pages/` — a ready-to-paste draft for every page",
            "`platform-tips.md` — where to click in Wix, Squarespace, WordPress & co.",
        ],
        "ai": [
            "`prompts/` — one copy-paste AI prompt per page (start with `prompts/how-to-use.md`)",
        ],
        "hire": [
            "`hire-someone/` — a ready-to-post job brief + a checklist to verify the work",
            "`pages/` — ready-to-paste drafts (useful to you *and* whoever you hire)",
            "`platform-tips.md` — where to click in Wix, Squarespace, WordPress & co.",
        ],
    }[mode]
    lines += [f"- {c}" for c in contents]
    lines += [
        "- `get-found-now.md` — visibility wins that need no website at all",
        "- `for-your-developer/` — technical files, in case a developer ever joins the project",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _owner_page_md(node: Any, *, topic: str, origin: str, llm: Any) -> Asset:
    """The diy/hire flavor of a page spec: same draft, owner-first framing, tech demoted."""
    from ..recommender.draft import draft_missing_page

    draft = draft_missing_page(node, topic=topic, llm=llm, origin=origin)
    p = draft.to_payload()
    job = _INTENT_JOB.get(draft.intent, _INTENT_JOB["informational"])
    jsonld = json.dumps(p["jsonld"], indent=2)
    body = (
        f"# {draft.h1 or _node_attr(node, 'title', '')}\n\n"
        f"**What this page is for:** {job}.\n\n"
        f"**Create a page called** “{_node_attr(node, 'title', '')}” with a web address ending "
        f"`{draft.slug}`, then paste the draft below and make it sound like you.\n\n"
        f"---\n\n{p['body_markdown']}\n\n---\n\n"
        "## Optional technical extra (skip if unsure)\n\n"
        "The snippet below helps AI assistants and search engines read this page. "
        "`platform-tips.md` shows exactly where to paste it on your platform.\n\n"
        f"```json\n{jsonld}\n```\n"
    )
    return Asset(path=f"pages/{_slug_filename(draft.slug)}.md", content=body, kind="page_draft")


def _ai_prompt_text(node: Any, business: dict[str, Any] | None, topic: str) -> str:
    """The paste-into-ChatGPT prompt body for a page — the AI half of #4's AI-vs-human
    prompts. Deterministic; shared by the ``prompts/`` asset and the structured plan."""
    biz = _biz(business)
    who = biz["name"]
    if biz["category"]:
        who += f", a {biz['category']} business"
    if biz["location"]:
        who += f" in {biz['location']}"
    title = _node_attr(node, "title", "")
    job = _INTENT_JOB.get(str(_node_attr(node, "intent", "")), _INTENT_JOB["informational"])
    questions = [str(q) for q in (_node_attr(node, "seed_questions", []) or [])]
    entities = [str(e) for e in (_node_attr(node, "required_entities", []) or [])]
    services = ", ".join(biz["services"])

    prompt_lines = [
        f"Write the full text for a web page called \"{title}\" for {who}.",
        f"The page's job: {job}.",
    ]
    if services:
        prompt_lines.append(f"What the business offers: {services}.")
    if questions:
        prompt_lines.append("The page must clearly answer these questions (use them as headings or an FAQ):")
        prompt_lines += [f"- {q}" for q in questions]
    if entities:
        prompt_lines.append(f"Mention these naturally where relevant: {', '.join(entities)}.")
    prompt_lines += [
        "Write 600 to 900 words. Start with a short, direct answer a reader (or an AI assistant)",
        "could quote on its own. Use clear section headings, end with a short FAQ, and keep the",
        "tone friendly and expert — plain language, no buzzwords, no filler.",
    ]
    return "\n".join(prompt_lines)


def _human_prompt_text(node: Any) -> str:
    """The do-it-yourself instruction for a page — the human half of #4's prompts.
    A concrete recipe an owner/writer can follow without any AI."""
    title = _node_attr(node, "title", "")
    questions = [str(q) for q in (_node_attr(node, "seed_questions", []) or [])]
    entities = [str(e) for e in (_node_attr(node, "required_entities", []) or [])]
    parts = [
        f"Write the “{title}” page yourself: open with a 2–3 sentence direct answer an AI "
        "assistant could quote on its own, then expand into clear sections.",
    ]
    if questions:
        parts.append("Make sure the page answers, as headings or an FAQ: " + "; ".join(questions) + ".")
    if entities:
        parts.append("Name these explicitly where relevant: " + ", ".join(entities) + ".")
    parts.append("Aim for 600–900 words in plain, expert language; end with a short FAQ.")
    return " ".join(parts)


def _prompt_md(node: Any, business: dict[str, Any] | None, topic: str) -> Asset:
    """One paste-into-ChatGPT prompt per page. Deterministic — no LLM call here."""
    title = _node_attr(node, "title", "")
    prompt_text = _ai_prompt_text(node, business, topic)
    body = (
        f"# AI prompt — {title}\n\n"
        f"Copy everything in the box into ChatGPT (or your website builder's AI assistant),\n"
        f"then paste the result into a new page with a web address ending `{_node_attr(node, 'slug', '')}`.\n\n"
        "```text\n" + prompt_text + "\n```\n\n"
        "After pasting the result: read it once and fix anything that isn't true for your business —\n"
        "AI sometimes invents specifics. Your real details always win.\n"
    )
    return Asset(path=f"prompts/{_slug_filename(str(_node_attr(node, 'slug', '/')))}.md", content=body, kind="prompt")


def _prompts_howto_md(nodes: list[Any]) -> str:
    return (
        "# How to use these prompts\n\n"
        f"There's one prompt file per page — {len(nodes)} in all, already in priority order\n"
        "(the `START-HERE.md` plan tells you which to do each week).\n\n"
        "1. Open a prompt file and copy everything inside the box.\n"
        "2. Paste it into ChatGPT, Claude, or your website builder's AI (Wix AI, Squarespace AI, Framer AI…).\n"
        "3. Create the page in your builder, paste the AI's text in, and personalize it.\n"
        "4. Read it once — fix anything that isn't true for your business.\n\n"
        "Tip: paste two or three of your real customer questions into the prompt too. The more of *you*\n"
        "in the prompt, the less generic the page.\n"
    )


def _platform_tips_md() -> str:
    return (
        "# Where to click on your website platform\n\n"
        "Two things come up while building from this kit: creating pages, and (optionally)\n"
        "pasting the technical snippet from the bottom of each page draft.\n\n"
        "## Wix\n"
        "- New page: **Site & Mobile App → Pages → Add Page**.\n"
        "- Snippet: open the page's **SEO settings → Advanced → Structured data**, paste there.\n\n"
        "## Squarespace\n"
        "- New page: **Pages → + → Blank Page**.\n"
        "- Snippet: page **Settings → Advanced → Page Header Code Injection**, paste inside\n"
        "  `<script type=\"application/ld+json\"> … </script>` tags.\n\n"
        "## WordPress\n"
        "- New page: **Pages → Add New**.\n"
        "- Snippet: easiest with a free plugin like **WPCode** (add a per-page header snippet),\n"
        "  or an SEO plugin (Yoast / Rank Math) that manages structured data for you.\n\n"
        "## GoDaddy / other simple builders\n"
        "- New page: look for **Pages → Add**.\n"
        "- Snippet: many simple builders don't support custom code — skip it. The page text\n"
        "  matters far more; add the snippets later if you ever move platforms.\n\n"
        "Stuck? The snippet is optional everywhere. Publish the pages first.\n"
    )


def _job_post_md(nodes: list[Any], business: dict[str, Any] | None, origin: str) -> str:
    biz = _biz(business)
    page_list = "\n".join(
        f"- {_node_attr(n, 'title', '')} (`{_node_attr(n, 'slug', '')}`)" for n in nodes[:12]
    )
    return (
        "# Job post — copy, tweak, and publish on Upwork / Fiverr / to your web person\n\n"
        "---\n\n"
        f"**Title:** Build {len(nodes)} website pages from a complete, ready-made plan\n\n"
        f"I run {biz['name']}"
        + (f", a {biz['category']} business" if biz["category"] else "")
        + (f" in {biz['location']}" if biz["location"] else "")
        + (f" ({origin})" if origin and "example.com" not in origin else "")
        + ".\n\n"
        "I have a complete launch kit (attached): a page list with priorities, a draft for every\n"
        "page, navigation and linking plans, and ready-made structured-data snippets. **No content\n"
        "writing or SEO strategy is needed — just build what's specified.**\n\n"
        "Scope:\n"
        f"{page_list}\n\n"
        "For each page: create it at the given address, use the matching draft from `pages/`,\n"
        "add the JSON-LD snippet from the draft, and follow `for-your-developer/navigation.md`\n"
        "and `internal-linking.md` for menus and links between pages.\n\n"
        "Please quote a fixed price and timeline for the full list.\n\n"
        "---\n\n"
        "_Tip: attach this whole kit folder (or the .zip) to the job post._\n"
    )


def _acceptance_checklist_md(nodes: list[Any]) -> str:
    lines = [
        "# How to check the work (no technical skills needed)",
        "",
        "Go through this list before paying the final invoice.",
        "",
        "## Every page",
    ]
    for n in nodes[:12]:
        lines.append(f"- [ ] **{_node_attr(n, 'title', '')}** exists at an address ending `{_node_attr(n, 'slug', '')}`")
    lines += [
        "",
        "## Quality checks (pick any 3 pages)",
        "- [ ] The page text matches the draft (personalized is fine; missing sections are not).",
        "- [ ] The questions listed in the draft are answered on the page.",
        "- [ ] The page reads well on your phone.",
        "",
        "## Technical checks (still no skills needed)",
        "- [ ] Paste a page's web address into validator.schema.org — it should find at least one item, with no errors.",
        "- [ ] Click every menu item — no dead links.",
        "- [ ] Each page links to at least one related page on your site.",
        "",
        "If something fails, point the builder at the exact file in this kit that specifies it.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _get_found_now_md(business: dict[str, Any] | None) -> str:
    biz = _biz(business)
    cat = biz["category"].lower()
    extra = next((dirs for keys, dirs in _CATEGORY_DIRECTORIES if any(k in cat for k in keys)), [])
    services = ", ".join(biz["services"][:5])
    lines = [
        "# Get found now — no website needed",
        "",
        "AI assistants don't just read websites: when someone asks for a recommendation, they",
        "lean heavily on business listings, maps, and reviews. Everything below is free and",
        "takes an afternoon — for many businesses it moves the needle faster than any web page.",
        "",
        "## 1. Claim your Google Business Profile (do this first)",
        "",
        f"- Business name: **{biz['name']}** — exactly as customers know it, everywhere.",
    ]
    if biz["category"]:
        lines.append(f"- Category: pick the closest match to *{biz['category']}* (you can add more than one).")
    if services:
        lines.append(f"- Services: list each one separately — e.g. {services}.")
    if biz["location"]:
        lines.append(f"- Address / service area: {biz['location']} — keep it identical on every site you list on.")
    lines += [
        "- Description: open with what you do and for whom in the first sentence — that's the part AI quotes.",
        "- Add real photos and your opening hours; profiles with both get recommended far more.",
        "",
        "## 2. Claim the other free listings",
        "",
    ]
    for name_, url, why in _UNIVERSAL_LISTINGS:
        lines.append(f"- **{name_}** ({url}) — {why}.")
    if extra:
        lines += ["", "### Worth it for your industry", ""]
        lines += [f"- **{d}**" for d in extra]
    lines += [
        "",
        "## 3. Reviews — the fuel for AI recommendations",
        "",
        "Ask every happy customer, the same day, with a direct link to your Google profile:",
        "",
        f"> Thanks again for choosing {biz['name']}! If you have 60 seconds, a quick Google review",
        "> helps people like you find us: [your review link]",
        "",
        "- Reply to every review, good or bad — politely and briefly. AI assistants notice responsiveness.",
        "- Never buy reviews; a steady trickle of real ones beats a suspicious burst.",
        "",
        "## One rule across everything",
        "",
        "Use the **exact same name, address, and phone number** on every listing. Mismatches make",
        "AI assistants unsure you're the same business — consistency is credibility.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def checklist_for(
    *, blueprint: Blueprint, coverage: CoverageDiffResult | None, builder_mode: str
) -> dict[str, Any]:
    """:func:`build_checklist` over the same to-build nodes the bundle uses."""
    return build_checklist(_target_nodes(blueprint, coverage), builder_mode)


def build_checklist(nodes: list[Any], builder_mode: str) -> dict[str, Any]:
    """The kit's plan as structured data — the same weeks as ``START-HERE.md``, so the
    UI can render an interactive checklist instead of asking owners to open files.
    Task ids are stable (page slug / fixed visibility ids) so client-side progress
    survives kit regeneration."""
    weeks: list[dict[str, Any]] = []
    for i, week in enumerate(_chunk_weeks(nodes), start=1):
        weeks.append({
            "title": f"Week {i}",
            "blurb": "Build these first" if i == 1 else "Build these next",
            "tasks": [
                {
                    "id": f"page:{_node_attr(n, 'slug', '')}",
                    "label": f"Create the “{_node_attr(n, 'title', '')}” page",
                    "detail": f"web address ending {_node_attr(n, 'slug', '')}",
                }
                for n in week
            ],
        })
    if builder_mode != "dev":
        weeks.append({
            "title": "Final week",
            "blurb": "Get found everywhere else — no website needed",
            "tasks": [
                {"id": "vis:gbp", "label": "Claim your Google Business Profile",
                 "detail": "step 1 in get-found-now.md"},
                {"id": "vis:listings", "label": "Claim the other free listings",
                 "detail": "Bing, Apple, Yelp, Facebook — step 2 in get-found-now.md"},
                {"id": "vis:reviews", "label": "Ask three happy customers for a Google review",
                 "detail": "the ready-made ask script is in get-found-now.md"},
                {"id": "vis:readthrough", "label": "Read every new page once on your phone",
                 "detail": "typos, broken links, missing contact info"},
            ],
        })
    total = sum(len(w["tasks"]) for w in weeks)
    return {"weeks": weeks, "total": total}


# ── phased plan (#8 phases · #9 quick wins · #4 AI-vs-human prompts · #10 JSON) ──

# Phase keys (#8): essentials first, then the rest of the month, then later.
PHASE_WEEK_1 = "week_1"
PHASE_WEEK_2_4 = "week_2_4"
PHASE_LATER = "later"

_PHASE_TITLES: dict[str, str] = {
    PHASE_WEEK_1: "Week 1 — essentials",
    PHASE_WEEK_2_4: "Weeks 2–4",
    PHASE_LATER: "Later",
}
_PHASE_BLURBS: dict[str, str] = {
    PHASE_WEEK_1: "The highest-impact pages — do these first.",
    PHASE_WEEK_2_4: "Round out coverage and authority.",
    PHASE_LATER: "Nice-to-have pages once the essentials are live.",
}
_PHASE_ORDER = (PHASE_WEEK_1, PHASE_WEEK_2_4, PHASE_LATER)

# Effort by page type — creating a contact/about page is low lift; a pillar is high.
_EFFORT_BY_TYPE: dict[str, str] = {
    "contact": "low", "about": "low", "faq": "low", "utility": "low", "homepage": "low",
    "blog": "medium", "default": "medium", "solution": "medium",
    "pillar": "high", "product": "high",
}
_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


def _load_phasing_cfg() -> dict[str, Any]:
    """Phasing knobs from config/prioritization.yaml (code defaults if absent)."""
    from ..settings import load_yaml_file

    raw = (load_yaml_file("prioritization.yaml") or {}).get("phasing") or {}
    return {
        "week_1_top_n": int(raw.get("week_1_top_n", 3)),
        "week_2_4_top_n": int(raw.get("week_2_4_top_n", 10)),
        "quick_win_max_effort": str(raw.get("quick_win_max_effort", "low")),
        "quick_win_min_priority": float(raw.get("quick_win_min_priority", 0.6)),
    }


def _phase_for_rank(rank: int, cfg: dict[str, Any]) -> str:
    if rank < cfg["week_1_top_n"]:
        return PHASE_WEEK_1
    if rank < cfg["week_2_4_top_n"]:
        return PHASE_WEEK_2_4
    return PHASE_LATER


def _is_quick_win(effort: str, priority: float, cfg: dict[str, Any]) -> bool:
    max_rank = _EFFORT_RANK.get(cfg["quick_win_max_effort"], 0)
    return _EFFORT_RANK.get(effort, 1) <= max_rank and priority >= cfg["quick_win_min_priority"]


def _page_plan_task(
    node: Any, rank: int, *, business: dict[str, Any] | None, topic: str, cfg: dict[str, Any]
) -> dict[str, Any]:
    """One enriched plan task for a page to build: phase, quick-win flag, the three
    Block-A fields, and both AI and human implementation prompts (#4)."""
    slug = _node_attr(node, "slug", "")
    title = _node_attr(node, "title", "")
    page_type = str(_node_attr(node, "page_type", "default"))
    priority = float(_node_attr(node, "priority", 0.0) or 0.0)
    effort = _EFFORT_BY_TYPE.get(page_type, "medium")
    phase = _phase_for_rank(rank, cfg)
    return {
        "id": f"page:{slug}",
        "label": f"Create the “{title}” page",
        "detail": f"web address ending {slug}",
        "phase": phase,
        "quick_win": _is_quick_win(effort, priority, cfg),
        "effort": effort,
        "priority": round(priority, 3),
        # Block A's three plain-language fields, for a page that doesn't exist yet.
        "current_state": "This page doesn't exist on your site yet.",
        "action_required": f"Create the “{title}” page at a web address ending {slug}.",
        "how_to": (
            "Use the AI prompt below (or the matching draft in pages/) to write it, publish it "
            f"at {slug}, then link it into your navigation."
        ),
        # #4 — AI-vs-human implementation prompts, embedded in the payload.
        "prompts": {
            "ai": _ai_prompt_text(node, business, topic),
            "human": _human_prompt_text(node),
        },
    }


# Visibility wins (owner modes) — no website needed; inherently low-effort/high-impact.
_VIS_PLAN_TASKS: list[dict[str, Any]] = [
    {
        "id": "vis:gbp", "label": "Claim your Google Business Profile", "phase": PHASE_WEEK_1,
        "quick_win": True, "effort": "low",
        "current_state": "Your business may not appear (or appears unverified) in Google's local data.",
        "action_required": "Claim and fully fill out your Google Business Profile.",
        "how_to": "Follow step 1 in get-found-now.md — name, category, services, hours, photos.",
    },
    {
        "id": "vis:listings", "label": "Claim the other free listings", "phase": PHASE_WEEK_2_4,
        "quick_win": True, "effort": "low",
        "current_state": "Bing, Apple, Yelp and Facebook have no verified listing for you.",
        "action_required": "Claim Bing Places, Apple Business Connect, Yelp and a Facebook page.",
        "how_to": "Follow step 2 in get-found-now.md — keep name/address/phone identical everywhere.",
    },
    {
        "id": "vis:reviews", "label": "Ask three happy customers for a Google review", "phase": PHASE_WEEK_2_4,
        "quick_win": True, "effort": "low",
        "current_state": "Few or no recent reviews — the fuel AI assistants weigh most for recommendations.",
        "action_required": "Ask three recent happy customers for a Google review, same day.",
        "how_to": "Use the ready-made ask script in get-found-now.md with a direct review link.",
    },
    {
        "id": "vis:readthrough", "label": "Read every new page once on your phone", "phase": PHASE_WEEK_2_4,
        "quick_win": False, "effort": "low",
        "current_state": "New pages haven't been proofed on a real device.",
        "action_required": "Read every new page once on your phone.",
        "how_to": "Check for typos, broken links, and missing contact info; fix as you go.",
    },
]


def plan_for(
    *,
    blueprint: Blueprint,
    coverage: CoverageDiffResult | None,
    builder_mode: str,
    business: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """:func:`build_plan` over the same to-build nodes the bundle uses."""
    return build_plan(
        _target_nodes(blueprint, coverage), builder_mode,
        business=business, topic=blueprint.topic,
    )


def build_plan(
    nodes: list[Any],
    builder_mode: str,
    *,
    business: dict[str, Any] | None = None,
    topic: str = "",
) -> dict[str, Any]:
    """The prioritized plan as structured JSON (#10): phased (#8), quick-wins flagged
    (#9), each task carrying current_state/action_required/how_to (Block A) and both an
    AI and a human implementation prompt (#4).

    Pure and deterministic; the same priority-ordered nodes as the kit, so a task id
    (stable page slug / fixed visibility id) lines up with the bundle assets and lets
    client-side progress survive a regeneration."""
    cfg = _load_phasing_cfg()
    tasks: list[dict[str, Any]] = [
        _page_plan_task(n, rank, business=business, topic=topic, cfg=cfg)
        for rank, n in enumerate(nodes)
    ]
    # Visibility wins ride along in the owner-facing modes (no website needed).
    if builder_mode != "dev":
        tasks += [dict(t) for t in _VIS_PLAN_TASKS]

    phases = [
        {
            "key": key,
            "title": _PHASE_TITLES[key],
            "blurb": _PHASE_BLURBS[key],
            "tasks": [t for t in tasks if t["phase"] == key],
        }
        for key in _PHASE_ORDER
    ]
    phases = [p for p in phases if p["tasks"]]  # drop empty phases
    quick_win_ids = [t["id"] for t in tasks if t.get("quick_win")]
    return {
        "phases": phases,
        "quick_win_ids": quick_win_ids,
        "quick_win_count": len(quick_win_ids),
        "total": len(tasks),
    }


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
    builder_mode: str = "dev",
    business: dict[str, Any] | None = None,
) -> AssetBundle:
    """Assemble the launch-kit bundle from a blueprint (+ optional coverage diff and
    SiteProfile dict), shaped for whoever is building (``builder_mode``: dev | diy |
    ai | hire — see the module docstring). ``business`` ({name, category, location,
    services}) personalizes the owner-mode assets. ``draft_limit`` caps the per-page
    drafts/prompts (in dev/diy/hire the drafts are the expensive, LLM-backed step);
    the rest of the bundle covers every page. Pure — call :meth:`AssetBundle.write`
    to materialize it."""
    org = _origin(origin or (profile or {}).get("domain"))
    nodes = _target_nodes(blueprint, coverage)
    bundle_name = name or blueprint.topic

    dev_assets: list[Asset] = [
        Asset("README.md", _readme_md(blueprint, nodes, org, profile is not None), "readme"),
        Asset("sitemap.xml", _sitemap_xml(blueprint, org), "sitemap"),
        Asset("navigation.md", _navigation_md(blueprint), "nav"),
        Asset("content-briefs.md", _content_briefs_md(blueprint, nodes), "content_briefs"),
        Asset("internal-linking.md", _internal_linking_md(blueprint), "linking"),
        Asset("schema-and-entities.md", _schema_entities_md(blueprint), "schema"),
    ]
    if profile is not None:
        dev_assets.append(Asset("STRATEGY.md", _strategy_md(profile), "strategy"))

    if builder_mode == "dev":
        assets = dev_assets
        if draft_limit > 0:
            for node in nodes[:draft_limit]:
                assets.append(_page_spec_md(node, topic=blueprint.topic, origin=org, llm=llm))
        return AssetBundle(name=bundle_name, assets=assets)

    # Owner modes (diy / ai / hire): owner files at the root, the dev bundle kept
    # under for-your-developer/ so a later hire never requires regenerating.
    assets = [
        Asset("START-HERE.md", _start_here_md(nodes, business, builder_mode), "start_here"),
        Asset("get-found-now.md", _get_found_now_md(business), "visibility"),
    ]

    if builder_mode in ("diy", "hire"):
        assets.append(Asset("platform-tips.md", _platform_tips_md(), "tips"))
        for node in nodes[:draft_limit]:
            assets.append(_owner_page_md(node, topic=blueprint.topic, origin=org, llm=llm))

    if builder_mode == "ai":
        assets.append(Asset("prompts/how-to-use.md", _prompts_howto_md(nodes[:draft_limit]), "tips"))
        for node in nodes[:draft_limit]:
            assets.append(_prompt_md(node, business, blueprint.topic))

    if builder_mode == "hire":
        assets.append(Asset("hire-someone/job-post.md", _job_post_md(nodes, business, org), "hire"))
        assets.append(Asset("hire-someone/acceptance-checklist.md", _acceptance_checklist_md(nodes), "hire"))

    assets += [Asset(f"for-your-developer/{a.path}", a.content, a.kind) for a in dev_assets]
    return AssetBundle(name=bundle_name, assets=assets)
