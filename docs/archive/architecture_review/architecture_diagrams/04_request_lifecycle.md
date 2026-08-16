# Request Lifecycle — weekly `audit-cycle` (recommended architecture)

```mermaid
sequenceDiagram
    autonumber
    participant Cron as systemd timer
    participant Orch as Async Orchestrator
    participant Ref as Reference Generator
    participant Crawl as Crawler (Crawl4AI)
    participant Proc as Processor (extract+score)
    participant Anl as Analysis (gap+recommend+validate)
    participant DB as PostgreSQL
    participant Ext as External (Gemini/Perplexity/PSI)

    Cron->>Orch: audit_cycle(domain)
    Orch->>Crawl: discover + prioritize (top-N)
    Crawl-->>Orch: ranked URL inventory
    Orch->>Ref: generate_and_pin_blueprint (L1+L2+L3)
    Ref->>Ext: Gemini synthesis (optional)
    Ref->>DB: save_versioned + pin to run
    Orch->>Proc: site-level Coverage Diff (ideal vs discovered)
    Proc->>DB: persist missing/thin findings
    loop per top-N page
        Orch->>Crawl: fetch page
        Crawl-->>Orch: HTML (+ content hash)
        alt unchanged (hash gate)
            Orch->>DB: carry-forward prior extraction+score
        else changed / new
            Orch->>Proc: 12 extractors -> 10 scorers (8 deterministic)
            Proc->>Ext: PageSpeed (batched) + optional LLM depth
            Proc->>DB: persist extraction + score
        end
    end
    loop per scored client page (thread pool)
        Orch->>Anl: Dual-Layer Gap (60/40)
        Anl->>Anl: recommend (schema/entity/content)
        Anl->>Anl: re-score + retry <=3
        Anl->>Ext: Independent validator + Perplexity citation
        Anl->>Ext: adversarial auditor (opt-in)
        Anl->>DB: per-page report (Error Sink isolates failures)
    end
    Orch->>DB: site-level report (pinned to blueprint version)
    Orch-->>Cron: run summary (succeeded/partial/failed)
    Note over Orch,DB: Every step writes an agent_traces row + OTLP span;<br/>aeo trace PAGE_ID reconstructs the full journey.
```
