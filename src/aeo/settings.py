"""
Application settings.

Three-layer config, merged in this order (later wins):
  1. Defaults in code
  2. config/*.yaml files
  3. Environment variables (AEO__SECTION__KEY=value)

Anything secret (DB URL, API keys) belongs in env vars only.
"""

from __future__ import annotations

import os
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# YAML-backed sections
# ---------------------------------------------------------------------------


class RateLimitCfg(BaseModel):
    requests_per_minute: int = 30
    burst: int = 5


class RetryCfg(BaseModel):
    max_attempts: int = 4
    initial_backoff_sec: float = 1.5
    max_backoff_sec: float = 30.0
    retry_on_status: list[int] = Field(default_factory=lambda: [408, 425, 429, 500, 502, 503, 504])


class FingerprintCfg(BaseModel):
    enabled: bool = True
    algorithm: str = "sha256"


class BrowserCfg(BaseModel):
    headless: bool = True
    remove_overlay_elements: bool = True
    word_count_threshold: int = 0


class DiscoveryCfg(BaseModel):
    # Site Discovery harvests a domain's URL inventory (sitemap, then recursive)
    # with plain HTTP GETs — no JS render needed to read links. max_urls caps the
    # inventory *before* prioritization (which then cuts to prioritization.top_n).
    max_urls: int = 200
    max_depth: int = 2          # recursive fallback BFS depth from the homepage
    max_sitemaps: int = 50      # cap sitemap-index expansion (avoid pathological sites)
    timeout_sec: int = 15


class CrawlerCfg(BaseModel):
    user_agent: str = "AEOBot/0.2"
    concurrency: int = 4
    request_timeout_sec: int = 30
    respect_robots: bool = True
    # Force IPv4 on outbound HTTP. On OCI Ampere (ARM) the default dual-stack
    # resolver silently stalls scraper fetches on AAAA records; binding the
    # client to an IPv4 local address (0.0.0.0) forces AF_INET. Off by default
    # (dev/most clouds are fine); flip on in the OCI deployment.
    force_ipv4: bool = False
    rate_limit: RateLimitCfg = RateLimitCfg()
    retry: RetryCfg = RetryCfg()
    fingerprint: FingerprintCfg = FingerprintCfg()
    browser: BrowserCfg = BrowserCfg()
    discovery: DiscoveryCfg = DiscoveryCfg()


class LLMCfg(BaseModel):
    enabled: bool = True          # off → scorers fall back to deterministic-only
    provider: str = "ollama"      # "ollama" (local) | "cloud" (OpenAI-compatible)
    # Ollama (local) backend
    host: str = "http://localhost:11434"
    model: str = "phi3"
    # Cloud backend: any OpenAI-compatible /chat/completions endpoint (OpenAI,
    # Gemini's compat endpoint, Together, …). Key via AEO__LLM__CLOUD_API_KEY.
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_model: str = "gpt-4o-mini"
    cloud_api_key: str | None = None
    # Shared generation params
    timeout_sec: int = 120
    temperature: float = 0.1
    num_predict: int = 600


class DatabaseCfg(BaseModel):
    url: str = "postgresql://aeo:aeo@localhost:5432/aeo"
    pool_min: int = 2
    pool_max: int = 10


class ValidationCfg(BaseModel):
    # Validation loop: apply the proposed edits to a synthetic page, re-score,
    # and retry the Recommender if the simulated score did not improve. Capped so
    # a stubborn page can never spin forever — after this many attempts it is
    # flagged 'could-not-improve' and routed to Human Review.
    max_attempts: int = 3
    # v4 Independent Validator: after the edit-efficacy gate, run independent,
    # non-circular signals (deterministic checks + Perplexity citation test) to
    # decide review routing. Off → the v3 (circular) re-score gate alone decides.
    independent_enabled: bool = True
    # Fan-out for the per-page analysis loop (gap→recommend→validate→report).
    # Each page is independent and Error-Sink isolated, so they run in a thread
    # pool. 1 = sequential (the v3 behavior). Real win when an LLM is enabled.
    analysis_concurrency: int = 1
    # v4+ Adversarial auditor (ported idea): an independent, model-isolated skeptic
    # that tries to REFUTE each recommendation, plus deterministic citation-
    # hallucination checks on any URLs it cites. Off by default; when on but the LLM
    # is disabled it degrades to the deterministic citation checks alone (never
    # fails a page for a missing model — the deterministic-first contract).
    adversarial_enabled: bool = False
    adversarial_max_attempts: int = 3
    # Verify cited URLs are actually reachable (a HEAD request per citation). Off by
    # default since it makes outbound network calls; structural validity is always checked.
    verify_citations: bool = False


class PerplexityCfg(BaseModel):
    # The v4 Independent Validator's real-world signal: query the target question
    # on Perplexity and compare the rewrite's shape to what's actually cited.
    # Disabled by default — with no key the validator falls back to its
    # deterministic independent checks (never fails a page for a missing key).
    enabled: bool = False
    api_key: str | None = None
    base_url: str = "https://api.perplexity.ai"
    model: str = "sonar"
    timeout_sec: int = 60


class ScoringCfg(BaseModel):
    # v4 Parallel Processor: run the criterion scorers concurrently in a thread
    # pool. Output is identical to sequential (scorers are pure over a shared
    # read-only context); the win is on the I/O-bound LLM-refined criteria.
    parallel: bool = False
    max_workers: int = 8


