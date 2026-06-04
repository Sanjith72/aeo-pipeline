# Phase 5 — Deployment Guide

How to run the AEO crawler locally, in Docker, and in the cloud — plus CI/CD,
monitoring, scaling, scheduling, secrets, backups, and cost.

---

## 1. Topology

Three long-lived components and a stateless batch worker:

```
                 enqueue                 ┌──────────────┐
   CLI / cron ───────────────────────►   │  PostgreSQL  │  jobs, pages,
        │                                │  (queue +    │  extractions,
        │ run (one-shot)                 │   results)   │  rubric_scores_v2
        ▼                                └──────▲───────┘
  ┌───────────────┐   claim job   ───────────── │
  │  aeo worker   │ ◄──────────────────────────┘
  │  (1..N)       │
  │               │  fetch (headless Chromium via Crawl4AI/Playwright)
  │               │ ───────────────────────────────►  target websites
  │               │  score (deterministic; LLM optional)
  └──────┬────────┘
         │ optional
         ▼
   ┌──────────┐        ┌─────────────────────────┐
   │  Ollama  │        │ PageSpeed Insights API  │  (criterion 8, optional)
   │ (LLM)    │        └─────────────────────────┘
   └──────────┘
```

- **PostgreSQL** is the only required dependency — it is the queue *and* the
  result store.
- **Workers** are stateless and horizontally scalable; each runs its own
  headless Chromium. Crash-safe: a job stays claimable if a worker dies.
- **Ollama** and **PageSpeed Insights** are both optional. Disabled → scoring
  degrades gracefully (deterministic-only; `load_speed` scores a neutral 3).

Two execution modes share one codebase:
| Mode | Command | Use when |
|------|---------|----------|
| One-shot | `aeo run URLS… -t Securin` | Ad-hoc audits, small batches, cron. |
| Queue + worker | `aeo enqueue …` + `aeo worker` | Continuous / large-scale crawling. |

---

## 2. Prerequisites

- **Python ≥ 3.11** (3.12 recommended; CI and the image use 3.12).
- **PostgreSQL ≥ 14.**
- **Playwright Chromium** + OS libs (`playwright install --with-deps chromium`)
  — pulled in automatically by the Docker image.
- *(optional)* **Ollama** for LLM-assisted `content_depth` / `stats` scoring.
- *(optional)* A **PageSpeed Insights API key** for `load_speed`.

---

## 3. Local development

```bash
# 1. Virtualenv + install (editable, with dev tools)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium

# 2. Configure
cp .env.example .env                 # edit DATABASE_URL at minimum

# 3. Database
createdb aeo                         # or: docker compose up -d db
aeo migrate                          # applies src/aeo/storage/migrations/*.sql

# 4. Smoke test (offline, no DB/LLM needed)
pytest -q

# 5. Run the pipeline
aeo run https://securin.io/blog/some-post -t Securin
aeo status                           # DB health + queue depth
```

`pyproject.toml` sets `pythonpath = ["src"]`, so the test suite imports `aeo`
without an editable install; the `pip install -e` above is for the `aeo` CLI.

---

## 4. Configuration & secrets

**Layering** (later wins): code defaults → `config/*.yaml` → environment
variables. The rubric (`config/scoring.yaml`) and extractor packs
(`config/extractors.yaml`) are config, not code — tune them without redeploying.

**Env var naming** — every `Settings` field is read with the `AEO__` prefix and
`__` as the nesting delimiter. The *only* unprefixed vars are `DATABASE_URL`,
`DB_POOL_MIN`, `DB_POOL_MAX` (read directly). See `.env.example` for the full
set. Common ones:

| Variable | Purpose | Default |
|----------|---------|---------|
| `DATABASE_URL` | Postgres DSN (`postgresql://…`) | local default |
| `AEO__CONFIG_DIR` | Path to `config/` — **required for installed wheel / container** | `<repo>/config` |
| `AEO__LLM__ENABLED` | Turn the LLM on/off | `true` (set `false` for deterministic) |
| `AEO__LLM__HOST` / `__MODEL` | Ollama endpoint / model | `localhost:11434` / `phi3` |
| `AEO__CRAWLER__CONCURRENCY` | Parallel fetches per worker | `4` |
| `AEO__PSI_API_KEY` | PageSpeed Insights key | unset → neutral score |
| `AEO__LOG_LEVEL` / `AEO__LOG_FORMAT` | `INFO` / `json`\|`console` | `INFO` / `console` |

> **Why `AEO__CONFIG_DIR` matters in containers:** `config/` lives at the repo
> root, not under `src/aeo`, so it is **not** bundled into the wheel. The image
> copies it to `/app/config` and sets `AEO__CONFIG_DIR=/app/config`. Forget this
> and the app falls back to a path that doesn't exist in the container.

