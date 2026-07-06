#!/bin/sh
# Production scanner loop: 15-minute scans, optional Telegram polling, bounded log rotation.
#
# Rotates only scan.log and signal_audit.jsonl. Never touches .env, telegram.log,
# active_signal_state.json, near_setup_state.json, news_cache.json, or results/.
#
# Environment (all optional):
#   LOG_DIR                  default /app/logs
#   ROTATE_THRESHOLD_BYTES   default 52428800 (50 MiB)
#   RETAIN_BYTES             default 26214400 (25 MiB)
#   SCAN_INTERVAL_SECONDS    default 900 (15 minutes)
#   TELEGRAM_POLL_ENABLED    default true
#   ROTATE_ONCE              default false — test-only one-shot rotation then exit 0

set -eu

LOG_DIR="${LOG_DIR:-/app/logs}"
ROTATE_THRESHOLD_BYTES="${ROTATE_THRESHOLD_BYTES:-52428800}"
RETAIN_BYTES="${RETAIN_BYTES:-26214400}"
SCAN_INTERVAL_SECONDS="${SCAN_INTERVAL_SECONDS:-900}"
TELEGRAM_POLL_ENABLED="${TELEGRAM_POLL_ENABLED:-true}"
ROTATE_ONCE="${ROTATE_ONCE:-false}"

TELEGRAM_PID=""

cleanup() {
  if [ -n "$TELEGRAM_PID" ] && kill -0 "$TELEGRAM_PID" 2>/dev/null; then
    kill -TERM "$TELEGRAM_PID" 2>/dev/null || true
    wait "$TELEGRAM_PID" 2>/dev/null || true
  fi
  exit 0
}

trap cleanup TERM INT

rotate_file() {
  filepath="$1"
  [ -f "$filepath" ] && [ ! -L "$filepath" ] || return 0

  size=$(wc -c <"$filepath" | tr -d ' ')
  [ "$size" -ge "$ROTATE_THRESHOLD_BYTES" ] || return 0

  tmp="${filepath}.rotate.$$"
  tail -c "$RETAIN_BYTES" "$filepath" >"$tmp"
  mv -f "$tmp" "$filepath"
}

rotate_managed_logs() {
  rotate_file "$LOG_DIR/scan.log"
  rotate_file "$LOG_DIR/signal_audit.jsonl"
}

mkdir -p "$LOG_DIR"

rotate_managed_logs

if [ "$ROTATE_ONCE" = "true" ]; then
  exit 0
fi

if [ "$TELEGRAM_POLL_ENABLED" = "true" ]; then
  python -u -m src.cli telegram-poll >>"$LOG_DIR/telegram.log" 2>&1 &
  TELEGRAM_PID=$!
fi

while true; do
  rotate_managed_logs
  echo "=== $(date) ===" >>"$LOG_DIR/scan.log"
  python -u -m src.cli scan >>"$LOG_DIR/scan.log" 2>&1
  sleep "$SCAN_INTERVAL_SECONDS"
done