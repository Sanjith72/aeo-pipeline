"""
Developer Handoff brief — turn one milestone task into developer-ready instructions.

:func:`generate_dev_brief` reads a persisted ``milestone_tasks`` row (its ``verify_kind`` +
``verify_target``) and emits a concrete, paste-able technical brief: the exact page to
build with a ready JSON-LD ``<script>`` block, the exact heading tag to add, etc. — plus a
"how we verify" line that mirrors what the weekly crawl (:mod:`aeo.intelligence.milestone_verify`)
actually checks, so the developer knows precisely what closes the task.

Pure + deterministic (no DB, no network). :func:`attach_dev_briefs` walks a dashboard
payload and attaches ``dev_brief`` to every task, so both the owner dashboard and the
read-only share view render the same brief. ``origin`` (the client's domain) is optional —
given it, page/schema briefs use absolute URLs; without it they fall back to the path.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

# schema.org @type to recommend per page-slug shape (best-effort, from the slug alone —
# the milestone row doesn't carry page_type). Mirrors packager._SCHEMA_FOR_TYPE intent.
_SLUG_SCHEMA: list[tuple[tuple[str, ...], str]] = [
    (("/services", "/service/", "/solutions", "/solution/", "/treatments"), "Service"),
    (("/products", "/product/", "/shop"), "Product"),
    (("/about",), "AboutPage"),
    (("/contact",), "ContactPage"),
    (("/blog", "/news", "/articles", "/guide", "/resources"), "Article"),
    (("/faq", "/faqs"), "FAQPage"),
]


def _origin_host(origin: str | None) -> str | None:
    """Bare ``https://host`` for an origin/domain, or None when not given."""
    if not origin:
        return None
    parts = urlsplit(origin if "://" in origin else f"https://{origin}")
    host = parts.netloc or parts.path.split("/", 1)[0]
    return f"https://{host}" if host else None


def _abs_url(origin: str | None, path: str) -> str:
    """Absolute URL for a slug when we know the origin; else the path itself."""
    p = "/" + (path or "").strip().lstrip("/")
    host = _origin_host(origin)
    return f"{host}{p}" if host else p


def _title_from_label(label: str, fallback_slug: str = "") -> str:
    """The offering/page name a task is about, pulled from its label.

    Plan labels look like ``Create the “Commercial Roofing” page`` — grab the quoted name
    (straight or curly quotes); else fall back to the slug's last segment, title-cased."""
    m = re.search(r"[\"“']([^\"”']{2,80})[\"”']", label or "")
    if m:
        return m.group(1).strip()
    seg = (fallback_slug or "").rstrip("/").rsplit("/", 1)[-1]
    return seg.replace("-", " ").replace("_", " ").strip().title() or "this page"


def _schema_type_for(slug: str) -> str:
    s = (slug or "").lower()
    for needles, schema_type in _SLUG_SCHEMA:
        if any(n in s for n in needles):
            return schema_type
    return "WebPage"


def _jsonld_block(schema_type: str, name: str, url: str) -> str:
    data: dict[str, Any] = {"@context": "https://schema.org", "@type": schema_type, "name": name, "url": url}
    if schema_type == "Service":
        data["serviceType"] = name
        data["provider"] = {"@type": "Organization", "name": "Your Business"}
    payload = json.dumps(data, indent=2)
    return f'<script type="application/ld+json">\n{payload}\n</script>'


def _page_brief(target: str, label: str, how_to: str, origin: str | None) -> str:
    title = _title_from_label(label, target)
    url = _abs_url(origin, target)
    schema_type = _schema_type_for(target)
    return "\n".join([
        f"TASK: {label}",
        "TYPE: New page",
        f"URL PATH: {url}",
        "",
        "IMPLEMENTATION",
        f"1. Create a new page reachable at {url}",
        f'2. Set the H1 to "{title}" and write a descriptive <title> + meta description.',
        "3. Paste this JSON-LD into the page's <head> (it's how answer engines read the page):",
        "",
        _jsonld_block(schema_type, title, url),
        "",
        "HOW WE VERIFY",
        f"The weekly tracking crawl marks this Verified automatically once a page is live at "
        f"{target} (or a heading/offering matching \"{title}\" appears on the site).",
    ])


