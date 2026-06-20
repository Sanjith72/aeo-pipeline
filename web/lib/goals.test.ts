// Unit tests for goalsFromProfile (the signal → GOAL_OPTIONS mapping). Runs on Node's
// built-in test runner with native TS type-stripping — no extra dependency:
//
//   node --test lib/goals.test.ts        (or: npm test, from web/)
//
// Imports use explicit .ts extensions so Node resolves them directly; the Next tsconfig
// excludes *.test.ts so those extensions don't trip `tsc --noEmit`.

import test from "node:test";
import assert from "node:assert/strict";

import {
  GOAL_AI_ANSWERS,
  GOAL_AUTHORITY,
  GOAL_BEAT_COMPETITORS,
  GOAL_SELL_ONLINE,
  GOAL_WIN_CUSTOMERS,
  goalsFromProfile,
} from "./goals.ts";
import { GOAL_OPTIONS } from "./options.ts";
import type { SiteProfile } from "./types.ts";

// A minimal SiteProfile fixture; overrides patch in the signal under test.
function profileFixture(over: {
  structure_score?: number;
  page_count?: number;
  present?: string[];
  missing?: string[];
  stages?: { covered: boolean }[];
  gaps?: string[];
  model?: string;
}): SiteProfile {
  return {
    domain: "example.com",
    scenario: "small_site",
    deliverable: "Gap Analysis & Build Plan",
    headline: "",
    narrative: "",
    agency_mode: false,
    classification: {
      site_class: "small_site",
      page_count: over.page_count ?? 8,
      structure_score: over.structure_score ?? 0.8,
      type_distribution: {},
      present_archetypes: over.present ?? ["home", "about", "services", "contact"],
      missing_archetypes: over.missing ?? [],
    },
    business_intent: {
      model: over.model ?? "informational",
      confidence: 0.8,
      decided_by: "test",
      evidence: [],
      scores: {},
    },
    journey: {
      stages: (over.stages ?? [{ covered: true }, { covered: true }]).map((s, i) => ({
        stage: `stage_${i}`,
        present_count: s.covered ? 1 : 0,
        covered: s.covered,
        examples: [],
      })),
      gaps: over.gaps ?? [],
      filling_nodes: {},
    },
    actions: [],
  };
}

test("low structured-data foundation suggests the AI-answers goal", () => {
  const goals = goalsFromProfile({ profile: profileFixture({ structure_score: 0.3 }) });
  assert.ok(goals.includes(GOAL_AI_ANSWERS));
});

test("weak answer-readiness (uncovered journey stages) suggests AI-answers", () => {
  const goals = goalsFromProfile({
    profile: profileFixture({
      structure_score: 0.9, // foundation is fine; the journey is the signal
      stages: [{ covered: false }, { covered: false }, { covered: true }],
    }),
  });
  assert.ok(goals.includes(GOAL_AI_ANSWERS));
});

test("thin content suggests the brand-authority goal", () => {
  const goals = goalsFromProfile({ profile: profileFixture({ page_count: 2 }) });
  assert.ok(goals.includes(GOAL_AUTHORITY));
});

test("thin ideal-page coverage suggests AI-answers via coveragePct", () => {
  const goals = goalsFromProfile({
    profile: profileFixture({ structure_score: 0.9 }),
    coveragePct: 35,
  });
  assert.ok(goals.includes(GOAL_AI_ANSWERS));
});

test("e-commerce model/industry suggests sell-online; competitors found suggests beat-competitors", () => {
  const goals = goalsFromProfile({
    profile: profileFixture({ model: "ecommerce" }),
    industry: "E-commerce & Retail",
    competitorCount: 3,
  });
  assert.ok(goals.includes(GOAL_SELL_ONLINE));
  assert.ok(goals.includes(GOAL_BEAT_COMPETITORS));
});

test("a decision-stage gap suggests win-more-customers", () => {
  const goals = goalsFromProfile({ profile: profileFixture({ gaps: ["decision"] }) });
  assert.ok(goals.includes(GOAL_WIN_CUSTOMERS));
});

test("a strong, well-rounded profile yields no forced goal", () => {
  // Sound foundation, every stage covered, all archetypes present, no gaps, no competitors.
  const goals = goalsFromProfile({
    profile: profileFixture({
      structure_score: 0.95,
      page_count: 12,
      present: ["home", "about", "services", "contact", "faq", "pricing"],
      missing: [],
      stages: [{ covered: true }, { covered: true }, { covered: true }],
      gaps: [],
      model: "informational",
    }),
    coveragePct: 90,
  });
  assert.deepEqual(goals, []);
});

test("no profile and no signals → empty (never guesses)", () => {
  assert.deepEqual(goalsFromProfile({ profile: null }), []);
});

test("every mapped label still exists in GOAL_OPTIONS (drift guard)", () => {
  const valid = new Set(GOAL_OPTIONS.map((g) => g.label));
  const everySuggestable = goalsFromProfile({
    profile: profileFixture({
      structure_score: 0.1,
      page_count: 1,
      present: [],
      missing: ["home", "about"],
      stages: [{ covered: false }],
      gaps: ["decision"],
      model: "ecommerce local service",
    }),
    industry: "E-commerce & Retail",
    competitorCount: 2,
  });
  assert.ok(everySuggestable.length >= 5);
  for (const g of everySuggestable) assert.ok(valid.has(g), `"${g}" missing from GOAL_OPTIONS`);
});
