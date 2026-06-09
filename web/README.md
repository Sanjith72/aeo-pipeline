# AEO Studio (web) — SP-4b

The guided consultant UI: a Next.js (App Router) + TypeScript + Tailwind front end over the
AEO HTTP API (SP-4a). It turns a business brief into an AI-search-ready **blueprint**,
**strategy / action plan**, and a downloadable **implementation bundle** — and can also
analyze an existing site.

## Run it

1. Start the backend API (from the repo root):
   ```bash
   pip install -e ".[api]"
   aeo serve            # http://localhost:8000  (interactive docs at /docs)
   ```
2. Start the frontend (from `web/`):
   ```bash
   cp .env.example .env.local      # adjust NEXT_PUBLIC_API_BASE if the API isn't on :8000
   npm install
   npm run dev                     # http://localhost:3000
   ```

## Flow

- **Plan a new site** → `POST /api/plan` → ideal sitemap + `no_website` strategy + action plan,
  then **Generate deliverables** → `POST /api/deliverables` (sitemap.xml, navigation, content
  briefs, per-page specs with JSON-LD, internal-linking + schema plans) with per-file download.
- **Analyze an existing site** → `POST /api/profile` → classification, business model, journey
  gaps, and a prioritized action plan.

The UI is a thin client: all intelligence lives in the `aeo` package behind the API
(`lib/api.ts` + `lib/types.ts` mirror the contract).

## Layout

```
app/        layout.tsx, globals.css, page.tsx (the wizard, a client component)
lib/        api.ts (typed client), types.ts (API payload mirrors)
```
