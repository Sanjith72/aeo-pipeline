"""
Shared test fixtures.

Scoring/extraction tests run fully offline — no database, no browser, no
Ollama. A bundle is built by running the real extractors over fixture HTML
(mirroring the pipeline's ``ExtractStage``: a FRESH soup per extractor because
``utils.html.body_text`` is destructive), then scored with the LLM disabled so
every result is deterministic and fast.
"""

from __future__ import annotations

import os

# Disable the LLM for the whole session BEFORE settings are first read, so no
# scorer reaches for Ollama (which would otherwise hang on its 120s timeout).
os.environ["AEO__LLM__ENABLED"] = "false"

# Same trick, same timing, for the DEPLOYMENT-only credentials. pydantic-settings reads the
# repo's .env file, so a developer who has real values there gets a different test suite
# from CI, which has no .env at all: `require_api_key` 401s every TestClient request that
# carries no X-API-Key header, and the JWT gate rejects the fabricated users these tests
# inject. That is how ~20 tests across test_tickets.py and test_payments.py could fail on a
# working machine while main stayed green — a suite whose result depends on an untracked
# file is reporting on the file, not the code.
#
# Blank rather than delete: an empty value reads as "unset" everywhere (ApiCfg treats it as
# falsy, AuthCfg has an explicit blank-is-unset validator) and, unlike `del`, it also
# overrides whatever the .env would otherwise supply. Tests that need a key set it themselves.
os.environ["AEO__API__AUTH_KEY"] = ""
os.environ["AEO__API__ADMIN_KEY"] = ""
os.environ["AEO__AUTH__JWT_SECRET"] = ""
os.environ["AEO__AUTH__JWKS_URL"] = ""
os.environ["AEO__AUTH__JWT_ISSUER"] = ""

from pathlib import Path

import pytest

from aeo.extract import DEFAULT_EXTRACTORS
from aeo.nlp.llm import LLMClient, get_client
from aeo.scoring.result import ScoreContext
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg, get_settings, load_yaml_file
from aeo.storage.models import ExtractionBundle
from aeo.utils.html import parse

# Caches may have been primed during collection; rebuild against the env above.
get_settings.cache_clear()
get_client.cache_clear()
load_yaml_file.cache_clear()

FIXTURES = Path(__file__).parent / "fixtures"


def build_bundle(
    html: str,
    url: str,
    *,
    page_id: int = 1,
    pagespeed: dict | None = None,
) -> ExtractionBundle:
    """Run every registered extractor over ``html`` the way ExtractStage does."""
    bundle = ExtractionBundle(page_id=page_id)
    for name, fn in DEFAULT_EXTRACTORS:
        soup = parse(html)  # fresh soup — body_text() decomposes nodes
        bundle.put(name, fn(html, soup, url))
    if pagespeed is not None:
        bundle.put("pagespeed", pagespeed)
    return bundle


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def rubric():
    return load_rubric()


@pytest.fixture(scope="session")
def disabled_llm() -> LLMClient:
    """Explicitly disabled client — ``generate`` short-circuits to None."""
    return LLMClient(LLMCfg(enabled=False))


@pytest.fixture
def ctx_factory(rubric, disabled_llm):
    """Factory: HTML + URL → a ScoreContext with the LLM disabled."""

    def _make(html: str, url: str, *, pagespeed: dict | None = None) -> ScoreContext:
        bundle = build_bundle(html, url, pagespeed=pagespeed)
        return ScoreContext(bundle=bundle, rubric=rubric, llm=disabled_llm)

    return _make


@pytest.fixture
def strong_ctx(ctx_factory):
    return ctx_factory(
        load_fixture("strong_page.html"),
        "https://securin.io/blog/vulnerability-management-guide",
    )


@pytest.fixture
def weak_ctx(ctx_factory):
    return ctx_factory(load_fixture("weak_page.html"), "https://securin.io/resources")


@pytest.fixture
def glossary_ctx(ctx_factory):
    return ctx_factory(load_fixture("glossary_page.html"), "https://securin.io/glossary")
