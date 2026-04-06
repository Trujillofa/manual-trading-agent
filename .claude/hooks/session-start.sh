#!/bin/bash
# Read JSON input from stdin
INPUT=$(cat)
SESSION_TYPE=$(echo "$INPUT" | jq -r '.matcher // "startup"')

# Inject dynamic context that Claude sees
echo "## Session Context (auto-loaded)"
echo "- Project: manual-trading-agent (Forex RSI MTF Scanner)"
echo "- Paper mode: ALWAYS (safety)"
echo "- Key files: config/settings.yaml, src/strategy/multi_timeframe.py, src/cli.py"
echo ""
echo "### Recent Signal Activity"
# Show last 5 signals from audit log if exists
if [ -f "$CLAUDE_PROJECT_DIR/logs/signal_audit.jsonl" ]; then
  tail -5 "$CLAUDE_PROJECT_DIR/logs/signal_audit.jsonl" 2>/dev/null | jq -r '"- \(.timestamp // "unknown"): \(.pair // "?") \(.direction // "?") RSI=\(.rsi_values // "N/A")"' 2>/dev/null || echo "(Could not parse signal log)"
else
  echo "(No signal audit log found)"
fi
echo ""
echo "### Current Config Snapshot"
if [ -f "$CLAUDE_PROJECT_DIR/config/settings.yaml" ]; then
  echo "Mode: $(grep 'mode:' "$CLAUDE_PROJECT_DIR/config/settings.yaml" | head -1)"
  echo "RSI thresholds: $(grep 'rsi_overbought:\|rsi_oversold:' "$CLAUDE_PROJECT_DIR/config/settings.yaml")"
  echo "TP/SL: $(grep 'tp_usd:\|sl_usd:' "$CLAUDE_PROJECT_DIR/config/settings.yaml")"
fi
