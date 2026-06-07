# Deployment Architecture — recommended (OCI ARM, single always-on VM → scale-out)

```mermaid
flowchart LR
    subgraph OCI[OCI Ampere ARM · US region · force-IPv4]
        subgraph VM[Always-on VM]
            TIMER[systemd timer<br/>weekly audit-cycle]
            APP[aeo CLI / Orchestrator container]
            W1[Worker 1]
            W2[Worker N · horizontal<br/>FOR UPDATE SKIP LOCKED]
            OLL[Ollama / cloud LLM<br/>optional]
        end
        PG[(PostgreSQL 16<br/>store + job queue + traces)]
    end

    subgraph EXT[External APIs · read-only · injectable seams]
        GEM[Gemini · L3 synthesis]
        PPX[Perplexity · citation test]
        PSI[PageSpeed Insights]
    end

    COL[OTLP collector / Grafana Tempo<br/>OpenTelemetry export]

    TIMER --> APP
    APP --> PG
    W1 --> PG
    W2 --> PG
    APP --> OLL
    APP -. optional .-> GEM
    APP -. optional .-> PPX
    APP -. optional .-> PSI
    APP -- spans --> COL
    W1 -- spans --> COL

    CI[GitHub Actions<br/>ruff + mypy + 342 tests] -->|image| GHCR[(GHCR)]
    GHCR --> APP
```

## Scaling path

| Stage | Topology | Trigger |
|---|---|---|
| **Now** | 1 VM, 1 worker, Postgres on same box | single client / weekly cadence |
| **Scale-out** | N stateless workers claim from the Postgres queue (no broker) | more domains / faster cadence |
| **Managed DB** | Postgres → managed instance (read replicas for reporting) | DB becomes SPOF / reporting load |
| **Async DB** | psycopg2 → asyncpg (per B's proven pattern) | event-loop blocking dominates profile |
| **Multi-region** | per-region workers, central blueprint store | latency / data-residency needs |