**Secrets hygiene**
- `.env` is git-ignored — never commit it. Commit only `.env.example`.
- In the cloud, inject secrets via the platform's secret store (see §6), not
  baked into the image.
- Rotate any key that has ever been shared in plaintext (chat, logs, tickets).

---

## 5. Docker (single host)

Artifacts in the repo root: `Dockerfile`, `docker-compose.yml`, `.dockerignore`.

```bash
cp .env.example .env                 # set POSTGRES_PASSWORD
docker compose up -d db              # Postgres with a healthcheck
docker compose run --rm migrate      # apply schema, then exit
docker compose up -d worker          # start draining the queue

# Enqueue / inspect (the 'app' service is on-demand)
docker compose run --rm app enqueue https://example.com/page -t Securin
docker compose run --rm app status

# Opt into the local LLM
docker compose --profile llm up -d ollama
docker compose --profile llm exec ollama ollama pull phi3
#   …then set AEO__LLM__ENABLED=true in .env and recreate the worker.
```

Scale workers on one host: `docker compose up -d --scale worker=4`. Each worker
runs its own Chromium — budget **~300–500 MB RAM per worker**.

---

## 6. Cloud deployment

The unit of deployment is the single container image (`Dockerfile`), driven by
`DATABASE_URL` + env vars. Pick the platform by how much ops you want to own:

| Platform | Best fit | DB | Container surface | Ops effort | Rough $/mo (small) |
|----------|----------|----|--------------------|-----------|--------------------|
| **Railway** | Fastest path; PoC → prod | Managed Postgres add-on | Deploy from repo/image; cron built-in | Lowest | $10–30 |
| **Render** | Same, with Background Workers | Managed Postgres | "Background Worker" + "Cron Job" types | Low | $15–40 |
| **DigitalOcean** | Predictable pricing | Managed PG or droplet | App Platform *or* a $12 droplet + compose | Low–med | $12–50 |
| **GCP Cloud Run + Cloud SQL** | Scale-to-zero bursts | Cloud SQL Postgres | Run jobs/services; Cloud Scheduler | Medium | $20–60 |
| **AWS ECS Fargate + RDS** | Existing AWS estate | RDS Postgres | Fargate service + EventBridge Scheduler | Higher | $40–90 |
| **Azure Container Apps + Flexible PG** | Existing Azure estate | Flexible Server | Container Apps + jobs | Medium | $30–70 |
| **Oracle Cloud (OCI) VM** | Zero-cost self-host, full control | Postgres in the compose stack | SSH + `docker compose` on an Always-Free Ampere box | Medium (you own the box) | **$0** (Always Free) |

**Recommended path:** start on **Railway or Render** — managed Postgres, secrets
UI, and built-in cron cover everything here with near-zero ops. Graduate to
**Cloud Run + Cloud SQL** (scale-to-zero) or **ECS Fargate + RDS** only when an
existing cloud footprint or compliance posture demands it.

**Mapping the components**
- *Worker* → a long-running "background worker" / Fargate service / Cloud Run
  service (min-instances ≥ 1).
- *Migrate* → a one-shot job in the release/pre-deploy step (`aeo migrate`).
- *Enqueue* → a scheduled job (see §10) or an app endpoint you add later.
- *Postgres* → always the platform's **managed** offering (backups, failover).
- *Ollama* → skip in most clouds (needs a big always-on box / GPU). Keep the LLM
  disabled, or host Ollama on a dedicated VM and point `AEO__LLM__HOST` at it.

### 6.1 Walkthrough — Oracle Cloud (OCI) Always-Free VM

A single Always-Free **Ampere A1** instance (ARM64, up to 4 OCPU / 24 GB RAM,
free indefinitely) runs the entire `docker-compose.yml` stack — Postgres +
worker — at **$0**. The crawler only makes *outbound* connections, so you need
**no inbound ports** beyond SSH; Postgres stays private on the compose network.

**1. Provision the VM** (OCI Console → Compute → Instances → Create):
- Shape: **Ampere A1 (VM.Standard.A1.Flex)**, e.g. 2 OCPU / 12 GB (well within
  Always Free). Image: **Ubuntu 24.04**. Save the SSH keypair.
- Networking: keep the default VCN. Only port 22 needs to be open (it is by
  default). Do **not** expose 5432.

**2. Install Docker** (SSH in as `ubuntu@<public-ip>`):
```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker      # use docker without sudo
sudo systemctl enable --now docker                    # start on boot
```

