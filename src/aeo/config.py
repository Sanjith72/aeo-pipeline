from __future__ import annotations
import json
from functools import lru_cache
from typing import Annotated
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = Field(..., description="PostgreSQL DSN")

    # Ollama -- all LLM calls: blueprint generation, coverage diff, recommender, processor
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "phi3"
    # Timeout for Ollama inference calls. phi3 on CPU can take 90-120s per request;
    # set higher if you see ReadTimeout in coverage_diff or recommender logs.
    ollama_timeout: float = 180.0

    # Crawler
    crawler_max_pages: int = 200
    crawler_max_concurrent: int = 10
    crawler_timeout_seconds: int = 30
    crawler_user_agent: str = "AEO-Pipeline/1.0"

    # Pipeline
    # NoDecode disables pydantic-settings' source-level JSON decoding so an
    # empty or non-JSON env value reaches the validator below instead of
    # crashing during settings construction.
    pipeline_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    freshness_days: int = 7

    @field_validator("pipeline_domains", mode="before")
    @classmethod
    def _parse_pipeline_domains(cls, value: object) -> object:
        # Already a list (e.g. default_factory or programmatic init) -- keep as-is.
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return []
        # Support JSON list form: PIPELINE_DOMAINS=["securin.io", "example.com"]
        if value.startswith("["):
            return json.loads(value)
        # Fall back to comma-separated form: PIPELINE_DOMAINS=a.com,b.com
        return [item.strip() for item in value.split(",") if item.strip()]

    # Observability
    otel_endpoint: str = ""
    otel_service_name: str = "aeo-pipeline"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
