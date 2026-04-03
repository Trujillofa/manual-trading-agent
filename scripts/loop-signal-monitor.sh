#!/bin/bash
# Monitor signal audit log for new signals
# Usage: ./scripts/loop-signal-monitor.sh
# Designed for: claude /loop 5m "./scripts/loop-signal-monitor.sh"

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

SIGNAL_LOG="logs/signal_audit.jsonl"
STATE_FILE="/tmp/manual-trading-signal-monitor-state"

# Get last known line count
LAST_COUNT=0
if [ -f "$STATE_FILE" ]; then
  LAST_COUNT=$(cat "$STATE_FILE")
fi

# Count current lines
if [ -f "$SIGNAL_LOG" ]; then
  CURRENT_COUNT=$(wc -l < "$SIGNAL_LOG")

  if [ "$CURRENT_COUNT" -gt "$LAST_COUNT" ]; then
    NEW_SIGNALS=$((CURRENT_COUNT - LAST_COUNT))
    echo "📊 ${NEW_SIGNALS} new signal(s) detected since last check:"
    tail -${NEW_SIGNALS} "$SIGNAL_LOG" | jq -r '"  [\(.timestamp)] \(.pair) \(.direction) — RSI: \(.rsi_values)"' 2>/dev/null || tail -${NEW_SIGNALS} "$SIGNAL_LOG"
  else
    echo "📊 No new signals. Total: ${CURRENT_COUNT}"
  fi

  echo "$CURRENT_COUNT" > "$STATE_FILE"
else
  echo "📊 No signal log found yet"
fi

# Also show recent cooldown state
if [ -f "logs/cooldown_state.json" ]; then
  echo ""
  echo "⏳ Active cooldowns:"
  jq -r 'to_entries[] | select(.value > (now | todate)) | "  \(.key): cooldown until \(.value)"' logs/cooldown_state.json 2>/dev/null || echo "  (none)"
fi
