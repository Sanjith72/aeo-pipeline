"""Export the app's migration chain as a single Supabase-CLI-compatible baseline.

The app's source of truth for schema is src/aeo/storage/migrations/*.sql, applied by
``aeo migrate`` (tracked in schema_versions). Supabase users have two equivalent paths:

  1. Do nothing — the API container runs ``aeo migrate`` at boot (start-api), which
     applies the same files over the direct connection.
  2. Supabase-CLI-first — ``supabase db push`` the generated baseline, which also
     seeds schema_versions so a later ``aeo migrate`` is a no-op (no double-apply).

Run from the repo root whenever a new migration lands:

    python scripts/export_supabase_baseline.py

Regenerates supabase/migrations/<stamp>_aeo_baseline.sql deterministically (the stamp
is fixed, not wall-clock, so re-runs don't mint duplicate files).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src" / "aeo" / "storage" / "migrations"
OUT_DIR = REPO_ROOT / "supabase" / "migrations"
# Fixed stamp: Supabase CLI orders migrations by this prefix. One baseline file that is
# regenerated in place beats an ever-growing pile of near-identical baselines.
BASELINE_NAME = "20260101000000_aeo_baseline.sql"

BOOTSTRAP = """\
-- schema_versions bootstrap (mirrors src/aeo/storage/migrate.py) so the app's own
-- migration runner recognises everything below as already applied.
CREATE TABLE IF NOT EXISTS schema_versions (
    version     VARCHAR(20)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""


def discover() -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    for p in sorted(SOURCE_DIR.glob("*.sql")):
        version, _, name = p.stem.partition("_")
        if version.isdigit():
            out.append((version, name or p.stem, p))
    return out


def build() -> str:
    migrations = discover()
    if not migrations:
        raise SystemExit(f"no migrations found under {SOURCE_DIR}")

    parts: list[str] = [
        "-- AEO pipeline — Supabase baseline (GENERATED — do not edit by hand).",
        "-- Source of truth: src/aeo/storage/migrations/*.sql",
        "-- Regenerate with: python scripts/export_supabase_baseline.py",
        f"-- Includes migrations {migrations[0][0]}..{migrations[-1][0]}.",
        "",
        BOOTSTRAP,
    ]
    for version, name, path in migrations:
        parts.append(f"\n-- ═══ {version}_{name} ══════════════════════════════════════════════")
        parts.append(path.read_text(encoding="utf-8").strip())
        parts.append(
            "INSERT INTO schema_versions (version, name) "
            f"VALUES ('{version}', '{name}') ON CONFLICT (version) DO NOTHING;"
        )
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / BASELINE_NAME
    out_path.write_text(build(), encoding="utf-8", newline="\n")
    count = len(discover())
    print(f"wrote {out_path.relative_to(REPO_ROOT)} ({count} migrations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
