"""
Adversarial auditor — a model-isolated skeptic over the recommendations.

A *second*, independent check on top of the (non-circular) Independent Validator,
ported from the parallel ``aeo-pipeline`` build. Two layers, both designed to never
break a run and to degrade to the deterministic layer when no LLM is available:

  * **Deterministic citation-hallucination checks (always).** Any URL the
    recommendations cite is checked for structural validity, and — when
    ``verify_reachability`` is on — a HEAD request confirms it actually resolves.
    A non-resolvable or malformed cited URL is flagged ``hallucinated``.

  * **Model-isolated adversarial verdict (optional).** When an LLM is enabled, a
    distinct *adversarial persona* ("you did NOT write this; find why it's wrong")
    judges the proposal — isolation is in the prompt, not the vendor, so it breaks
    the recommender's self-grading loop without a second dependency. Wrapped in a
    bounded retry; if the model never answers, the audit degrades to the
    deterministic citation layer rather than failing the page.

Mirrors the codebase's deterministic-first contract: ``adversarial_audit`` returns
a verdict and never raises, and with the LLM off it reduces to "no hallucinated
citations". Synchronous on purpose (the analysis loop runs in worker threads, like
the scorers / Perplexity probe).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

import httpx

from ..logging import get_logger
from ..nlp.llm import LLMClient

log = get_logger(__name__)

# http/https URLs; stops at whitespace, quotes, angle brackets, closing parens/brackets.
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

_ADVERSARIAL_SYSTEM = (
    "You are an INDEPENDENT adversarial auditor. You did NOT write the proposal below "
    "and you are NOT grading your own work. Find real reasons it is wrong, unsupported, "
    "hallucinated, or not implementable. Be skeptical; only pass it if you cannot find a "
    'genuine defect. Reply with JSON only: {"passed": true|false, "failure_reason": "..."|null}.'
)


@dataclass(slots=True)
class CitationSignal:
    """One cited URL's behavioral check result."""

    url: str
    structurally_valid: bool
    reachable: bool | None  # None when reachability was not verified
    hallucinated: bool


@dataclass(slots=True)
class AdversarialVerdict:
    """Outcome of an adversarial audit over a proposal's text."""

    passed: bool                       # authoritative gate: no hallucinated cite AND model didn't refute
    available: bool                    # did the LLM adversarial verdict actually run?
    llm_passed: bool | None            # the model's verdict (None when not run)
    attempts: int
    citation_signals: list[CitationSignal] = field(default_factory=list)
    failure_reason: str | None = None

    @property
    def hallucinated_count(self) -> int:
        return sum(1 for s in self.citation_signals if s.hallucinated)

    def to_detail(self) -> dict:
        return {
            "passed": self.passed,
            "available": self.available,
            "llm_passed": self.llm_passed,
            "attempts": self.attempts,
            "hallucinated": self.hallucinated_count,
            "failure_reason": self.failure_reason,
            "citation_signals": [asdict(s) for s in self.citation_signals],
        }


def extract_citation_urls(text: str) -> list[str]:
    """Pull candidate cited URLs out of proposal text, de-duped, order-stable."""
    seen: dict[str, None] = {}
    for m in _URL_RE.findall(text or ""):
        seen.setdefault(m.rstrip(".,;:"), None)
    return list(seen)


def _is_structurally_valid(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def _default_head_check(url: str) -> bool:
    """Real reachability probe: a short HEAD via the force-IPv4 transport seam.
    Any failure (timeout/DNS/4xx-5xx) counts as unreachable, never raises."""
    from ..crawl.transport import sync_transport

    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, transport=sync_transport()) as client:
            resp = client.head(url)
            return resp.status_code < 400
    except Exception as exc:  # unreachable is a signal, not a crash
        log.warning("citation_head_failed", url=url, error=str(exc))
        return False


def check_citation_signals(
    urls: list[str],
    *,
    verify_reachability: bool = False,
    head_check=_default_head_check,
) -> list[CitationSignal]:
    """Structural validity for every URL (always), plus optional HEAD reachability.
    A URL is ``hallucinated`` when it is structurally invalid, or (when verified)
    structurally valid but unreachable."""
    signals: list[CitationSignal] = []
    for url in urls:
        structurally_valid = _is_structurally_valid(url)
        reachable: bool | None = None
        if verify_reachability and structurally_valid:
            reachable = head_check(url)
        hallucinated = (not structurally_valid) or (reachable is False)
        signals.append(
            CitationSignal(
                url=url,
                structurally_valid=structurally_valid,
                reachable=reachable,
                hallucinated=hallucinated,
            )
        )
    return signals


def _llm_verdict(text: str, llm: LLMClient) -> tuple[bool, str | None] | None:
    """One adversarial LLM verdict. Returns ``(passed, reason)`` or ``None`` on any
    failure (the facade already returns None rather than raising)."""
    data = llm.generate_json(
        f"Proposal under audit:\n{text}\n\nAudit it adversarially and return the JSON verdict.",
        _ADVERSARIAL_SYSTEM,
    )
    if not isinstance(data, dict):
        return None
    reason = data.get("failure_reason")
    return bool(data.get("passed", False)), (str(reason) if reason else None)


def adversarial_audit(
    text: str,
    *,
    llm: LLMClient | None = None,
    verify_reachability: bool = False,
    max_attempts: int = 3,
) -> AdversarialVerdict:
    """Audit a proposal's text. Deterministic citation-hallucination checks always
    run; the model-isolated adversarial verdict runs only when ``llm`` is enabled and
    is retried up to ``max_attempts``. Never raises.

    A proposal **passes** when no cited URL is hallucinated AND the model did not
    refute it (a model that never answered does not fail the page — the deterministic
    citation layer is the floor)."""
    signals = check_citation_signals(
        extract_citation_urls(text), verify_reachability=verify_reachability
    )
    hallucinated = [s for s in signals if s.hallucinated]

    llm_passed: bool | None = None
    verdict_reason: str | None = None
    available = False
    attempts = 0
    if llm is not None and llm.enabled:
        for attempt in range(1, max(1, max_attempts) + 1):
            attempts = attempt
            verdict = _llm_verdict(text, llm)
            if verdict is not None:
                llm_passed, verdict_reason = verdict
                available = True
                break

    passed = (llm_passed is not False) and not hallucinated

    failure_reason: str | None = None
    if not passed:
        parts: list[str] = []
        if hallucinated:
            parts.append(f"{len(hallucinated)} hallucinated citation(s)")
        if llm_passed is False:
            parts.append(verdict_reason or "adversarial auditor refuted the recommendation")
        failure_reason = "; ".join(parts) or None

    log.info(
        "adversarial_audit_done",
        passed=passed,
        available=available,
        citations=len(signals),
        hallucinated=len(hallucinated),
        attempts=attempts,
    )
    return AdversarialVerdict(
        passed=passed,
        available=available,
        llm_passed=llm_passed,
        attempts=attempts,
        citation_signals=signals,
        failure_reason=failure_reason,
    )
