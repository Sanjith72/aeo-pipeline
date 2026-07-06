// web/lib/quest/theme.ts
// Data-driven skins for the Quest Map. Each phase key maps to one complete theme — its
// stage surface, palette, ordered enemy roster (weakest → named final boss) and reward.
// Swapping a phase's theme (e.g. Phase 3) is a config change here; no component edits.
//
// Visual language: every stage is a DARK surface built from the app's paper tokens with a
// faint per-phase tint, so the map reads as part of the Celestial Blueprint shell rather
// than a colored island (the old Phase-1 parchment yellow). Accents come from the app's
// semantic set (emerald / sky / cyan); text is always the ink scale for AA contrast.

export interface QuestTheme {
  key: string;
  name: string; // "Quick Wins — Shoreline Raid"
  /** Tailwind classes for the expanded themed stage background — paper-dark with a subtle
   *  phase tint, so the stage feels native to the app shell. */
  surfaceClass: string;
  inkClass: string; // base text on the stage
  subInkClass: string; // muted text on the stage
  nodeAccent: string; // hex — active node ring / glow
  pathColor: string; // hex — not-yet-cleared trail
  pathDoneColor: string; // hex — cleared trail
  coinColor: string; // hex — coins are white across every theme (one currency, the app accent)
  /** Ordered difficulty scale, weakest first, named final boss last. The map compresses or
   *  repeats this to fit however many tasks a phase actually has; the last task always
   *  renders as `roster[last]`. */
  roster: { glyph: string; name: string }[];
  chestGlyph: string;
  rewardLabel: string; // "Treasure chest" | "Vault" | "Cargo pod"
}

const COIN_WHITE = "#ffffff"; // matches the app accent (white) — coins read as one currency

// Phase 1 — Quick Wins. Emerald: the app's "win/verified" color, on near-paper dark.
const PIRATE: QuestTheme = {
  key: "week_1",
  name: "Pirate Treasure Map",
  surfaceClass: "bg-gradient-to-b from-[#0f1a15] via-[#0c1210] to-paper-200",
  inkClass: "text-ink",
  subInkClass: "text-ink-500",
  nodeAccent: "#34d399", // emerald-400
  pathColor: "#71717a", // zinc-500 — recessive but visible on the dark stage
  pathDoneColor: "#34d399",
  coinColor: COIN_WHITE,
  roster: [
    { glyph: "🦜", name: "Pirate deckhand" },
    { glyph: "⚔️", name: "Pirate swordsman" },
    { glyph: "🏴‍☠️", name: "Pirate captain" },
    { glyph: "💀", name: "Skeleton king" },
    { glyph: "🐙", name: "Sea monster" },
    { glyph: "⛵", name: "Rival pirate fleet" },
  ],
  chestGlyph: "💰",
  rewardLabel: "Treasure chest",
};

// Phase 2 — Foundation. Sky: the app's "good/structural" score tone, on slate-dark.
const DUNGEON: QuestTheme = {
  key: "week_2_4",
  name: "Dungeon Expedition",
  surfaceClass: "bg-gradient-to-b from-[#0d1520] via-[#0a0f16] to-paper-200",
  inkClass: "text-ink",
  subInkClass: "text-ink-500",
  nodeAccent: "#38bdf8", // sky-400
  pathColor: "#71717a",
  pathDoneColor: "#38bdf8",
  coinColor: COIN_WHITE,
  roster: [
    { glyph: "👺", name: "Goblin scout" },
    { glyph: "💀", name: "Skeleton warrior" },
    { glyph: "🗿", name: "Dungeon golem" },
    { glyph: "🧙", name: "Dark mage" },
    { glyph: "🛡️", name: "Possessed knight" },
    { glyph: "🐉", name: "Dragon" },
  ],
  chestGlyph: "💎",
  rewardLabel: "Vault",
};

// Phase 3 — Growth & Scale. Cyan on deep space — already the app's night-sky register.
const SPACE: QuestTheme = {
  key: "later",
  name: "Deep-Space Expedition",
  surfaceClass: "bg-gradient-to-b from-[#0a1018] via-[#080c14] to-paper-200",
  inkClass: "text-ink",
  subInkClass: "text-ink-500",
  nodeAccent: "#22d3ee", // cyan-400
  pathColor: "#71717a",
  pathDoneColor: "#22d3ee",
  coinColor: COIN_WHITE,
  roster: [
    { glyph: "🤖", name: "Scout drone" },
    { glyph: "👾", name: "Space pirate raider" },
    { glyph: "🦾", name: "Mech enforcer" },
    { glyph: "👽", name: "Alien creature" },
    { glyph: "🛰️", name: "Rogue AI ship" },
    { glyph: "🛸", name: "Enemy mothership" },
  ],
  chestGlyph: "📦",
  rewardLabel: "Cargo pod",
};

const THEME_BY_KEY: Record<string, QuestTheme> = {
  week_1: PIRATE,
  week_2_4: DUNGEON,
  later: SPACE,
};

const THEME_BY_INDEX = [PIRATE, DUNGEON, SPACE];

/** Resolve a phase's theme by key, falling back to position then pirate — so an unexpected
 *  phase key still renders a coherent themed stage rather than crashing. */
export function themeForPhase(key: string, index: number): QuestTheme {
  return THEME_BY_KEY[key] ?? THEME_BY_INDEX[index] ?? PIRATE;
}

/** The enemy (glyph + name) at a model-assigned roster slot, clamped to the theme's roster. */
export function enemyAt(theme: QuestTheme, slot: number): { glyph: string; name: string } {
  const i = Math.max(0, Math.min(theme.roster.length - 1, slot));
  return theme.roster[i];
}