**3. Get the code onto the box** — either path:
```bash
# a) via Git (preferred once it's a repo on GitHub)
git clone https://github.com/<you>/aeo-crawler.git && cd aeo-crawler

# b) no repo yet — copy from your laptop (run locally, PowerShell/bash):
#    scp -r -i <key> D:\securin_agents\page_crawler ubuntu@<public-ip>:~/aeo-crawler
```

**4. Configure secrets** — create `.env` on the VM (never copy your local one):
```bash
cp .env.example .env
# edit .env: set a strong POSTGRES_PASSWORD, and DATABASE_URL to match, e.g.
#   DATABASE_URL=postgresql://aeo:<password>@db:5432/aeo
# optional: AEO__PSI_API_KEY=<key> for the load_speed criterion
```

**5. Build & launch** (Ampere is ARM64 — the image builds natively, and
Playwright Chromium has arm64 builds, so no emulation):
```bash
docker compose build                  # ~5–10 min first time (installs Chromium)
docker compose up -d db               # start Postgres
docker compose run --rm migrate       # apply schema
docker compose up -d worker           # start the queue drainer
```

**6. Use it:**
```bash
docker compose run --rm app enqueue https://securin.io/blog/some-post -t Securin
docker compose run --rm app status    # queue depth + run report
docker compose logs -f worker         # tail structured logs
```

**7. Keep it running.** `restart: unless-stopped` (already in the compose file)
plus `systemctl enable docker` means the stack survives reboots. For recurring
crawls, add a host cron entry (see §10) that runs the `enqueue` command above.

> Once CI is pushing images to GHCR (§7), you can skip the build on the VM and
> pull instead: set `image: ghcr.io/<you>/aeo-crawler:latest` and
> `docker compose pull` — but a multi-arch image must include `linux/arm64`
> (build with `docker buildx --platform linux/amd64,linux/arm64`).

---

## 7. CI/CD

`.github/workflows/ci.yml` runs on every push/PR:

1. **test** — `pip install -e ".[dev]"`, `ruff check`, `pytest -q`. No services
   needed: the suite is fully offline (LLM disabled in `conftest.py`).
2. **image** (main only) — build the Docker image and push to GHCR tagged with
   both `latest` and the commit SHA, using GitHub Actions layer cache.

Wire continuous deploy by adding a final job that calls your platform's deploy
hook (Railway/Render auto-deploy on image push; Cloud Run/ECS via their deploy
action) and runs `aeo migrate` as a release step **before** routing traffic to
new workers.

---

## 8. Monitoring & logging

- **Logs** are structured (structlog). Set `AEO__LOG_FORMAT=json` in prod and
  ship stdout/stderr to the platform's log aggregator. Key events:
  `crawl_start`, `scorer_failed`, `db_pool_ready`, `migration_applied`.
- **Health** — `aeo status` prints DB reachability and queue depth; wrap it in a
  liveness probe or an uptime check.
- **Alert on:**
  - queue depth climbing monotonically → workers stalled/undersized.
  - rising `scorer_failed` rate → an extractor/scorer regression.
  - DB connection errors / pool exhaustion (`db_health_check_failed`).
- **Metrics (future):** the worker loop is the natural place to emit
  jobs-processed / fetch-latency counters to StatsD/Prometheus.

---

## 9. Scaling

- **Horizontal:** add workers (`--scale worker=N`, or more service replicas).
  Job claiming is atomic in Postgres, so workers never double-process.
- **Per-worker concurrency:** `AEO__CRAWLER__CONCURRENCY` (default 4) parallel
  fetches. Raise for I/O-bound crawling; watch RAM (each headless tab costs
  memory) and target-site politeness (`config/crawler.yaml` rate limits).
- **DB pool:** size `DB_POOL_MAX` ≈ `workers × (concurrency + headroom)`. Keep
  it under the managed instance's `max_connections`.
- **Where the time goes:** the headless fetch (~1–5 s/page) dominates;
  extraction+scoring is ~15–32 ms/page (see [VALIDATION.md](VALIDATION.md) §4),
  so CPU is never the bottleneck — throughput scales with worker count.
- **LLM:** if enabled, Ollama inference becomes the slowest step. Run it on a
  dedicated host and treat it as a shared, rate-limited resource.

---

## 10. Scheduling

Periodic audits = a scheduled `enqueue` (or `run`) of a URL list.

- **Railway/Render:** add a Cron Job service running
  `aeo enqueue -f urls.txt -t Securin` (workers pick the batch up).
- **GCP:** Cloud Scheduler → Cloud Run Job. **AWS:** EventBridge Scheduler →
  Fargate task. **Plain cron:** `0 6 * * 1 docker compose run --rm app enqueue -f /data/urls.txt -t Securin`.
