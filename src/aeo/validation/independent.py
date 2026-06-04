"""
Independent Validator — the v4 fix for *circular validation*.

v3's validator re-scored the recommendation against the **same** rubric the
recommender optimized: same signals, same blind spots. This validator checks
signals the recommender does *not* directly optimize, so passing it is real
evidence rather than the recommender grading its own homework:

  * **Deterministic, independent checks** (always run, no network):
      - ``tldr_under_50_words`` — there is a liftable lead answer (≤ ~50 words);
      - ``h1_is_question`` — the H1 itself parses as the user's question (the
        rubric scores H2/H3 question ratio + H1 *defects*, never "H1 is a question");
      - ``valid_jsonld_present`` — ≥1 JSON-LD block and zero invalid blocks
        (stricter than the schema_markup tier, which rewards type *coverage*).

  * **Real-world signal** (optional): a Perplexity citation test — does the page's
    domain actually get cited for its target question? Recorded as a signal that
    feeds the validated-wins loop; never a hard gate, since a brand-new page won't
    be cited yet and shouldn't be failed for it.

Pure over an extraction bundle (+ an injected Perplexity client). The overall
``passed`` is the conjunction of the deterministic checks — concrete, controllable,
and independent of the rubric.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..storage.models import ExtractionBundle
from ..utils.text import word_count

DEFAULT_TLDR_MAX_WORDS = 50


@dataclass(slots=True)
class IndependentCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class CitationVerdict:
    available: bool          # did the Perplexity test actually run?
    cited: bool
    question: str | None = None
    citations: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IndependentVerdict:
    checks: list[IndependentCheck]
    deterministic_passed: bool
    citation: CitationVerdict | None = None
    question: str | None = None

    @property
    def passed(self) -> bool:
        """Authoritative gate: all deterministic, non-circular checks pass.
        The citation result is a signal, not a hard requirement (new pages aren't
        cited yet)."""
        return self.deterministic_passed

    def to_detail(self) -> dict:
        return {
            "passed": self.passed,
            "deterministic_passed": self.deterministic_passed,
            "question": self.question,
            "checks": [asdict(c) for c in self.checks],
            "citation": asdict(self.citation) if self.citation else None,
        }


def _lead_answer_words(bundle: ExtractionBundle) -> int:
    """Word count of the page's opening passage (first chunk), 0 if none."""
    chunker = bundle.get("chunker", {}) or {}
    chunks = chunker.get("chunks", []) or []
    if not chunks:
        return 0
    return word_count(str(chunks[0].get("text", "")))


def _h1_text(bundle: ExtractionBundle) -> str | None:
    headings = bundle.get("headings", {}) or {}
    h1 = headings.get("h1_text")
    return str(h1).strip() if h1 else None


def derive_question(bundle: ExtractionBundle) -> str | None:
    """The page's target question for the citation test: a question-shaped H1,
    else the title, else None."""
    h1 = _h1_text(bundle)
    if h1 and h1.rstrip().endswith("?"):
        return h1
    meta = bundle.get("meta", {}) or {}
    title = str(meta.get("title", "")).strip()
    return title or h1 or None


def _check_tldr(bundle: ExtractionBundle, max_words: int) -> IndependentCheck:
    lead = _lead_answer_words(bundle)
    ok = 0 < lead <= max_words
    return IndependentCheck(
        name="tldr_under_50_words",
        passed=ok,
        detail=f"lead answer is {lead} word(s) (target 1-{max_words})",
    )


def _check_h1_question(bundle: ExtractionBundle) -> IndependentCheck:
    h1 = _h1_text(bundle)
    ok = h1 is not None and h1.rstrip().endswith("?")
    return IndependentCheck(
        name="h1_is_question",
        passed=ok,
        detail=f"H1={h1!r}" if h1 else "no H1",
    )


def _check_valid_jsonld(bundle: ExtractionBundle) -> IndependentCheck:
    schema = bundle.get("schema_jsonld", {}) or {}
    blocks = int(schema.get("block_count", 0) or 0)
    invalid = int(schema.get("invalid_blocks", 0) or 0)
    ok = blocks >= 1 and invalid == 0
    return IndependentCheck(
        name="valid_jsonld_present",
        passed=ok,
        detail=f"{blocks} block(s), {invalid} invalid",
    )


def validate_independent(
    bundle: ExtractionBundle,
    *,
    url: str,
    question: str | None = None,
    perplexity=None,
    tldr_max_words: int = DEFAULT_TLDR_MAX_WORDS,
) -> IndependentVerdict:
    """Run the deterministic independent checks (and, when a Perplexity client is
    supplied and enabled, the citation test) over ``bundle``."""
    checks = [
        _check_tldr(bundle, tldr_max_words),
        _check_h1_question(bundle),
        _check_valid_jsonld(bundle),
    ]
    deterministic_passed = all(c.passed for c in checks)

    target_question = question or derive_question(bundle)
    citation: CitationVerdict | None = None
    if perplexity is not None and getattr(perplexity, "enabled", False) and target_question:
        probe = perplexity.cited(target_question, target_url=url)
        if probe is not None:
            citation = CitationVerdict(
                available=True,
                cited=probe.cited,
                question=probe.question,
                citations=probe.citations,
                matched=probe.matched,
            )
        else:
            citation = CitationVerdict(available=False, cited=False, question=target_question)

    return IndependentVerdict(
        checks=checks,
        deterministic_passed=deterministic_passed,
        citation=citation,
        question=target_question,
    )