def _heading_brief(target: str, label: str, origin: str | None) -> str:
    tag = f"<h2>{target}</h2>"
    return "\n".join([
        f"TASK: {label}",
        "TYPE: On-page heading / DOM change",
        "",
        "IMPLEMENTATION",
        "Add the following heading to the relevant page so it appears in the rendered DOM:",
        "",
        f"    {tag}",
        "",
        "Use a real heading element (an <h2> or <h3>) — not a styled <div> — so it's machine-readable.",
        "",
        "HOW WE VERIFY",
        f'The weekly crawl marks this Verified once a heading matching "{target}" is live on the site.',
    ])


def _service_brief(target: str, label: str, origin: str | None) -> str:
    url = _abs_url(origin, "/services/" + re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-"))
    return "\n".join([
        f"TASK: {label}",
        "TYPE: New offering",
        "",
        "IMPLEMENTATION",
        f'1. List "{target}" as an offering in the primary navigation (e.g. under a Services menu).',
        f"2. Create a dedicated section or page for it (suggested path: {url}).",
        "3. Add Service JSON-LD to that page's <head>:",
        "",
        _jsonld_block("Service", target, url),
        "",
        "HOW WE VERIFY",
        f'The weekly crawl marks this Verified once "{target}" appears as an offering, '
        "nav item, or heading on the site.",
    ])


def _manual_brief(label: str, how_to: str) -> str:
    steps = how_to.strip() or "Complete this step in the relevant third-party tool."
    return "\n".join([
        f"TASK: {label}",
        "TYPE: Off-site (no website code change)",
        "",
        "WHAT TO DO",
        steps,
        "",
        "HOW WE VERIFY",
        "This step lives outside the website, so the tracker can't auto-detect it — "
        "mark it done in the dashboard once it's complete.",
    ])


# ── "I'll do it myself" — raw snippet + CMS-specific step-by-step ────────────────
#
# Two complementary outputs for the self-serve tab: a strictly paste-able artifact
# (``raw_snippet`` — just the JSON-LD <script> or the bare heading tag, nothing else, so a
# "Copy code" button copies exactly what goes on the page) and a numbered walkthrough
# (``diy_steps``) tailored to the detected CMS. Schema/page tasks share the "inject into
# <head>" flow; heading tasks edit on-page content.

_CMS_WORDPRESS = "wordpress"
_CMS_SHOPIFY = "shopify"
_CMS_UNKNOWN = "unknown"

# Per-CMS steps for tasks that need a snippet in the global <head> (JSON-LD schema).
_SCHEMA_STEPS: dict[str, list[str]] = {
    _CMS_WORDPRESS: [
        "Log into your WordPress Admin",
        "Install a plugin like 'Header and Footer Code Manager' OR go to "
        "Appearance > Theme File Editor > header.php",
        "Paste the code snippet below right before the closing </head> tag",
        "Save changes and clear any caching plugins",
    ],
    _CMS_SHOPIFY: [
        "Log into your Shopify Admin",
        "Go to Online Store > Themes",
        "Click the '...' next to your active theme and select 'Edit Code'",
        "Open 'theme.liquid' and paste the code snippet below right before the closing "
        "</head> tag",
        "Click Save",
    ],
    _CMS_UNKNOWN: [
        "Open your website's content management system (CMS) or custom code settings",
        "Locate the global header injection area",
        "Paste the code snippet below right before the closing </head> tag",
        "Save and publish",
    ],
}

# Per-CMS steps for on-page heading tasks (edit the page's content, not the <head>).
_HEADING_STEPS: dict[str, list[str]] = {
    _CMS_WORDPRESS: [
        "Log into your WordPress Admin",
        "Open the page or post where this section belongs in the block editor",
        "Add a Heading block and set its level to H2",
        "Paste the heading text below into that block, then Update the page",
    ],
    _CMS_SHOPIFY: [
        "Log into your Shopify Admin",
        "Go to Online Store > Pages (or the relevant theme section) and open the page",
        "Add the heading below as a real H2 — use the rich-text Heading style, not bold text",
        "Click Save",
    ],
    _CMS_UNKNOWN: [
        "Open your website editor and go to the relevant page",
        "Add a real heading element (an <h2>, not a styled <div>) using the text below",
        "Save and publish the page",
    ],
}

# Off-site tasks have no website code change — there's nothing to paste, just attest.
_MANUAL_STEPS: list[str] = [
    "This step happens outside your website — there's no code to paste",
    "Follow the 'How to do it' note above in the relevant third-party tool",
    "Mark this task as done in the dashboard once it's complete",
]


def _normalize_cms(cms_type: str | None) -> str:
    cms = (cms_type or "").strip().lower()
    return cms if cms in (_CMS_WORDPRESS, _CMS_SHOPIFY) else _CMS_UNKNOWN


def _is_schema_kind(kind: str) -> bool:
    """Kinds whose self-serve artifact is a JSON-LD <head> injection."""
    return kind in ("schema", "page", "service")


def generate_raw_snippet(task: dict[str, Any], *, origin: str | None = None) -> str | None:
    """The strictly paste-able artifact for the "I'll do it myself" tab, or None when the
    task has no on-page snippet (off-site/manual work).

    schema/page → the JSON-LD ``<script>`` block (inferred @type + verify_target);
    heading → the bare ``<h2>{verify_target}</h2>`` tag."""
    kind = str(task.get("verify_kind") or "manual")
    target = str(task.get("verify_target") or "").strip()
    label = str(task.get("label") or "").strip()

    if _is_schema_kind(kind):
        if kind == "service":
            name = target or _title_from_label(label)
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            return _jsonld_block("Service", name, _abs_url(origin, "/services/" + slug))
        if not target:
            return None
        title = _title_from_label(label, target)
        return _jsonld_block(_schema_type_for(target), title, _abs_url(origin, target))
    if kind == "heading":
        return f"<h2>{target}</h2>" if target else None
    return None


def generate_diy_steps(task: dict[str, Any], *, cms_type: str | None = None) -> list[str]:
    """The CMS-aware, numbered "do it yourself" walkthrough for one task.

    Shaped by ``verify_kind`` (where the change goes) and ``cms_type`` (how to get there):
    schema/page tasks inject into the global ``<head>``; heading tasks edit on-page
    content; everything else is off-site and owner-attested."""
    kind = str(task.get("verify_kind") or "manual")
    cms = _normalize_cms(cms_type)
    if _is_schema_kind(kind):
        return list(_SCHEMA_STEPS[cms])
    if kind == "heading":
        return list(_HEADING_STEPS[cms])
    return list(_MANUAL_STEPS)


def generate_dev_brief(task: dict[str, Any], *, origin: str | None = None) -> str:
    """A developer-ready technical brief for one milestone task.

    ``task`` is a milestone-task dict ({label, action_required, how_to, verify_kind,
    verify_target}). The brief is shaped by ``verify_kind``; ``origin`` (the site's domain)
    promotes page/schema URLs to absolute form when available."""
    kind = str(task.get("verify_kind") or "manual")
    target = str(task.get("verify_target") or "").strip()
    label = str(task.get("label") or "").strip() or "Implement this change"
    how_to = str(task.get("how_to") or "")

    if kind == "page" and target:
        return _page_brief(target, label, how_to, origin)
    if kind == "heading" and target:
        return _heading_brief(target, label, origin)
    if kind == "service" and target:
        return _service_brief(target, label, origin)
    return _manual_brief(label, how_to)


def attach_dev_briefs(
    dashboard: dict[str, Any], *, origin: str | None = None, cms_type: str | None = None
) -> dict[str, Any]:
    """Attach the per-task handoff fields to every task in a dashboard payload (in place).

    Each task gets the "Send to Developer" brief (``dev_brief``) plus the "I'll do it
    myself" pair: ``raw_snippet`` (paste-able code, or None) and ``diy_steps`` (the
    CMS-specific walkthrough). Both owner and read-only share views call this, so the two
    tabs render identically everywhere."""
    for milestone in dashboard.get("milestones", []) or []:
        for task in milestone.get("tasks", []) or []:
            task["dev_brief"] = generate_dev_brief(task, origin=origin)
            task["raw_snippet"] = generate_raw_snippet(task, origin=origin)
            task["diy_steps"] = generate_diy_steps(task, cms_type=cms_type)
    return dashboard
