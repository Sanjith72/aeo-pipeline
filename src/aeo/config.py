from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = Field(..., description="PostgreSQL DSN")

    # Google Gemini — reference blueprint generation only
    gemini_api_key: str = Field(..., description="Google Gemini API key (free tier)")
    gemini_model: str = "gemini-1.5-flash"

    # Ollama — all secondary reasoning (coverage diff, recommender, processor)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "phi3"

    # Crawler
    crawler_max_pages: int = 200
    crawler_max_concurrent: int = 10
    crawler_timeout_seconds: int = 30
    crawler_user_agent: str = "AEO-Pipeline/1.0"

    # Pipeline
    pipeline_domains: list[str] = Field(default_factory=list)
    freshness_days: int = 7

    # Observability
    otel_endpoint: str = ""
    otel_service_name: str = "aeo-pipeline"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
