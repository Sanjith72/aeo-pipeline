#!/usr/bin/env bash
# Standalone milestone-verification runner. The weekly `aeo audit-cycle` already verifies
# milestones at the end of its loop; use THIS lighter job to re-check whether a client has
# implemented their plan more often than you run a full audit (it only re-scrapes + matches
# — no scoring, no LLM). Loads the env file, activates the venv, runs the verify, and exits
# non-zero on failure so the scheduler surfaces it.
#
# Usage: weekly_verify.sh <domain> <target>   (defaults: securin.io Securin)
set -euo pipefail

APP_DIR="${AEO_APP_DIR:-/opt/aeo}"
ENV_FILE="${AEO_ENV_FILE:-/etc/aeo/audit.env}"
DOMAIN="${1:-${AEO_DOMAIN:-securin.io}}"
TARGET="${2:-${AEO_TARGET:-Securin}}"

cd "$APP_DIR"
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
fi

# shellcheck disable=SC1091
source "$APP_DIR/.venv/bin/activate"

echo "[$(date -Is)] aeo verify-milestones ${DOMAIN} --target ${TARGET}"
exec aeo verify-milestones "$DOMAIN" --target "$TARGET"