- Prefer **enqueue over run** on a schedule so the long crawl happens on the
  always-on workers, not in the short-lived scheduler task.

### v4 Weekly Audit Loop (single always-on VM, e.g. OCI Ampere)

LangGraph (here: the async `Orchestrator`) runs the graph; it does **not** schedule
itself. On one always-on VM the schedule is a systemd timer or crontab that invokes
the v4 entrypoint weekly — no Cloud Scheduler needed:

```bash
aeo audit-cycle securin.io -t Securin
# discover → blueprint (generate+pin) → coverage diff → crawl (hash-gated,
# unchanged pages carried forward) → analyze → site report
```

Ready-made `ops/` artifacts: `aeo-audit.service` + `aeo-audit.timer` (systemd),
`crontab.example`, and `weekly_audit.sh`. Install:

```bash
sudo cp ops/aeo-audit.service ops/aeo-audit.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aeo-audit.timer
systemctl list-timers aeo-audit.timer        # verify next run
```

The content-hash gate makes a weekly re-audit cheap (unchanged pages skip the
processor and carry their last report forward); the site-level Coverage Diff runs
every week regardless, since missing pages have no hash to compare.

### v4 environment (Reference Architecture + Independent Validator + OCI)

```bash
# Reference Architecture Generator (L3 synthesis via Gemini's OpenAI-compatible API)
AEO__REFERENCE_ARCHITECTURE__ENABLED=true
AEO__REFERENCE_ARCHITECTURE__TOPIC=PEV
AEO__LLM__ENABLED=true
AEO__LLM__PROVIDER=cloud
AEO__LLM__CLOUD_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
AEO__LLM__CLOUD_MODEL=gemini-2.0-flash
AEO__LLM__CLOUD_API_KEY=…          # secret

# Independent Validator real-world signal (optional; deterministic checks always run)
AEO__VALIDATION__INDEPENDENT_ENABLED=true
AEO__PERPLEXITY__ENABLED=true
AEO__PERPLEXITY__API_KEY=…         # secret

# Parallel processor + analysis fan-out
AEO__SCORING__PARALLEL=true
AEO__VALIDATION__ANALYSIS_CONCURRENCY=4

# OCI Ampere: force IPv4 to stop silent dual-stack scraper stalls
AEO__CRAWLER__FORCE_IPV4=true
```

Everything above is optional and degrades gracefully: with no Gemini/Perplexity
key the generator falls back to its deterministic blueprint and the validator to
its deterministic checks. `force_ipv4` covers the httpx clients (discovery,
PageSpeed, Perplexity, LLM); the Crawl4AI Chromium fetch is a separate
browser-launch concern.

---

## 11. Backups

- Use the managed Postgres provider's **automated daily snapshots + PITR** —
  this is the main reason to prefer managed over self-hosted.
- Self-hosted fallback: `pg_dump` on a cron, retained off-box:
  ```bash
  pg_dump "$DATABASE_URL" | gzip > aeo_$(date +%F).sql.gz
  ```
- Schema is reproducible from `src/aeo/storage/migrations/` via `aeo migrate`;
  back up **data**, not structure.
- The crawl queue (`jobs`) is transient — losing it only means re-enqueuing.
  Prioritise `pages`, `extractions`, and `rubric_scores_v2`.

---

## 12. Cost (rough, small workload: a few thousand pages/month)

| Item | Railway / Render | Cloud Run + Cloud SQL | ECS Fargate + RDS |
|------|------------------|-----------------------|-------------------|
| Compute (1 worker) | $7–15 | $10–25 (scale-to-zero helps batch) | $20–40 |
| Managed Postgres | $7–20 | $10–30 | $15–35 |
| Egress / misc | minimal | minimal | minimal |
| **Total** | **~$15–35** | **~$20–55** | **~$35–75** |

LLM is the cost wildcard: a always-on Ollama box (CPU) adds ~$20–40/mo; GPU far
more. Keeping the LLM disabled is the cheapest *and* most deterministic option —
the deterministic scorers already cover all 10 criteria.

---

## 13. Pre-production checklist

- [ ] `.env` populated; secrets in the platform secret store, not the image.
- [ ] `AEO__CONFIG_DIR` set (container) and `config/` present.
- [ ] `AEO__LOG_FORMAT=json`; logs shipped to an aggregator.
- [ ] `aeo migrate` runs as a release step before new workers take traffic.
- [ ] Managed Postgres with automated backups; `DB_POOL_MAX` < `max_connections`.
- [ ] At least one worker with `restart: unless-stopped` (or replicas ≥ 1).
- [ ] Uptime check on `aeo status`; alerts on queue depth & `scorer_failed`.
- [ ] Any previously-disclosed credentials rotated.
