"""
Synthetic-page simulation — apply a proposed edit to an extraction bundle.

The scorers never re-parse HTML; they read the extraction bundle. So "apply the
edit to a synthetic page and re-score" means: mutate a *copy* of the bundle so
its extracted signals reflect what the recommendation would achieve, then run
the same 10-criterion rubric over it.

Two rules keep this faithful rather than wishful:

  * **Bounded to target.** An applier raises a criterion's binding signal only as
    far as the Reference-Layer *target* tier (read from the same ``config`` the
    scorer reads), never to a perfect 5. A page whose only deficiency is
    competitor pressure (already at/above target) therefore does not "improve"
    in simulation -- which is correct, and is what drives the retry/flag path.

  * **Concrete edits only.** A recommendation is simulated only when it carries a
    concrete artifact: deterministic schema JSON-LD, or an LLM ``edits`` list.
    A bare advisory ("add an FAQ section") has nothing to apply, so it is a
    no-op -- the Validation loop then routes the page to Human Review instead of
    claiming an improvement it cannot substantiate.

Pure and deterministic: no DB, no network. ``apply_recommendation`` mutates the
bundle in place (callers pass a deep copy) and returns whether it changed a
signal -- useful for tracing, though the improve/not-improve decision is made by
re-scoring, not by this return value.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..recommender.models import SCHEMA, Recommendation
from ..scoring.rubric import Rubric
from ..storage.models import ExtractionBundle

# Q&A pair counts and word counts are banded inside the scorers themselves
# (qa_blocks.score / content_depth.score), not in config -- mirror the minimum
# value that reaches each tier. Keep in sync if those bands ever change.
_QA_PAIRS_FOR_TIER: dict[int, int] = {2: 1, 3: 2, 4: 3, 5: 5}
_DEPTH_WORDS_FOR_TIER: dict[int, int] = {2: 200, 3: 400, 4: 800, 5: 1500}


def apply_recommendation(
    bundle: ExtractionBundle,
    rec: Recommendation,
    *,
    rubric: Rubric,
    reference: Any,
) -> bool:
    """Apply one recommendation to ``bundle`` in place. Returns True if a signal
    changed. Advisory-only recs (no concrete edit) and criteria without an
    applier are deliberate no-ops.

    When the recommendation carries ready-to-publish ``draft`` copy for a criterion
    we *measure* its actual signals (via the real extractors) and substitute those,
    so the re-score reflects what would ship rather than an applier optimistically
    assuming the target tier was reached. The optimistic edits appliers remain the
    fallback for advisory/scaffold recs that have no draft."""
    if rec.rec_type == SCHEMA:
        return _apply_schema(bundle, rec)

    criterion = rec.criterion

    # Measured-draft path: re-score on the content that would actually ship.
    if rec.payload.get("draft") and criterion in _DRAFT_FIELDS:
        return _apply_draft(bundle, criterion, str(rec.payload["draft"]))

    # Only concrete edits can be simulated; a static advisory has nothing to apply.
    if "edits" not in rec.payload:
        return False

    applier = _APPLIERS.get(criterion) if criterion else None
    if applier is None:
        return False
    target = int(reference.target_for(criterion))
    return applier(bundle, target, rubric)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _section(bundle: ExtractionBundle, name: str) -> dict[str, Any]:
    """Return the named extraction sub-dict, creating an empty one if absent."""
    sec = bundle.data.get(name)
    if not isinstance(sec, dict):
        sec = {}
        bundle.data[name] = sec
    return sec


def _tiers(rubric: Rubric, name: str, key: str = "tiers") -> dict[int, Any]:
    """Tier map for a criterion as the scorer reads it, with int keys."""
    crit = rubric.criteria.get(name)
    raw = (crit.cfg.get(key, {}) if crit else {}) or {}
    return {int(k): v for k, v in raw.items()}


def _as_float(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _as_int(value: Any, default: int = 0) -> int:
    return int(value) if isinstance(value, (int, float)) else default


# ---------------------------------------------------------------------------
# per-criterion appliers (criterion -> bring its binding signal to `target`)
# ---------------------------------------------------------------------------


def _apply_schema(bundle: ExtractionBundle, rec: Recommendation) -> bool:
    """Add the recommended JSON-LD type to the page's structured data."""
    stype = rec.payload.get("schema_type")
    if not stype:
        return False
    sec = _section(bundle, "schema_jsonld")
    types = list(sec.get("types", []) or [])
    changed = stype not in types
    if changed:
        types.append(stype)
    sec["types"] = types
    # The added block is well-formed; reflect it in the count without inflating
    # invalid_blocks (a malformed-schema penalty the page did not incur).
    sec["block_count"] = max(_as_int(sec.get("block_count")), len(types))
    return changed


