"""
Per-domain onboarding config — ``config/domains/{domain}.yaml``.

A lightweight onboarding seam (ported idea): instead of carrying one global topic +
engine_target in settings, each client domain gets a small YAML file that overrides
those per run. Onboarding a new client is a file, not a code or env change.

  config/domains/securin.io.yaml
    domain: securin.io
    topic: PEV
    engine_target: perplexity      # perplexity | chatgpt_search | gemini | generic
    max_urls: 120                  # optional discovery cap
    label: "securin weekly"        # optional run label

Resolution order downstream (highest wins): explicit CLI arg → domain config →
settings.reference_architecture → framework default. Unknown ``engine_target`` values
fall back to the settings/generic value rather than failing — same forgiving contract
as the rest of the config layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)

_VALID_ENGINES = {"perplexity", "chatgpt_search", "gemini", "generic"}


def normalize_domain(domain: str) -> str:
    """Bare host key: drop scheme, ``www.``, path, and surrounding slashes.

    ``https://www.Securin.io/blog`` → ``securin.io``; ``securin.io/`` → ``securin.io``."""
    d = (domain or "").strip().lower()
    d = urlsplit(d).netloc if "://" in d else d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip("/")


@dataclass(slots=True)
class DomainConfig:
    domain: str
    topic: str | None = None
    engine_target: str | None = None
    max_urls: int | None = None
    label: str | None = None
    notes: str = ""


def _domains_dir(config_dir: Path | None = None) -> Path:
    base = config_dir or Path(get_settings().config_dir)
    return base / "domains"


def load_domain_config(domain: str, *, config_dir: Path | None = None) -> DomainConfig | None:
    """Load ``config/domains/{normalized_domain}.yaml`` if present, else ``None``.

    Not cached: it is read once per run (off the hot path), and leaving it
    un-cached keeps it test-friendly (temp configs reload)."""
    key = normalize_domain(domain)
    if not key:
        return None
    path = _domains_dir(config_dir) / f"{key}.yaml"
    if not path.exists():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # a malformed onboarding file must never abort a run
        log.warning("domain_config_unreadable", domain=key, error=str(exc))
        return None
    if not isinstance(raw, dict):
        return None

    engine = str(raw.get("engine_target", "")).strip().lower() or None
    if engine is not None and engine not in _VALID_ENGINES:
        log.warning("domain_config_bad_engine", domain=key, engine_target=engine)
        engine = None  # fall back to settings/generic

    max_urls_raw = raw.get("max_urls")
    try:
        max_urls = int(max_urls_raw) if max_urls_raw is not None else None
    except (TypeError, ValueError):
        max_urls = None

    return DomainConfig(
        domain=str(raw.get("domain", key)),
        topic=(str(raw["topic"]).strip() or None) if raw.get("topic") else None,
        engine_target=engine,
        max_urls=max_urls,
        label=(str(raw["label"]) if raw.get("label") else None),
        notes=str(raw.get("notes", "")),
    )