class ObsCfg(BaseModel):
    # Observability. The custom ``agent_traces`` table + ``aeo trace`` are always on
    # (queryable per-page journey). This adds OPTIONAL OpenTelemetry OTLP export
    # ALONGSIDE it (ported idea) — standards-aligned distributed tracing for an OTLP
    # collector (Tempo/Jaeger/Honeycomb). Off by default; a no-op when the SDK isn't
    # installed or no endpoint is set, so it never adds a hard dependency.
    otel_enabled: bool = False
    otel_endpoint: str = ""  # OTLP gRPC endpoint, e.g. http://localhost:4317
    otel_service_name: str = "aeo"


class ApiCfg(BaseModel):
    # HTTP API (SP-4) auth. When AEO__API__AUTH_KEY is set, every /api/* route except
    # /api/health requires a matching X-API-Key header. Unset (default) = open mode for
    # local dev; set it in any deployment that exposes the API.
    auth_key: str | None = None


class ReferenceArchitectureCfg(BaseModel):
    # v4 Reference Architecture Generator: the versioned, per-topic ideal-site
    # blueprint. Generated on a slow cadence and pinned per run so the measuring
    # stick doesn't move week to week.
    enabled: bool = True
    topic: str = "PEV"  # default topic (Proactive Exposure / Vulnerability mgmt) for Securin
    framework_version: str = "1"
    min_pages_per_cluster: int = 10  # topical-authority target; thin-cluster threshold
    regenerate_cadence_days: int = 30  # regenerate only this often (else reuse pinned version)
    # Per-engine emphasis for LLM blueprint synthesis (ported idea): perplexity →
    # citation density; chatgpt_search → conversational coverage; gemini → entity
    # structure; generic → engine-neutral. Routes the synthesis PROMPT only — the
    # deterministic floor and the closed-vocab guardrail are unchanged. Unknown
    # values fall back to 'generic'.
    engine_target: str = "generic"
    # The generator uses the configured LLM (set llm.provider=cloud pointed at
    # Gemini's OpenAI-compatible endpoint) for synthesis; falls back to the
    # deterministic builder when the LLM is disabled or fails.
    # v4+ Content drafting: turn missing-page coverage gaps into ready-to-publish
    # drafts (H1 + headers + body prose + JSON-LD) attached to the site report. The
    # top `draft_limit` missing pages (priority order) are drafted — LLM-authored
    # prose when enabled, a deterministic scaffold otherwise. 0 → draft none.
    draft_missing_pages: bool = True
    draft_limit: int = 10


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="AEO__",
        extra="ignore",
    )

    crawler: CrawlerCfg = CrawlerCfg()
    llm: LLMCfg = LLMCfg()
    database: DatabaseCfg = DatabaseCfg()
    validation: ValidationCfg = ValidationCfg()
    perplexity: PerplexityCfg = PerplexityCfg()
    scoring: ScoringCfg = ScoringCfg()
    reference_architecture: ReferenceArchitectureCfg = ReferenceArchitectureCfg()
    obs: ObsCfg = ObsCfg()
    api: ApiCfg = ApiCfg()

    log_level: str = "INFO"
    log_format: str = "console"
    config_dir: str = str(DEFAULT_CONFIG_DIR)
    psi_api_key: str | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Load .env into the process environment BEFORE constructing Settings or
    # reading os.getenv below. pydantic-settings' own env_file parsing only
    # populates AEO__-prefixed model fields; the unprefixed DATABASE_URL /
    # DB_POOL_* contract (read via os.getenv) needs the values in os.environ.
    # override=False keeps real OS env vars winning over .env (prod-safe).
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    s = Settings()
    cfg_dir = Path(s.config_dir)

    # Merge YAML on top of defaults — env vars override YAML below
    crawler_yaml = _load_yaml(cfg_dir / "crawler.yaml")
    if crawler_yaml:
        s.crawler = CrawlerCfg(**{**s.crawler.model_dump(), **crawler_yaml})

    # Re-apply env overrides on the now-merged crawler section
    env_overrides = {k: v for k, v in os.environ.items() if k.startswith("AEO__CRAWLER__")}
    for env_key, env_val in env_overrides.items():
        path = env_key.removeprefix("AEO__CRAWLER__").lower().split("__")
        _set_nested(s.crawler, path, env_val)

    # Database URL & pool from a single DATABASE_URL — keeps the legacy contract
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        s.database = DatabaseCfg(
            url=db_url,
            pool_min=int(os.getenv("DB_POOL_MIN", str(s.database.pool_min))),
            pool_max=int(os.getenv("DB_POOL_MAX", str(s.database.pool_max))),
        )

    return s


def _set_nested(obj: BaseModel, path: list[str], value: str) -> None:
    """Walk a dotted attribute path and set the final value, coercing primitives."""
    cur: Any = obj
    for key in path[:-1]:
        cur = getattr(cur, key)
    final_key = path[-1]
    existing = getattr(cur, final_key, None)
    coerced: Any
    if isinstance(existing, bool):
        coerced = value.lower() in ("1", "true", "yes", "on")
    elif isinstance(existing, int):
        coerced = int(value)
    elif isinstance(existing, float):
        coerced = float(value)
    else:
        coerced = value
    setattr(cur, final_key, coerced)


@cache
def load_yaml_file(name: str) -> dict[str, Any]:
    """Public helper for non-settings YAML (scoring.yaml, entities.yaml, etc.).

    Cached: config files are static for a process lifetime, and extractors call
    this on the hot path (once or twice per page). Treat the result as read-only.
    """
    s = get_settings()
    return _load_yaml(Path(s.config_dir) / name)