def _apply_qa(bundle: ExtractionBundle, target: int, _rubric: Rubric) -> bool:
    sec = _section(bundle, "qa_blocks")
    cur = _as_int(sec.get("pair_count"))
    desired = _QA_PAIRS_FOR_TIER.get(target, cur)
    if desired > cur:
        sec["pair_count"] = desired
        return True
    return False


def _apply_stats(bundle: ExtractionBundle, target: int, rubric: Rubric) -> bool:
    tiers = _tiers(rubric, "stats_in_html") or {1: 0, 2: 1, 3: 3, 4: 6, 5: 10}
    desired = _as_int(tiers.get(target))
    sec = _section(bundle, "stats")
    if desired > _as_int(sec.get("count")):
        sec["count"] = desired
        return True
    return False


def _apply_entity(bundle: ExtractionBundle, target: int, rubric: Rubric) -> bool:
    tiers = _tiers(rubric, "entity_consistency") or {1: 0.0, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.5}
    desired = _as_float(tiers.get(target))
    sec = _section(bundle, "entities")
    cur_ratio = _as_float(sec.get("ratio"))
    first_person = _as_int(sec.get("first_person_count"))
    entity_count = _as_int(sec.get("entity_count"))
    new_ratio = max(cur_ratio, desired)
    # Replacing first-person phrasing with the brand raises the entity count;
    # keep it consistent with the new ratio so the evidence is coherent.
    new_entity = max(entity_count, 1, math.ceil(new_ratio * first_person))
    changed = new_ratio > cur_ratio or new_entity > entity_count
    sec["ratio"] = new_ratio
    sec["entity_count"] = new_entity
    sec["first_person_count"] = first_person
    return changed


def _apply_heading(bundle: ExtractionBundle, target: int, rubric: Rubric) -> bool:
    tiers = _tiers(rubric, "heading_structure") or {1: 0.0, 2: 0.10, 3: 0.25, 4: 0.45, 5: 0.65}
    desired = _as_float(tiers.get(target))
    sec = _section(bundle, "headings")
    changed = False
    if desired > _as_float(sec.get("h23_question_ratio")):
        sec["h23_question_ratio"] = desired
        changed = True
    # The rewrite also fixes the H1 defects the heading scorer penalizes.
    if sec.get("missing_h1"):
        sec["missing_h1"] = False
        changed = True
    if sec.get("template_h1"):
        sec["template_h1"] = False
        changed = True
    return changed


def _apply_depth(bundle: ExtractionBundle, target: int, _rubric: Rubric) -> bool:
    desired = _DEPTH_WORDS_FOR_TIER.get(target)
    if desired is None:
        return False
    sec = _section(bundle, "readability")
    if desired > _as_int(sec.get("word_count")):
        sec["word_count"] = desired
        return True
    return False


