"""Approved-run export — the moment approval starts producing something.

An approved agent run's drafts (``agent_runs.result.tasks[].draft`` — the stored
PageDraft payloads the human reviewed) convert into the same ``pages/<slug>.md``
launch-kit assets the deliverables packager ships. Nothing is re-generated: what the
reviewer approved is byte-for-byte what exports. Agent drafts are often
``draft_quality="full"`` (LLM-written) where the instant wizard bundle is
``"scaffold"``, so this is the upgrade path for the kit.
"""

from __future__ import annotations

import json
from typing import Any

# _slug_filename is the packager's single source of truth for slug→filename mapping;
# sharing it keeps agent exports and wizard bundles naming pages identically.
from ..report.packager import Asset, AssetBundle, _slug_filename


def bundle_from_run(run: dict[str, Any]) -> AssetBundle:
    """The launch-kit bundle for an approved run: one ``pages/<slug>.md`` per drafted
    task (rendered exactly like the packager's page specs, from the stored payload)
    plus a README naming the run and any critic flags the reviewer accepted."""
    result = run.get("result") or {}
    tasks = [t for t in result.get("tasks") or [] if t.get("draft")]
    return AssetBundle(
        name=f"agent-run-{run.get('id', 'unknown')}",
        assets=[_readme_asset(run, tasks), *(_page_asset(t) for t in tasks)],
    )


def _task_slug(task: dict[str, Any]) -> str:
    node = task.get("node") or {}
    fallback = str(task.get("id") or "page")
    return str(task.get("slug") or node.get("slug") or fallback.removeprefix("page:"))


def _page_asset(task: dict[str, Any]) -> Asset:
    """Mirror of the packager's ``_page_spec_md`` rendering, fed from the STORED draft
    payload instead of a fresh ``draft_missing_page`` call."""
    p = task.get("draft") or {}
    node = task.get("node") or {}
    header = (
        f"<!-- page spec: {task.get('page_type') or node.get('page_type') or 'page'}/"
        f"{node.get('intent') or 'informational'} · "
        f"generator={p.get('generator')} · quality={p.get('draft_quality')} -->\n\n"
    )
    jsonld = json.dumps(p.get("jsonld") or [], indent=2)
    body = (
        f"{header}{p.get('body_markdown', '')}\n\n"
        f"## JSON-LD (paste into <head>)\n\n```json\n{jsonld}\n```\n"
    )
    return Asset(path=f"pages/{_slug_filename(_task_slug(task))}.md", content=body, kind="page_spec")


def _readme_asset(run: dict[str, Any], tasks: list[dict[str, Any]]) -> Asset:
    result = run.get("result") or {}
    flagged = sum(1 for t in tasks if (t.get("critic") or {}).get("needs_review"))
    lines = [
        f"# Approved page drafts — {result.get('domain') or run.get('domain') or 'your site'}",
        "",
        f"Agent run `{run.get('id', '')}` — {len(tasks)} page draft(s), approved by a human reviewer.",
        "Each file under `pages/` is a ready-to-edit draft: the page copy in markdown plus",
        "the JSON-LD block to paste into that page's `<head>`.",
        "",
    ]
    if flagged:
        lines += [
            f"⚠ {flagged} draft(s) carried \"claims to verify\" critic flags at review time —",
            "double-check those facts before the pages go live.",
            "",
        ]
    lines += [f"- `pages/{_slug_filename(_task_slug(t))}.md` — {t.get('title', '')}" for t in tasks]
    return Asset(path="README.md", content="\n".join(lines).rstrip() + "\n", kind="readme")
