# syntax=docker/dockerfile:1
#
# AEO crawler image. Bundles the headless Chromium that Crawl4AI/Playwright
# drives, so the same image serves `aeo run`, `aeo worker`, and `aeo migrate`.
#
#   docker build -t aeo-crawler .
#   docker run --rm --env-file .env aeo-crawler status
#
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # config/ is not packaged into the wheel — point the app at the copied tree.
    AEO__CONFIG_DIR=/app/config \
    AEO__LOG_FORMAT=json \
    # Shared, world-readable browser path so the non-root user finds Chromium.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# ca-certificates for TLS (httpx, PSI). Playwright's own OS libs are pulled in
# by `playwright install --with-deps` below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer: copy only what the wheel build needs so edits to config/
# or tests don't bust the pip cache.
COPY pyproject.toml README.md ./
COPY src ./src
# Install with the [api] extra so the same image serves `aeo serve` (FastAPI/uvicorn)
# alongside `aeo run` / `aeo worker` / `aeo migrate`.
RUN pip install ".[api]" \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

# Runtime config (rubric thresholds, extractor regex packs).
COPY config ./config

# Single-token PaaS boot command (`start-api` = migrate + serve): some hosts'
# start-command parsing breaks quoted `sh -c "…"` strings (Render dockerCommand).
COPY --chmod=755 scripts/start-api.sh /usr/local/bin/start-api

# Drop privileges.
RUN useradd --create-home --uid 10001 aeo && chown -R aeo:aeo /app
USER aeo

# Default to the help screen; compose / `docker run … <cmd>` override this.
ENTRYPOINT ["aeo"]
CMD ["--help"]
