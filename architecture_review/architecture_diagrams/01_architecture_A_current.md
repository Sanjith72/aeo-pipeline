# Architecture A — Kenneth / local `page_crawler` (deterministic-first)

End-to-end data flow as actually wired in code (`src/aeo/pipeline/orchestrator.py`).

```mermaid
flowchart TD
    subgraph IN[Inputs]
        T[Topic / Category]
        CU[Client domain]
        CO[Competitor URLs]
    end

    subgraph REF[Reference Architecture Generator · versioned · once per run]
        L1[L1 Competitor structural patterns<br/>reference/competitor_patterns.py]
        L2[L2 Framework + criteria definitions<br/>reference/framework.py · config/framework.yaml]
        L3[L3 Gemini synthesis - optional<br/>reference/generator.py]
        BP[(Blueprint vN · ideal sitemap + coverage map<br/>reference/blueprint.py · content-hash versioned)]
        L1 --> L3
        L2 --> L3
        L3 --> BP
    end

    subgraph CRAWL[Crawler Block]
        SD[Site Discovery · sitemap+recursive<br/>crawl/discovery.py]
        PP[Page Prioritization · top-N<br/>crawl/prioritize.py]
        PC[Page Crawler · Crawl4AI/Playwright<br/>crawl/runner.py]
        HG{Content-hash gate<br/>crawl/fingerprint.py}
        SD --> PP --> PC --> HG
    end

    CDIFF[Coverage Diff · SITE level<br/>discovered vs ideal sitemap to missing/thin<br/>processor/coverage_diff.py]
    BP --> CDIFF
    SD --> CDIFF
    CDIFF --> PP

    subgraph PROC[Processor · per page]
        EX[Extract · 12 pure extractors<br/>extract/*]
        SC[Score · 10 scorers · 8 deterministic + 2 hybrid<br/>scoring/scorers/* · optional ThreadPool]
        GAP[Dual-Layer Gap · 60% blueprint+rubric / 40% competitor<br/>processor/gap_analysis.py]
        EX --> SC --> GAP
    end
    HG -->|unchanged| CF[Carry-forward prior extraction+score]
    HG -->|changed/new| EX

    subgraph REC[Recommender]
        SM[Schema · deterministic<br/>recommender/schema.py]
        EO[Entity<br/>recommender/entity.py]
        CR[Content<br/>recommender/content.py]
    end
    GAP --> SM & EO & CR
    CDIFF -.->|missing-page briefs| CR

    subgraph VAL[Validation]
        V1[Edit-efficacy re-score · retry <=3<br/>validation/validator.py]
        V2[Independent Validator · TLDR/H1/JSON-LD + Perplexity<br/>validation/independent.py]
        V3[Adversarial Auditor · opt-in<br/>validation/adversarial.py]
    end
    SM & EO & CR --> V1 --> V2 --> V3
    V3 --> HR[Human Review]
    HR --> RP[Per-page + Site reports<br/>report/site_builder.py]
    RP -.->|cited wins · human-gated| FB[Validated-wins feedback<br/>reference/feedback.py]
    FB -.-> L2

    subgraph UTIL[Utilities · cross-cutting]
        OR[Async Orchestrator<br/>pipeline/orchestrator.py]
        WK[Queue Worker · FOR UPDATE SKIP LOCKED<br/>pipeline/worker.py]
        DB[(PostgreSQL · store + job queue · psycopg2)]
        OBS[Observability · agent_traces + aeo trace<br/>obs/tracing.py]
        ES[Error Sink · per-page isolation<br/>obs/error_sink.py]
        CRON[Weekly audit-cycle · systemd/cron]
    end
```

**Wiring note:** engine-target routing, the independent validator, and the adversarial auditor are all reachable from the live run path (`orchestrator.py:231-232` → `analysis.py:142-160`), each gated by settings and covered by dedicated tests.
