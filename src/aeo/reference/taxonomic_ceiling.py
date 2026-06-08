"""
Taxonomic Ceiling — the per-category regulatory/authority "ceiling" half of L2.

The Reference Architecture Generator fuses two layers: an empirical *floor* (what
competitors actually publish) and a curated *ceiling* (what better-than-competitor
looks like). For a single topic that ceiling lived implicitly in
``config/framework.yaml``'s ``required_entities`` (MITRE ATT&CK, CVSS, …). This
module generalizes it so the tool is no longer hardwired to cybersecurity: pass a
``category`` and you get an industry-appropriate ceiling — the standards bodies,
regulations, and authoritative sources a site in that vertical must speak to.

Two ways the ceiling is produced, deterministic-first:

  * **Curated seed (deterministic).** A small map of well-known verticals
    (healthcare → HIPAA + medical peer-review; finance → SEC/CFPB/FINRA; …). Resolves
    through forgiving aliases ("personal finance" → finance, "infosec" → cybersecurity)
    so common inputs land without an LLM. cybersecurity is just one entry now, not a
    special case.
  * **LLM synthesis (dynamic).** :func:`ceiling_prompt_clause` injects an instruction
    that makes the bootstrap LLM synthesize the ceiling for *any* category — seeding
    from the curated standards when known, inventing the right ones when not. So an
    unseen vertical ("veterinary telehealth") still gets a sensible regulatory ceiling.

Pure data + string helpers. No I/O, no model calls — callers (framework_bootstrap)
decide how to apply it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TaxonomicCeiling:
    """The regulatory/authority ceiling for one vertical.

    ``standards`` are the compliance/regulatory entities that belong in a blueprint's
    ``required_entities`` (the coverage map's ceiling); ``authorities`` are the bodies a
    page should cite for E-E-A-T; ``summary`` is the one-line framing fused into prompts."""

    category: str            # canonical category key
    label: str               # human-readable label
    standards: list[str] = field(default_factory=list)
    authorities: list[str] = field(default_factory=list)
    summary: str = ""


# ── Curated ceilings for common verticals ────────────────────────────────────
# Deliberately small + high-signal: the LLM expands on these (see ceiling_prompt_clause).
# Add a vertical = add an entry; no code change elsewhere.
_CEILINGS: dict[str, TaxonomicCeiling] = {
    "healthcare": TaxonomicCeiling(
        category="healthcare",
        label="Healthcare / Medical",
        standards=["HIPAA", "HITECH", "HITRUST", "FDA 21 CFR Part 11", "ICH-GCP"],
        authorities=["FDA", "NIH", "CDC", "WHO", "peer-reviewed journals (PubMed)"],
        summary=(
            "HIPAA/HITECH data-privacy compliance and medical peer-review standards — "
            "claims should be evidence-based and cite clinical or regulatory authorities."
        ),
    ),
    "finance": TaxonomicCeiling(
        category="finance",
        label="Personal Finance / Financial Services",
        standards=["SEC regulations", "CFPB guidelines", "FINRA rules", "SOX", "GAAP", "Regulation Z"],
        authorities=["SEC", "CFPB", "FINRA", "Federal Reserve", "IRS"],
        summary=(
            "SEC/FINRA disclosure rules and CFPB consumer-protection guidelines — "
            "advice should be compliant, disclosed, and sourced to financial regulators."
        ),
    ),
    "ecommerce_saas": TaxonomicCeiling(
        category="ecommerce_saas",
        label="E-commerce / SaaS",
        standards=["PCI DSS", "SOC 2", "GDPR", "CCPA", "ISO 27001"],
        authorities=["PCI Security Standards Council", "NIST", "Cloud Security Alliance"],
        summary=(
            "PCI DSS payment security, SOC 2 trust criteria, and GDPR/CCPA data-privacy "
            "compliance — trust and security posture are the ceiling buyers evaluate against."
        ),
    ),
    "legal": TaxonomicCeiling(
        category="legal",
        label="Legal Services",
        standards=["ABA Model Rules", "state bar advertising rules", "attorney-client privilege", "GDPR"],
        authorities=["American Bar Association", "state bar associations", "primary case law and statutes"],
        summary=(
            "ABA Model Rules and state-bar advertising/ethics constraints — content must be "
            "accurate, non-misleading, and grounded in primary statutes and case law."
        ),
    ),
    "cybersecurity": TaxonomicCeiling(
        category="cybersecurity",
        label="Cybersecurity",
        standards=["MITRE ATT&CK", "CVSS", "NIST CSF", "ISO 27001", "CISA KEV"],
        authorities=["NIST", "CISA", "MITRE", "OWASP"],
        summary=(
            "MITRE ATT&CK technique coverage, CVSS/KEV severity grounding, and NIST/ISO "
            "control frameworks — claims should map to recognized security standards."
        ),
    ),
}

# Forgiving aliases → canonical key. Anything not here falls through to a substring
# scan, then to None (unknown category → pure LLM synthesis / generic).
_ALIASES: dict[str, str] = {
    "health": "healthcare",
    "healthcare": "healthcare",
    "medical": "healthcare",
    "medicine": "healthcare",
    "health care": "healthcare",
    "pharma": "healthcare",
    "pharmaceutical": "healthcare",
    "biotech": "healthcare",
    "finance": "finance",
    "financial": "finance",
    "financial services": "finance",
    "personal finance": "finance",
    "fintech": "finance",
    "banking": "finance",
    "insurance": "finance",
    "wealth management": "finance",
    "ecommerce": "ecommerce_saas",
    "e-commerce": "ecommerce_saas",
    "e commerce": "ecommerce_saas",
    "ecommerce saas": "ecommerce_saas",
    "e-commerce saas": "ecommerce_saas",
    "saas": "ecommerce_saas",
    "software": "ecommerce_saas",
    "retail": "ecommerce_saas",
    "legal": "legal",
    "law": "legal",
    "legal services": "legal",
    "law firm": "legal",
    "attorney": "legal",
    "cybersecurity": "cybersecurity",
    "cyber security": "cybersecurity",
    "cyber": "cybersecurity",
    "security": "cybersecurity",
    "infosec": "cybersecurity",
}


def normalize_category(category: str | None) -> str:
    return (category or "").strip().lower()


def resolve_ceiling(category: str | None) -> TaxonomicCeiling | None:
    """Best-effort curated ceiling for a category, or ``None`` when unknown.

    Tries exact-alias, then a token-aware scan: a single-word alias must match a whole
    word ("fintech" in "b2b fintech platform" → finance), a multi-word alias matches as a
    phrase. Word-boundary matching avoids false positives like "health" inside
    "telehealth" or "law" inside "lawnmower". ``None`` is not a failure — it just means
    there's no curated seed and the LLM should synthesize the ceiling from the name alone."""
    key = normalize_category(category)
    if not key:
        return None
    if key in _ALIASES:
        return _CEILINGS[_ALIASES[key]]
    words = set(re.findall(r"[a-z0-9]+", key))
    # Longest alias first so a specific phrase wins over a generic word.
    for alias in sorted(_ALIASES, key=len, reverse=True):
        multiword = " " in alias or "-" in alias
        if (alias in key) if multiword else (alias in words):
            return _CEILINGS[_ALIASES[alias]]
    return None


def ceiling_standards(category: str | None) -> list[str]:
    """The curated standards for a category (empty when unknown). Used to seed a
    deterministic framework's ``required_entities`` so the ceiling holds with no LLM."""
    ceiling = resolve_ceiling(category)
    return list(ceiling.standards) if ceiling else []


def ceiling_prompt_clause(category: str | None) -> str:
    """An instruction fused into the bootstrap synthesis prompt so the LLM synthesizes
    an industry-appropriate Taxonomic Ceiling for ``category``.

    Empty string when no category is given (keeps the generic prompt unchanged). For a
    *known* category it anchors on the curated standards; for an *unknown* one it asks
    the model to synthesize the right standards from scratch — so the ceiling is dynamic
    for any vertical, not limited to the curated list."""
    key = normalize_category(category)
    if not key:
        return ""
    ceiling = resolve_ceiling(category)
    if ceiling is not None:
        seed = ", ".join(ceiling.standards)
        return (
            f"\nIndustry category: {ceiling.label}. Taxonomic ceiling — {ceiling.summary}\n"
            f"Treat these as the REQUIRED regulatory/standards floor and include them among "
            f"required_entities, then add any others specific to this site: {seed}.\n"
            f"Authoritative sources to anchor citations: {', '.join(ceiling.authorities)}.\n"
        )
    return (
        f"\nIndustry category: {category}. Synthesize an industry-appropriate TAXONOMIC "
        f"CEILING for this category: the specific regulations, compliance standards, and "
        f"governing bodies a credible site in this vertical must address (e.g. HIPAA for "
        f"healthcare, SEC/CFPB for finance). Include those standards among required_entities "
        f"and prefer their governing bodies as authoritative citation sources.\n"
    )


def merge_ceiling_entities(category: str | None, entities: list[str], *, limit: int) -> list[str]:
    """Union curated ceiling standards into an entity list (ceiling first, order-stable,
    deduped, capped at ``limit``). Guarantees the regulatory ceiling is present even if an
    LLM omitted it; a no-op when the category is unknown."""
    seed = ceiling_standards(category)
    if not seed:
        return entities[:limit]
    seen: set[str] = set()
    out: list[str] = []
    for item in [*seed, *entities]:
        t = str(item).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= limit:
            break
    return out
