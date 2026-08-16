# Documentation map

Active references live at this level; specs and history live in the subfolders.

## Active references (this folder)

| Doc | Contents |
|-----|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Backend design rationale, module map, data flow, extensibility. |
| [PIPELINE_EXPLAINED.md](PIPELINE_EXPLAINED.md) | Walkthrough of the end-to-end crawl → score → recommend pipeline. |
| [V5_CONTRACTS.md](V5_CONTRACTS.md) | The locked JSON/DB contracts for the v5 build (skills, packs, entitlements). |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Local, Docker, and cloud deploy; CI/CD; monitoring; scaling; secrets; backups. |
| [DEPLOY_RAILWAY_VERCEL.md](DEPLOY_RAILWAY_VERCEL.md) | Railway + Vercel hosting specifics. |
| [VALIDATION.md](VALIDATION.md) | Rubric → implementation mapping, test coverage, benchmark. |
| [MIGRATION_V3_V4.md](MIGRATION_V3_V4.md) | v3 → v4 migration report and production-readiness review. |
| [AGENT_LAYER_REPORT.md](AGENT_LAYER_REPORT.md) | The agent runtime (planner, builder, critic, ReAct loop). |
| [STRATEGY_ENGINE_DESIGN.md](STRATEGY_ENGINE_DESIGN.md) | Strategy/action-plan generation design. |
| [TASK_PRIORITIZATION_ARCHITECTURE.md](TASK_PRIORITIZATION_ARCHITECTURE.md) | How tickets/tasks get ranked. |
| [CRAWL_OPTIMIZATION_PLAN.md](CRAWL_OPTIMIZATION_PLAN.md) | Crawl budget and incremental-crawl design. |
| [LLM_FIRST_PRODUCT_STRATEGY.md](LLM_FIRST_PRODUCT_STRATEGY.md) | Where the LLM does and doesn't drive the product. |
| [UX_AUDIT.md](UX_AUDIT.md) | UI/UX audit notes. |

## Subfolders

| Folder | Contents |
|--------|----------|
| [product/](product/) | Product specs: [AEO_PRODUCT_CHANGES_v5.md](product/AEO_PRODUCT_CHANGES_v5.md) (the authoritative v5 build spec), [PRODUCT_FLOW.md](product/PRODUCT_FLOW.md) (API contract §3 the web UI is built against), [UPDATED_ARCHITECTURE.md](product/UPDATED_ARCHITECTURE.md), [USER_SCENARIOS.md](product/USER_SCENARIOS.md). |
| [architecture/](architecture/) | Frozen architecture specs: [v3](architecture/aeo_architecture_v3.md) and [v4](architecture/aeo_architecture_v4.md) (Reference Architecture Generator, Coverage Diff, Independent Validator). |
| [archive/](archive/) | Historical plans, checklists, and release notes that shipped: R2/R3 redesign docs, SP-1→SP-4 implementation plan/report, v4.1 beta notes, the gamified-task dev prompt, and the A/B architecture review. Kept for provenance; not maintained. |
| [superpowers/](superpowers/) | Dated design specs and build plans, one per feature push. |
| [prompts/](prompts/) | Reusable working prompts. |
