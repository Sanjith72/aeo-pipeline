// web/lib/quest/theme.ts
// Data-driven skins for the Quest Map. Each phase key maps to one complete theme — its
// stage surface, palette, ordered enemy roster (weakest → named final boss) and reward.
// Swapping a phase's theme (e.g. Phase 3) is a config change here; no component edits.

export interface QuestTheme {
  key: string;
  name: string; // "Pirate Treasure Map"
  /** Tailwind classes for the expanded themed stage background (a deliberate visual island
   *  inside the otherwise-dark app shell). */
  surfaceClass: string;
  inkClass: string; // base text on the stage
  subInkClass: string; // muted text on the stage
  nodeAccent: string; // hex — active node ring / glow
  pathColor: string; // hex — not-yet-cleared trail
  pathDoneColor: string; // hex — cleared trail
  coinColor: string; // hex — coins are blue across every theme (one currency)
  /** Ordered difficulty scale, weakest first, named final boss last. The map compresses or
   *  repeats this to fit however many tasks a phase actually has; the last task always
   *  renders as `roster[last]`. */
  roster: { glyph: string; name: string }[];
  chestGlyph: string;
  rewardLabel: string; // "Treasure chest" | "Vault" | "Cargo pod"
}

const COIN_BLUE = "#ffffff"; // matches the app accent (white) — coins read as one currency

const PIRATE: QuestTheme = {
  key: "week_1",
  name: "Pirate Treasure Map",
  surfaceClass: "bg-gradient-to-b from-amber-100 via-amber-50 to-amber-200/80",
  inkClass: "text-stone-800",
  subInkClass: "text-stone-600",
  nodeAccent: "#b45309", // amber-700
  pathColor: "#a8a29e", // stone-400
  pathDoneColor: "#b45309",
  coinColor: COIN_BLUE,
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

const DUNGEON: QuestTheme = {
  key: "week_2_4",
  name: "Dungeon Expedition",
  surfaceClass: "bg-gradient-to-b from-slate-900 via-indigo-950 to-slate-950",
  inkClass: "text-slate-100",
  subInkClass: "text-slate-400",
  nodeAccent: "#f59e0b", // amber-500 torchlight
  pathColor: "#475569", // slate-600
  pathDoneColor: "#f59e0b",
  coinColor: COIN_BLUE,
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

// Phase 3 theme is provisional per the spec — swappable here without touching components.
const SPACE: QuestTheme = {
  key: "later",
  name: "Deep-Space Expedition",
  surfaceClass: "bg-gradient-to-b from-[#070b18] via-[#0a1030] to-[#05070f]",
  inkClass: "text-cyan-50",
  subInkClass: "text-slate-400",
  nodeAccent: "#22d3ee", // cyan-400
  pathColor: "#334155", // slate-700
  pathDoneColor: "#22d3ee",
  coinColor: COIN_BLUE,
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
