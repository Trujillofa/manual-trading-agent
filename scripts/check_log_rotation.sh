#!/usr/bin/env bash
# Check managed log sizes and send Telegram alerts on threshold escalation.
#
# Usage (on Hetzner host):
#   ./scripts/check_log_rotation.sh
#
# Cron example (every 6 hours):
#   0 */6 * * * cd /home/emilio/manual-trading-agent && ./scripts/check_log_rotation.sh >> logs/log_monitor.log 2>&1

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-manual-trading-agent}"

if docker inspect "$SERVICE_NAME" >/dev/null 2>&1; then
  exec docker exec "$SERVICE_NAME" python -m src.cli logs-status --notify
fi

echo "[log-monitor] container '$SERVICE_NAME' not running" >&2
exit 1