"""
structlog setup. Console-pretty in dev, JSON in prod.

Usage:
    from aeo.logging import get_logger
    log = get_logger(__name__)
    log.info("crawl_start", url=url, batch=batch_id)
"""

from __future__ import annotations

import logging
import sys

import structlog

from .settings import get_settings

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return

    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)

    shared: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if s.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Tame noisy libraries
    for noisy in ("urllib3", "asyncio", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _configured:
        configure()
    return structlog.get_logger(name)
