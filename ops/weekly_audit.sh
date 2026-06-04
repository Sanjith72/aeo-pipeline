#!/usr/bin/env bash
# Weekly AEO audit-cycle runner (v4). Wrapper used by the systemd service / cron:
# loads the env file, activates the venv, runs the cycle, and exits non-zero on
# failure so the scheduler surfaces it.
#
# Usage: weekly_audit.sh <domain> <target>   (defaults: securin.io Securin)
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

echo "[$(date -Is)] aeo audit-cycle ${DOMAIN} --target ${TARGET}"
exec aeo audit-cycle "$DOMAIN" --target "$TARGET"