def _apply_readability(bundle: ExtractionBundle, target: int, rubric: Rubric) -> bool:
    crit = rubric.criteria.get("answer_readability")
    cfg = crit.cfg if crit else {}
    flesch_tiers = _tiers(rubric, "answer_readability", key="flesch_tiers") or {
        1: 0, 2: 20, 3: 30, 4: 45, 5: 55
    }
    max_sent = _as_float(cfg.get("max_avg_sentence_len"), 28.0)
    min_words = _as_int(cfg.get("min_word_count"), 50)
    desired = _as_float(flesch_tiers.get(target))

    read = _section(bundle, "readability")
    chunk = _section(bundle, "chunker")
    changed = False

    if desired > _as_float(read.get("flesch_reading_ease"), -1.0):
        read["flesch_reading_ease"] = desired
        changed = True
    if _as_int(read.get("word_count")) < min_words:
        read["word_count"] = min_words
        changed = True
    avg = read.get("avg_sentence_length")
    if isinstance(avg, (int, float)) and float(avg) > max_sent:
        read["avg_sentence_length"] = max_sent
        changed = True
    # Segmenting the page removes the monolithic-content penalty (chunks <= 1).
    if _as_int(chunk.get("chunk_count")) <= 1:
        chunk["chunk_count"] = 2
        changed = True
    return changed


_APPLIERS: dict[str, Callable[[ExtractionBundle, int, Rubric], bool]] = {
    "qa_blocks": _apply_qa,
    "stats_in_html": _apply_stats,
    "entity_consistency": _apply_entity,
    "heading_structure": _apply_heading,
    "content_depth": _apply_depth,
    "answer_readability": _apply_readability,
}


# ---------------------------------------------------------------------------
# measured-draft path (re-score on the copy that would actually ship)
# ---------------------------------------------------------------------------

# Per criterion: which extracted section(s) the draft overlays, and for each
# scoring-relevant field the direction in which a *better* value moves (+1 higher
# is better, -1 lower is better). The draft's measured value replaces the page's
# only when it scores strictly better, so a thin draft cannot manufacture an
# improvement -- which is exactly what drives the retry / could-not-improve path.
_DRAFT_FIELDS: dict[str, dict[str, dict[str, int]]] = {
    "qa_blocks": {"qa_blocks": {"pair_count": +1, "question_heading_count": +1}},
    "stats_in_html": {"stats": {"count": +1}},
    "content_depth": {"readability": {"word_count": +1}},
    "heading_structure": {"headings": {"h23_question_ratio": +1}},
    "answer_readability": {
        "readability": {
            "flesch_reading_ease": +1,
            "word_count": +1,
            "avg_sentence_length": -1,
        },
        "chunker": {"chunk_count": +1},
    },
}


def _apply_draft(bundle: ExtractionBundle, criterion: str, draft: str) -> bool:
    """Overlay the criterion's scored signals with values *measured* from the draft
    (HTML/Markdown run through the real extractors), taking the draft value only when
    it scores better than the page's current one. Returns True if a signal changed."""
    # Local import: the extractor suite (bs4 + textstat + config) must not be a
    # module-load dependency of the otherwise pure, network-free simulator.
    from .draft_check import signals_from_draft

    spec = _DRAFT_FIELDS.get(criterion)
    if not spec:
        return False
    measured = signals_from_draft(draft)
    changed = False
    for section_name, fields in spec.items():
        draft_sec = measured.get(section_name) or {}
        sec = _section(bundle, section_name)
        for field, direction in fields.items():
            dv = draft_sec.get(field)
            if not isinstance(dv, (int, float)):
                continue
            cur = sec.get(field)
            if not isinstance(cur, (int, float)) or (direction > 0 and dv > cur) or (direction < 0 and dv < cur):
                sec[field] = dv
                changed = True

    # The heading rewrite can only *clear* the H1 defects the scorer penalizes; it
    # never (re)introduces them, so a draft with a real, non-template H1 fixes them.
    if criterion == "heading_structure":
        draft_h = measured.get("headings") or {}
        sec = _section(bundle, "headings")
        for defect in ("missing_h1", "template_h1"):
            if sec.get(defect) and not draft_h.get(defect, True):
                sec[defect] = False
                changed = True
    return changed
