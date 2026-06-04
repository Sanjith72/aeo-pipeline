"""
Pipeline stages — small, single-responsibility steps the orchestrator wires
together. Keeping each stage isolated makes them trivially unit-testable and
lets the worker reuse the exact same logic as the inline CLI path.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..extract import DEFAULT_EXTRACTORS
from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..scoring import score_page
from ..settings import get_settings
from ..storage.models import ExtractionBundle, FetchedPage, PageScore, StoredPage
from ..storage.repos import extractions as extractions_repo
from ..storage.repos import pages as pages_repo
from ..storage.repos import scores as scores_repo
from ..utils.html import parse

log = get_logger(__name__)


class ExtractStage:
    """Run every registered extractor over a page's HTML.

    Each extractor gets a FRESH soup on purpose: ``utils.html.body_text`` is
    destructive (it decomposes script/style/nav/footer), so a shared tree
    would silently break later extractors that need those nodes —
    schema_jsonld and glossary read <script type=ld+json>, links reads <a>.
    """

    def __init__(self, extractors: Iterable[tuple[str, Any]] = DEFAULT_EXTRACTORS) -> None:
        self._extractors = list(extractors)

    def run(self, page: FetchedPage, page_id: int, pagespeed: dict[str, Any] | None = None) -> ExtractionBundle:
        bundle = ExtractionBundle(page_id=page_id)
        for name, fn in self._extractors:
            soup = parse(page.html)
            try:
                bundle.put(name, fn(page.html, soup, page.url))
            except Exception as exc:  # one bad extractor shouldn't kill the page
                log.warning("extractor_failed", extractor=name, url=page.url, error=str(exc))
                bundle.put(name, {"error": str(exc)})
        # PageSpeed is async/external, fetched outside the extractor loop.
        if pagespeed is not None:
            bundle.put("pagespeed", pagespeed)
        return bundle


class ScoreStage:
    """Score a page. Honors the v4 Parallel Processor toggle
    (``AEO__SCORING__PARALLEL``): the criterion scorers run concurrently, with
    output identical to the sequential path."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm
        scoring = get_settings().scoring
        self._parallel = scoring.parallel
        self._max_workers = scoring.max_workers

    def run(self, bundle: ExtractionBundle, run_id: int) -> PageScore:
        return score_page(
            bundle, run_id, llm=self._llm,
            parallel=self._parallel, max_workers=self._max_workers,
        )


class PersistStage:
    """A single, mockable persistence seam over the repos."""

    def page(self, page: FetchedPage, run_id: int, client_id: int | None, competitor_id: int | None) -> StoredPage:
        return pages_repo.upsert(page, run_id, client_id, competitor_id)

    def extraction(self, bundle: ExtractionBundle) -> int:
        return extractions_repo.put(bundle)

    def score(self, page_score: PageScore, scored_by: str) -> int:
        return scores_repo.put(page_score, scored_by)

    def copy_unchanged(self, url_normalized: str, page_id: int, run_id: int) -> bool:
        """Clone the prior extraction + score forward. Both must succeed for
        the short-circuit to count as a clean copy."""
        copied_extraction = extractions_repo.copy_latest_for_url(url_normalized, page_id)
        copied_score = scores_repo.copy_latest_for_url(url_normalized, page_id, run_id)
        return copied_extraction and copied_score
